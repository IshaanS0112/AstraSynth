"""Theta*: any-angle path planning over the shared cost model.

The problem it solves
---------------------
A* on an 8-connected grid can only produce headings that are multiples of 45
degrees. Asked to cross open ground at 20 degrees it returns a staircase: the
right *length* to three decimal places, and a route no rover would drive,
because every step of the staircase is a real turn that costs real time and real
localisation error. The grid is an artefact of the discretisation, and A* has no
way to see past it.

Theta* is A* with one change: when relaxing a neighbour ``s'`` of ``s``, it first
asks whether ``s'`` is visible from ``s``'s *parent*. If it is, ``s'`` is
attached to that grandparent directly and the intermediate cell is skipped. Paths
therefore consist of straight segments between corners rather than grid steps,
and the corners land where the terrain actually requires a turn.

What it costs
-------------
**Theta\\* is not optimal, and this module does not claim it is.** Two reasons,
both real here:

1. Theta* itself is only an approximation of the true any-angle optimum even on
   a uniform grid - it considers a shortcut only to the immediate parent, not to
   every ancestor.
2. Segment costs come from :meth:`PlanningGrid.segment`, a sampled line integral.
   A sampled integral is not identical to the sum of the grid steps beneath it,
   so a Theta* cost and an A* cost are not two measurements of the same
   quantity and must not be compared as though they were.

What *can* be compared, and what the test suite checks, are the two properties
Theta* is actually for: Euclidean route length, and how many times the route
changes heading. On open terrain it should beat A* on both. Where it should not
be used is anywhere the guarantee matters more than the shape - which is why A*
remains the default planner and the baseline every benchmark measures against.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import numpy as np

from app.services.planning.grid import Cell, EdgeBlock, PlanningGrid


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
    """Theta* from ``start`` to ``goal``.

    Returns the corner sequence, not the cells in between: consecutive entries
    are the endpoints of straight segments and are generally not adjacent.
    """
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
            # because the shortcut may be blocked or simply worse.
            step_cost, reason = grid.evaluate_edge(current, neighbour)
            if step_cost is None:
                if reason is EdgeBlock.SLOPE:
                    blocked_by_slope += 1
                elif reason is EdgeBlock.LETHAL_HAZARD:
                    blocked_by_hazard += 1
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
