"""D* Lite: incremental replanning when the belief map changes mid-traverse.

The situation this exists for
-----------------------------
A rover plans from an orbital map. The orbital map is coarse. Two hundred metres
along the route a hazard camera resolves a boulder field the orbital map showed
as open ground. The route is now wrong, and the rover is standing in the middle
of it.

Replanning with A* from scratch works and throws away everything: the previous
search proved facts about the parts of the map that did *not* change, and almost
all of the map did not change. D* Lite (Koenig & Likhachev, 2002) keeps that
work. It searches backwards from the goal, so g-values are costs *to* the goal
and stay valid as the rover moves; when edge costs change it repairs only the
part of the tree those edges could have affected.

The two claims and how they are checked
---------------------------------------
1. **It is correct** - the route it returns after a repair is the same route a
   fresh A* would return on the updated map, at the same cost. ``test_dstar_lite``
   asserts this on random terrain with random obstacle reveals, not on a
   hand-picked case.
2. **It is cheaper** - a repair expands fewer vertices than a from-scratch
   search. ``vertices_expanded`` is recorded per repair so the claim is a
   measurement.

Without (1), (2) is worthless, which is why the equality test is the important
one.

Implementation notes
--------------------
* Priority queue keys are ``[min(g, rhs) + h(s_start, s) + k_m, min(g, rhs)]``.
  ``k_m`` accumulates the heuristic shift as the rover moves, which is what lets
  the queue keep its ordering without being rebuilt on every step.
* The queue uses lazy deletion - a stale entry is recognised on pop by comparing
  against the authoritative key in ``_queue_keys`` - because ``heapq`` has no
  decrease-key.
* Edge costs here are **directed**: ``c(u, v)`` charges the hazard of ``v``, so
  ``c(u, v) != c(v, u)``. Successors and predecessors are the same eight cells
  geometrically but not the same edges, and the update rule below is written for
  the directed case. Getting this wrong produces a planner that is right on
  symmetric terrain and quietly wrong everywhere else.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from app.services.planning.grid import Cell, PlanningGrid

_INF = math.inf
Key = tuple[float, float]


@dataclass(slots=True)
class RepairRecord:
    """One incremental repair, for the mission event log."""

    trigger_cells: int
    vertices_expanded: int
    queue_size_after: int


@dataclass(slots=True)
class DStarLiteStats:
    initial_expansions: int = 0
    repairs: list[RepairRecord] = field(default_factory=list)

    @property
    def replan_count(self) -> int:
        return len(self.repairs)

    @property
    def repair_expansions(self) -> int:
        return sum(r.vertices_expanded for r in self.repairs)

    def as_dict(self) -> dict:
        return {
            "initial_expansions": self.initial_expansions,
            "replan_count": self.replan_count,
            "repair_expansions_total": self.repair_expansions,
            "repair_expansions_per_replan": [r.vertices_expanded for r in self.repairs],
        }


class DStarLite:
    """Incremental shortest path from a moving start to a fixed goal."""

    def __init__(self, grid: PlanningGrid, start: Cell, goal: Cell) -> None:
        if not grid.in_bounds(start) or not grid.in_bounds(goal):
            raise ValueError("start and goal must be inside the planning grid")
        self.grid = grid
        self.start = start
        self.goal = goal

        self._g: dict[Cell, float] = {}
        self._rhs: dict[Cell, float] = {}
        self._queue: list[tuple[Key, int, Cell]] = []
        self._queue_keys: dict[Cell, Key] = {}
        self._counter = 0
        self._k_m = 0.0
        self._last_start = start
        self.stats = DStarLiteStats()

        self._rhs[goal] = 0.0
        self._push(goal, self._key(goal))
        self.stats.initial_expansions = self._compute_shortest_path()

    # --- queue --------------------------------------------------------------

    def _push(self, cell: Cell, key: Key) -> None:
        self._counter += 1
        self._queue_keys[cell] = key
        heapq.heappush(self._queue, (key, self._counter, cell))

    def _remove(self, cell: Cell) -> None:
        self._queue_keys.pop(cell, None)

    def _top(self) -> tuple[Key, Cell] | None:
        while self._queue:
            key, _, cell = self._queue[0]
            authoritative = self._queue_keys.get(cell)
            if authoritative is None or authoritative != key:
                heapq.heappop(self._queue)  # stale entry
                continue
            return key, cell
        return None

    # --- D* Lite core -------------------------------------------------------

    def g(self, cell: Cell) -> float:
        return self._g.get(cell, _INF)

    def rhs(self, cell: Cell) -> float:
        return self._rhs.get(cell, _INF)

    def _key(self, cell: Cell) -> Key:
        best = min(self.g(cell), self.rhs(cell))
        return (best + self.grid.heuristic(self.start, cell) + self._k_m, best)

    def _update_vertex(self, cell: Cell) -> None:
        if cell != self.goal:
            best = _INF
            for successor in self.grid.neighbours(cell):
                cost = self.grid.edge_cost(cell, successor)
                if cost is None:
                    continue
                candidate = cost + self.g(successor)
                if candidate < best:
                    best = candidate
            self._rhs[cell] = best

        self._remove(cell)
        if self.g(cell) != self.rhs(cell):
            self._push(cell, self._key(cell))

    def _compute_shortest_path(self) -> int:
        expansions = 0
        while True:
            top = self._top()
            if top is None:
                break
            key, cell = top
            if not (key < self._key(self.start) or self.rhs(self.start) != self.g(self.start)):
                break

            heapq.heappop(self._queue)
            self._remove(cell)
            expansions += 1

            new_key = self._key(cell)
            if key < new_key:
                # The key was computed under an older k_m; reinsert, do not expand.
                self._push(cell, new_key)
            elif self.g(cell) > self.rhs(cell):
                # Overconsistent: the cheap case, just accept the better value.
                self._g[cell] = self.rhs(cell)
                for predecessor in self.grid.neighbours(cell):
                    self._update_vertex(predecessor)
            else:
                # Underconsistent: a path got *worse*. Invalidate and let the
                # predecessors - and this vertex - find out where else to go.
                self._g[cell] = _INF
                self._update_vertex(cell)
                for predecessor in self.grid.neighbours(cell):
                    self._update_vertex(predecessor)
        return expansions

    # --- driving ------------------------------------------------------------

    @property
    def reachable(self) -> bool:
        return self.rhs(self.start) < _INF

    def cost_to_goal(self) -> float:
        return min(self.g(self.start), self.rhs(self.start))

    def next_step(self) -> Cell | None:
        """The successor of ``start`` on the current best route, or ``None``.

        ``None`` means the goal is unreachable from where the rover is standing
        given what it currently believes.
        """
        if self.start == self.goal:
            return None
        best_cell: Cell | None = None
        best_value = _INF
        for successor in self.grid.neighbours(self.start):
            cost = self.grid.edge_cost(self.start, successor)
            if cost is None:
                continue
            value = cost + self.g(successor)
            if value < best_value:
                best_value = value
                best_cell = successor
        return best_cell if best_value < _INF else None

    def extract_path(self, max_steps: int | None = None) -> list[Cell]:
        """The full route from ``start`` to ``goal`` under the current beliefs.

        Walks greedily down the g-value field, which is the route the rover
        would drive if nothing else changed. Returns ``[]`` when the goal is
        unreachable. The step cap is a guard against a cycle from a numerically
        degenerate field, not an expected condition.
        """
        if not self.reachable:
            return []
        limit = max_steps if max_steps is not None else self.grid.rows * self.grid.cols
        cells = [self.start]
        current = self.start
        seen = {current}
        for _ in range(limit):
            if current == self.goal:
                return cells
            best_cell: Cell | None = None
            best_value = _INF
            for successor in self.grid.neighbours(current):
                cost = self.grid.edge_cost(current, successor)
                if cost is None:
                    continue
                value = cost + self.g(successor)
                if value < best_value:
                    best_value = value
                    best_cell = successor
            if best_cell is None or best_cell in seen:
                return []
            seen.add(best_cell)
            cells.append(best_cell)
            current = best_cell
        return []

    def move_to(self, cell: Cell) -> None:
        """Tell the planner the rover has driven to ``cell``.

        ``k_m`` absorbs the change in the heuristic's reference point so that
        keys already in the queue stay comparable with keys computed from here.
        This is the whole trick that makes D* Lite incremental across motion
        rather than only across map changes.
        """
        if cell == self.start:
            return
        self._k_m += self.grid.heuristic(self._last_start, cell)
        self._last_start = cell
        self.start = cell

    def apply_updates(self, updates: dict[Cell, float]) -> RepairRecord | None:
        """Write sensed hazard values into the grid and repair the search tree.

        Returns ``None`` when nothing actually changed - re-sensing known ground
        must not trigger a repair, or "incremental" would be a word rather than
        a property.
        """
        changed = self.grid.apply_hazard_update(updates)
        if not changed:
            return None

        for cell in changed:
            # c(u, v) charges the hazard of v, so changing v's hazard changes
            # every edge *into* v: its predecessors are what need re-evaluating.
            # v itself is updated too, because whether v can be left is
            # unaffected but its own consistency must still be restored.
            self._update_vertex(cell)
            for predecessor in self.grid.neighbours(cell):
                self._update_vertex(predecessor)

        expansions = self._compute_shortest_path()
        record = RepairRecord(
            trigger_cells=len(changed),
            vertices_expanded=expansions,
            queue_size_after=len(self._queue_keys),
        )
        self.stats.repairs.append(record)
        return record
