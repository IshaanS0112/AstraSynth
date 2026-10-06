"""A* rover path planning with an energy-aware cost function.

The stable, V1-compatible entry point. The cost model lives in
:mod:`app.services.planning.grid` and the search loop in
:mod:`app.services.planning.astar`, so Theta*, D* Lite and CBS search a provably
identical graph. The same call returns the same route, cost and metadata as V1::

    cost(a, b) = distance_m(a, b) * (1 + hazard(b)) * energy_factor(slope(a, b))
    energy_factor(slope) = 1 + k * |rise / run|

``rise / run`` is ``tan(slope)`` from the elevation model, so the factor tracks
the gravitational work per metre. ``k`` is a modelling constant, not a measured
rover parameter, and is stored with every plan so the number is auditable.
"""

from __future__ import annotations

from app.services.planning.astar import search
from app.services.planning.grid import (
    PathNotFoundError,
    PlannedPath,
    PlanningGrid,
    RoverSpec,
    Waypoint,
    energy_factor,
)
from app.services.planning.outcome import PlanningOutcome, diagnose_search_failure
from app.services.planning.trace import build_stepped_path

__all__ = [
    "PathNotFoundError",
    "PlannedPath",
    "RoverSpec",
    "Waypoint",
    "energy_factor",
    "plan_path",
    "plan_path_dijkstra",
    "plan_path_outcome",
]


def plan_path(
    hazard_grid,
    elevation_grid,
    start: dict,
    goal: dict,
    rover: RoverSpec,
    meters_per_cell: float,
    scale: float = 1.0,
    slope_coefficient: float = 0.5,
    max_hazard: float = 1.0,
    use_heuristic: bool = True,
) -> PlannedPath:
    """Run A* from ``start`` to ``goal``."""
    outcome = plan_path_outcome(
        hazard_grid=hazard_grid,
        elevation_grid=elevation_grid,
        start=start,
        goal=goal,
        rover=rover,
        meters_per_cell=meters_per_cell,
        scale=scale,
        slope_coefficient=slope_coefficient,
        max_hazard=max_hazard,
        use_heuristic=use_heuristic,
    )
    if not outcome.succeeded:
        raise PathNotFoundError(outcome.reason)
    return outcome.path


def plan_path_outcome(
    hazard_grid,
    elevation_grid,
    start: dict,
    goal: dict,
    rover: RoverSpec,
    meters_per_cell: float,
    scale: float = 1.0,
    slope_coefficient: float = 0.5,
    max_hazard: float = 1.0,
    use_heuristic: bool = True,
):
    """A* returning a :class:`PlanningOutcome` instead of raising."""
    grid = PlanningGrid(
        hazard=hazard_grid,
        elevation=elevation_grid,
        meters_per_cell=meters_per_cell,
        rover=rover,
        slope_coefficient=slope_coefficient,
        max_hazard=max_hazard,
    )
    start_cell = grid.to_cell(start, scale)
    goal_cell = grid.to_cell(goal, scale)

    if start_cell == goal_cell:
        raise ValueError("Start and goal resolve to the same planning cell")

    result = search(grid, start_cell, goal_cell, use_heuristic=use_heuristic)

    if not result.found:
        outcome = diagnose_search_failure(
            result.blocked, result.nodes_expanded, start_cell, goal_cell
        )
        # V1 wording, preserved because the API surfaces it and a test matches it.
        outcome.reason = (
            f"No traversable path from {start} to {goal} after expanding "
            f"{result.nodes_expanded} nodes: "
            f"{result.blocked['moves_blocked_by_slope_limit']} candidate moves exceeded "
            f"the rover's {rover.max_traversable_slope_deg} deg slope limit and "
            f"{result.blocked['moves_blocked_by_lethal_hazard']} were at or above the "
            f"lethal hazard threshold of {max_hazard}."
        )
        return outcome

    metadata = {
        "algorithm": "A_star" if use_heuristic else "dijkstra",
        **grid.cost_model_description(),
        "heuristic": "euclidean_distance_m" if use_heuristic else "zero",
        "planning_grid_shape": {"rows": grid.rows, "cols": grid.cols},
        "downsample_scale": round(scale, 4),
        "nodes_expanded": result.nodes_expanded,
        **result.blocked,
    }
    path = build_stepped_path(grid, result.cells, scale, metadata, total_cost=result.total_cost)
    return PlanningOutcome.success(path, nodes_expanded=result.nodes_expanded)


def plan_path_dijkstra(*args, **kwargs) -> PlannedPath:
    """Dijkstra over the same cost function - A* with the heuristic zeroed."""
    kwargs["use_heuristic"] = False
    return plan_path(*args, **kwargs)
