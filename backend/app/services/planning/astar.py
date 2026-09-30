"""A* and Dijkstra over a :class:`PlanningGrid`."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from math import inf

from app.services.planning.grid import Cell, PlanningGrid


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
    """Best-first search from ``start`` to ``goal``."""
    graph = grid.compiled()
    cols = graph.cols
    start_index = start[0] * cols + start[1]
    goal_index = goal[0] * cols + goal[1]

    neighbour = graph.neighbour
    edge_cost = graph.cost
    hazard_blocked = graph.blocked_hazard
    slope_blocked = graph.blocked_slope

    heuristic = graph.heuristics_to(goal_index) if use_heuristic else None

    g_score = [inf] * graph.size
    g_score[start_index] = 0.0
    came_from = [-1] * graph.size
    closed = bytearray(graph.size)

    blocked_by_slope = 0
    blocked_by_hazard = 0
    nodes_expanded = 0

    counter = 0  # FIFO tie-break; keeps the heap ordering deterministic
    open_heap: list[tuple[float, int, int]] = [
        (heuristic[start_index] if heuristic else 0.0, counter, start_index)
    ]

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if closed[current]:
            continue
        closed[current] = 1
        nodes_expanded += 1

        if current == goal_index:
            cells: list[Cell] = []
            cursor = current
            while cursor != -1:
                cells.append(divmod(cursor, cols))
                cursor = came_from[cursor]
            cells.reverse()
            return SearchResult(
                cells=cells,
                total_cost=g_score[goal_index],
                nodes_expanded=nodes_expanded,
                blocked={
                    "moves_blocked_by_slope_limit": blocked_by_slope,
                    "moves_blocked_by_lethal_hazard": blocked_by_hazard,
                },
            )

        current_g = g_score[current]
        for k in range(8):
            destination = neighbour[k][current]
            if destination < 0 or closed[destination]:
                continue
            cost = edge_cost[k][current]
            if cost == inf:
                # Attribution matches evaluate_edge: hazard is tested first, so
                # an edge failing both reasons counts as a hazard rejection.
                if hazard_blocked[k][current]:
                    blocked_by_hazard += 1
                elif slope_blocked[k][current]:
                    blocked_by_slope += 1
                continue
            tentative = current_g + cost
            if tentative < g_score[destination]:
                g_score[destination] = tentative
                came_from[destination] = current
                counter += 1
                heapq.heappush(
                    open_heap,
                    (
                        tentative + heuristic[destination] if heuristic else tentative,
                        counter,
                        destination,
                    ),
                )

    return SearchResult(
        cells=[],
        total_cost=inf,
        nodes_expanded=nodes_expanded,
        blocked={
            "moves_blocked_by_slope_limit": blocked_by_slope,
            "moves_blocked_by_lethal_hazard": blocked_by_hazard,
        },
        found=False,
    )
