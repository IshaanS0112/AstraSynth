"""Theta*: any-angle path planning over the shared cost model."""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from math import inf

import numpy as np

from app.services.planning.grid import NEIGHBOUR_OFFSETS, Cell, PlanningGrid

# Offset -> its index in NEIGHBOUR_OFFSETS, so a neighbour cell maps back to the
# compiled graph's direction slot without a search.
_DIRECTION = {offset: index for index, offset in enumerate(NEIGHBOUR_OFFSETS)}


@dataclass(slots=True)
class ThetaResult:
    cells: list[Cell]
    total_cost: float
    nodes_expanded: int
    line_of_sight_checks: int
    shortcuts_taken: int
    blocked: dict[str, int] = field(default_factory=dict)
    found: bool = True


def search(grid: PlanningGrid, start: Cell, goal: Cell) -> ThetaResult:
    """Theta* from ``start`` to ``goal``."""
    graph = grid.compiled()
    rows, cols = grid.shape
    g_score = np.full((rows, cols), np.inf, dtype=np.float64)
    g_score[start] = 0.0
    parent: dict[Cell, Cell] = {start: start}
    closed = np.zeros((rows, cols), dtype=bool)

    blocked_by_slope = 0
    blocked_by_hazard = 0
    los_checks = 0
    shortcuts = 0
    nodes_expanded = 0

    counter = 0
    open_heap: list[tuple[float, int, Cell]] = [(grid.heuristic(start, goal), counter, start)]

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if closed[current]:
            continue
        closed[current] = True
        nodes_expanded += 1

        if current == goal:
            cells = [goal]
            while cells[-1] != start:
                cells.append(parent[cells[-1]])
            cells.reverse()
            return ThetaResult(
                cells=cells,
                total_cost=float(g_score[goal]),
                nodes_expanded=nodes_expanded,
                line_of_sight_checks=los_checks,
                shortcuts_taken=shortcuts,
                blocked={
                    "moves_blocked_by_slope_limit": blocked_by_slope,
                    "moves_blocked_by_lethal_hazard": blocked_by_hazard,
                },
            )

        grandparent = parent[current]

        for neighbour in grid.neighbours(current):
            if closed[neighbour]:
                continue

            # Path 2 (the Theta* case): can the grandparent see the neighbour
            # directly? If so the intermediate corner is unnecessary.
            improved = False
            if grandparent != current:
                los_checks += 1
                segment_cost, _, _ = grid.segment(grandparent, neighbour)
                if segment_cost is not None:
                    candidate = g_score[grandparent] + segment_cost
                    if candidate < g_score[neighbour]:
                        g_score[neighbour] = candidate
                        parent[neighbour] = grandparent
                        shortcuts += 1
                        improved = True

            # Path 1 (the A* case): the ordinary grid step. Always evaluated,
            # because the shortcut may be blocked or simply worse. Read from the
            # compiled graph, so the same edge is not re-derived per visit.
            source = current[0] * cols + current[1]
            direction = _DIRECTION[(neighbour[0] - current[0], neighbour[1] - current[1])]
            step_cost = graph.cost[direction][source]
            if step_cost == inf:
                if graph.blocked_hazard[direction][source]:
                    blocked_by_hazard += 1
                elif graph.blocked_slope[direction][source]:
                    blocked_by_slope += 1
            else:
                candidate = g_score[current] + step_cost
                if candidate < g_score[neighbour]:
                    g_score[neighbour] = candidate
                    parent[neighbour] = current
                    improved = True

            if improved:
                counter += 1
                heapq.heappush(
                    open_heap,
                    (g_score[neighbour] + grid.heuristic(neighbour, goal), counter, neighbour),
                )

    return ThetaResult(
        cells=[],
        total_cost=float("inf"),
        nodes_expanded=nodes_expanded,
        line_of_sight_checks=los_checks,
        shortcuts_taken=shortcuts,
        blocked={
            "moves_blocked_by_slope_limit": blocked_by_slope,
            "moves_blocked_by_lethal_hazard": blocked_by_hazard,
        },
        found=False,
    )
