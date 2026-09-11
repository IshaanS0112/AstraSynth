"""A* and Dijkstra over a :class:`PlanningGrid`.

The search itself is unchanged from V1 - same 8-connectivity, same tie-break,
same admissible heuristic. What changed is that the cost function moved out into
``grid.py``, so this module now contains only the search strategy and every
other planner in the package is searching a provably identical graph.

Why A* and not Dijkstra: identical optimality guarantee under an admissible
heuristic, but Dijkstra expands uniformly in every direction while A* biases
expansion toward the goal. ``nodes_expanded`` is recorded on every plan so the
difference is measurable rather than asserted.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np

from app.services.planning.grid import Cell, EdgeBlock, PlanningGrid


@dataclass(slots=True)
class SearchResult:
    cells: list[Cell]
    total_cost: float
    nodes_expanded: int
    blocked: dict[str, int] = field(default_factory=dict)
    found: bool = True


def search(
    grid: PlanningGrid,
    start: Cell,
    goal: Cell,
    use_heuristic: bool = True,
) -> SearchResult:
    """Best-first search from ``start`` to ``goal``.

    ``use_heuristic=False`` zeroes the heuristic, which reduces the identical
    search to Dijkstra - used by the benchmark to compare node expansions and by
    the test suite to confirm both agree on the optimal cost, which is the
    empirical check that the heuristic really is admissible.
    """
    rows, cols = grid.shape
    blocked_by_slope = 0
    blocked_by_hazard = 0

    def h(cell: Cell) -> float:
        return grid.heuristic(cell, goal) if use_heuristic else 0.0

    g_score = np.full((rows, cols), np.inf, dtype=np.float64)
    g_score[start] = 0.0
    came_from: dict[Cell, Cell] = {}
    closed = np.zeros((rows, cols), dtype=bool)

    counter = 0  # FIFO tie-break; keeps the heap ordering deterministic
    open_heap: list[tuple[float, int, Cell]] = [(h(start), counter, start)]
    nodes_expanded = 0

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if closed[current]:
            continue
        closed[current] = True
        nodes_expanded += 1

        if current == goal:
            cells = [goal]
            while cells[-1] != start:
                cells.append(came_from[cells[-1]])
            cells.reverse()
            return SearchResult(
                cells=cells,
                total_cost=float(g_score[goal]),
                nodes_expanded=nodes_expanded,
                blocked={
                    "moves_blocked_by_slope_limit": blocked_by_slope,
                    "moves_blocked_by_lethal_hazard": blocked_by_hazard,
                },
            )

        for neighbour in grid.neighbours(current):
            if closed[neighbour]:
                continue
            cost, reason = grid.evaluate_edge(current, neighbour)
            if cost is None:
                if reason is EdgeBlock.SLOPE:
                    blocked_by_slope += 1
                elif reason is EdgeBlock.LETHAL_HAZARD:
                    blocked_by_hazard += 1
                continue
            tentative = g_score[current] + cost
            if tentative < g_score[neighbour]:
                g_score[neighbour] = tentative
                came_from[neighbour] = current
                counter += 1
                heapq.heappush(open_heap, (tentative + h(neighbour), counter, neighbour))

    return SearchResult(
        cells=[],
        total_cost=float("inf"),
        nodes_expanded=nodes_expanded,
        blocked={
            "moves_blocked_by_slope_limit": blocked_by_slope,
            "moves_blocked_by_lethal_hazard": blocked_by_hazard,
        },
        found=False,
    )
