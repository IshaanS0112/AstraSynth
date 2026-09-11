"""Turning a sequence of cells into a reportable path.

Every planner in this package produces a list of grid cells. Everything
downstream - the risk engine, the report generator, the API, the UI - consumes
``PlannedPath``. This module is the single conversion between the two, so a
route from Theta* is measured with exactly the same ruler as a route from A*.

Two builders, because two kinds of path exist here:

* :func:`build_stepped_path` for 8-connected routes (A*, D* Lite, CBS), where
  consecutive cells are adjacent and each step is one grid move;
* :func:`build_anyangle_path` for Theta*, where consecutive waypoints are
  straight-line segments of arbitrary length and the per-segment figures come
  from integrating along the line.
"""

from __future__ import annotations

import math

from app.services.planning.grid import Cell, PlannedPath, PlanningGrid, Waypoint


def _waypoint(
    grid: PlanningGrid,
    index: int,
    cell: Cell,
    scale: float,
    hazard: float,
    slope_deg: float,
    step_m: float,
    step_energy: float,
    cumulative_energy: float,
) -> Waypoint:
    return Waypoint(
        segment_id=index,
        x=int(round(cell[1] * scale)),
        y=int(round(cell[0] * scale)),
        hazard_score=round(float(hazard), 4),
        slope_deg=round(slope_deg, 3),
        step_distance_m=round(step_m, 3),
        energy_cost_kwh=round(step_energy, 6),
        cumulative_energy_kwh=round(cumulative_energy, 6),
    )


def build_stepped_path(
    grid: PlanningGrid,
    cells: list[Cell],
    scale: float,
    metadata: dict,
    total_cost: float | None = None,
) -> PlannedPath:
    """Measure an 8-connected route cell by cell."""
    if not cells:
        raise ValueError("cannot build a path from an empty cell list")

    waypoints: list[Waypoint] = []
    total_distance = 0.0
    cumulative_energy = 0.0
    accumulated_cost = 0.0

    for index, cell in enumerate(cells):
        if index == 0:
            step_m = 0.0
            slope_deg = 0.0
            step_energy = 0.0
        else:
            previous = cells[index - 1]
            step_m = grid.distance_m(previous, cell)
            slope_deg = math.degrees(math.atan(grid.rise_over_run(previous, cell)))
            step_energy = grid.step_energy_kwh(previous, cell)
            edge_cost = grid.edge_cost(previous, cell)
            accumulated_cost += edge_cost if edge_cost is not None else 0.0

        total_distance += step_m
        cumulative_energy += step_energy
        waypoints.append(
            _waypoint(
                grid,
                index,
                cell,
                scale,
                grid.hazard[cell],
                slope_deg,
                step_m,
                step_energy,
                cumulative_energy,
            )
        )

    return PlannedPath(
        waypoints=waypoints,
        total_distance_m=round(total_distance, 3),
        total_energy_cost_kwh=round(cumulative_energy, 6),
        total_cost=round(total_cost if total_cost is not None else accumulated_cost, 4),
        metadata=metadata,
    )


def build_anyangle_path(
    grid: PlanningGrid,
    cells: list[Cell],
    scale: float,
    metadata: dict,
    total_cost: float | None = None,
) -> PlannedPath:
    """Measure a route whose waypoints are the corners of straight segments.

    ``hazard_score`` on a waypoint is the *mean* hazard along the segment that
    arrives at it, not the hazard of the corner cell. The risk engine averages
    waypoint hazards over the route, and a Theta* route has far fewer waypoints
    than the ground it covers - reporting only the corners would let a segment
    that ploughs through a hazardous field read as safe because its endpoints
    happen to be clean.
    """
    if not cells:
        raise ValueError("cannot build a path from an empty cell list")

    waypoints: list[Waypoint] = []
    total_distance = 0.0
    cumulative_energy = 0.0
    accumulated_cost = 0.0

    for index, cell in enumerate(cells):
        if index == 0:
            step_m = 0.0
            slope_deg = 0.0
            step_energy = 0.0
            hazard = float(grid.hazard[cell])
        else:
            previous = cells[index - 1]
            step_m = grid.distance_m(previous, cell)
            rise_m = float(grid.elevation[cell] - grid.elevation[previous])
            slope_deg = math.degrees(math.atan(rise_m / step_m)) if step_m else 0.0
            step_energy = grid.segment_energy_kwh(previous, cell)
            cost, _, hazard = grid.segment(previous, cell)
            accumulated_cost += cost if cost is not None else 0.0

        total_distance += step_m
        cumulative_energy += step_energy
        waypoints.append(
            _waypoint(
                grid, index, cell, scale, hazard, slope_deg, step_m, step_energy, cumulative_energy
            )
        )

    return PlannedPath(
        waypoints=waypoints,
        total_distance_m=round(total_distance, 3),
        total_energy_cost_kwh=round(cumulative_energy, 6),
        total_cost=round(total_cost if total_cost is not None else accumulated_cost, 4),
        metadata=metadata,
    )


def heading_changes(cells: list[Cell]) -> int:
    """How many times the route changes direction.

    The measurable form of "a Theta* route looks like something a rover would
    actually drive": an 8-connected route approximating a straight diagonal
    zig-zags, and this counts the zigs.
    """
    if len(cells) < 3:
        return 0
    changes = 0
    for i in range(1, len(cells) - 1):
        before = (cells[i][0] - cells[i - 1][0], cells[i][1] - cells[i - 1][1])
        after = (cells[i + 1][0] - cells[i][0], cells[i + 1][1] - cells[i][1])
        # Compare normalised directions so a long segment and a short one in the
        # same direction do not count as a turn.
        if _direction(before) != _direction(after):
            changes += 1
    return changes


def _direction(delta: tuple[int, int]) -> tuple[float, float]:
    length = math.hypot(*delta)
    if length == 0:
        return (0.0, 0.0)
    return (round(delta[0] / length, 6), round(delta[1] / length, 6))
