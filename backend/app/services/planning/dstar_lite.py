"""D* Lite: incremental replanning when the belief map changes mid-traverse."""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

from app.services.planning.grid import NEIGHBOUR_OFFSETS, Cell, PlanningGrid

_INF = math.inf
Key = tuple[float, float]

# Predecessors of u are its neighbours, since NEIGHBOUR_OFFSETS holds every
# offset with its negation - but the edge between them is directional, and this
# table maps one direction to the other without redoing geometry per lookup.
_OPPOSITE = tuple(NEIGHBOUR_OFFSETS.index((-dr, -dc)) for dr, dc in NEIGHBOUR_OFFSETS)


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

    __slots__ = (
        "_cols",
        "_counter",
        "_g",
        "_goal",
        "_graph",
        "_heuristic_from_start",
        "_k_m",
        "_last_start",
        "_queue",
        "_queue_keys",
        "_rhs",
        "_start",
        "goal",
        "grid",
        "stats",
    )

    def __init__(self, grid: PlanningGrid, start: Cell, goal: Cell) -> None:
        if not grid.in_bounds(start) or not grid.in_bounds(goal):
            raise ValueError("start and goal must be inside the planning grid")
        self.grid = grid
        self.goal = goal

        # Flat int cells and list-backed tables: a dict keyed on fresh tuples
        # spends most of its time hashing, not searching.
        self._graph = grid.compiled()
        self._cols = self._graph.cols
        self._start = start[0] * self._cols + start[1]
        self._goal = goal[0] * self._cols + goal[1]

        size = self._graph.size
        self._g: list[float] = [_INF] * size
        self._rhs: list[float] = [_INF] * size
        self._queue: list[tuple[Key, int, int]] = []
        self._queue_keys: dict[int, Key] = {}
        self._counter = 0
        self._k_m = 0.0
        self._last_start = self._start
        self.stats = DStarLiteStats()

        self._heuristic_from_start = self._graph.heuristics_to(self._start)

        self._rhs[self._goal] = 0.0
        self._push(self._goal, self._key(self._goal))
        self.stats.initial_expansions = self._compute_shortest_path()

    # --- frame conversion -----------------------------------------------------

    @property
    def start(self) -> Cell:
        """The rover's current cell, in ``(row, col)`` terms for callers."""
        return divmod(self._start, self._cols)

    # --- queue --------------------------------------------------------------

    def _push(self, cell: int, key: Key) -> None:
        self._counter += 1
        self._queue_keys[cell] = key
        heapq.heappush(self._queue, (key, self._counter, cell))

    def _remove(self, cell: int) -> None:
        self._queue_keys.pop(cell, None)

    def _top(self) -> tuple[Key, int] | None:
        while self._queue:
            key, _, cell = self._queue[0]
            authoritative = self._queue_keys.get(cell)
            if authoritative is None or authoritative != key:
                heapq.heappop(self._queue)  # stale entry
                continue
            return key, cell
        return None

    # --- D* Lite core -------------------------------------------------------

    def g(self, cell: Cell | int) -> float:
        index = cell if isinstance(cell, int) else cell[0] * self._cols + cell[1]
        return self._g[index]

    def rhs(self, cell: Cell | int) -> float:
        index = cell if isinstance(cell, int) else cell[0] * self._cols + cell[1]
        return self._rhs[index]

    def _key(self, cell: int) -> Key:
        g = self._g[cell]
        rhs = self._rhs[cell]
        best = g if g < rhs else rhs
        return (best + self._heuristic_from_start[cell] + self._k_m, best)

    def _update_vertex(self, cell: int) -> None:
        if cell != self._goal:
            best = _INF
            neighbour = self._graph.neighbour
            edge_cost = self._graph.cost
            g = self._g
            for k in range(8):
                successor = neighbour[k][cell]
                if successor < 0:
                    continue
                cost = edge_cost[k][cell]
                if cost == _INF:
                    continue
                candidate = cost + g[successor]
                if candidate < best:
                    best = candidate
            self._rhs[cell] = best

        self._remove(cell)
        if self._g[cell] != self._rhs[cell]:
            self._push(cell, self._key(cell))

    def _compute_shortest_path(self) -> int:
        expansions = 0
        while True:
            top = self._top()
            if top is None:
                break
            key, cell = top
            start = self._start
            if not (key < self._key(start) or self._rhs[start] != self._g[start]):
                break

            heapq.heappop(self._queue)
            self._remove(cell)
            expansions += 1

            new_key = self._key(cell)
            if key < new_key:
                # The key was computed under an older k_m; reinsert, do not expand.
                self._push(cell, new_key)
            elif self._g[cell] > self._rhs[cell]:
                # Overconsistent: the cheap case, just accept the better value.
                self._g[cell] = self._rhs[cell]
                for predecessor in self._predecessors(cell):
                    self._update_vertex(predecessor)
            else:
                # Underconsistent: a path got worse. Invalidate and let the
                # predecessors, and this vertex, find another way.
                self._g[cell] = _INF
                self._update_vertex(cell)
                for predecessor in self._predecessors(cell):
                    self._update_vertex(predecessor)
        return expansions

    def _predecessors(self, cell: int):
        neighbour = self._graph.neighbour
        for k in _OPPOSITE:
            candidate = neighbour[k][cell]
            if candidate >= 0:
                yield candidate

    # --- driving ------------------------------------------------------------

    @property
    def reachable(self) -> bool:
        return self._rhs[self._start] < _INF

    def cost_to_goal(self) -> float:
        return min(self._g[self._start], self._rhs[self._start])

    def _best_successor(self, cell: int) -> tuple[int, float]:
        neighbour = self._graph.neighbour
        edge_cost = self._graph.cost
        g = self._g
        best_cell, best_value = -1, _INF
        for k in range(8):
            successor = neighbour[k][cell]
            if successor < 0:
                continue
            cost = edge_cost[k][cell]
            if cost == _INF:
                continue
            value = cost + g[successor]
            if value < best_value:
                best_value = value
                best_cell = successor
        return best_cell, best_value

    def next_step(self) -> Cell | None:
        """The successor of ``start`` on the current best route, or ``None``."""
        if self._start == self._goal:
            return None
        best_cell, best_value = self._best_successor(self._start)
        return divmod(best_cell, self._cols) if best_value < _INF else None

    def extract_path(self, max_steps: int | None = None) -> list[Cell]:
        """The full route from ``start`` to ``goal`` under the current beliefs."""
        if not self.reachable:
            return []
        limit = max_steps if max_steps is not None else self._graph.size
        current = self._start
        indices = [current]
        seen = {current}
        for _ in range(limit):
            if current == self._goal:
                return [divmod(i, self._cols) for i in indices]
            best_cell, best_value = self._best_successor(current)
            if best_cell < 0 or best_value == _INF or best_cell in seen:
                return []
            seen.add(best_cell)
            indices.append(best_cell)
            current = best_cell
        return []

    def move_to(self, cell: Cell) -> None:
        """Tell the planner the rover has driven to ``cell``."""
        index = cell[0] * self._cols + cell[1]
        if index == self._start:
            return
        last_row, last_col = divmod(self._last_start, self._cols)
        self._k_m += self.grid.heuristic((last_row, last_col), cell)
        self._last_start = index
        self._start = index
        # The heuristic is measured from the rover, so moving it re-bases the
        # whole table; one vectorised pass beats a hypot per key.
        self._heuristic_from_start = self._graph.heuristics_to(index)

    def apply_updates(self, updates: dict[Cell, float]) -> RepairRecord | None:
        """Write sensed hazard values into the grid and repair the search tree."""
        changed = self.grid.apply_hazard_update(updates)
        if not changed:
            return None

        for cell in changed:
            # c(u, v) charges v's hazard, so changing it changes every edge INTO
            # v: the predecessors need re-evaluating, and v itself for consistency.
            index = cell[0] * self._cols + cell[1]
            self._update_vertex(index)
            for predecessor in self._predecessors(index):
                self._update_vertex(predecessor)

        expansions = self._compute_shortest_path()
        record = RepairRecord(
            trigger_cells=len(changed),
            vertices_expanded=expansions,
            queue_size_after=len(self._queue_keys),
        )
        self.stats.repairs.append(record)
        return record
