"""A* rover path planning with an energy-aware cost function.

Cost model
----------
For a move from cell ``a`` to neighbouring cell ``b``::

    cost(a, b) = distance_m(a, b) * (1 + hazard(b)) * energy_factor(slope(a, b))
    energy_factor(slope) = 1 + k * |rise / run|

``rise / run`` is ``tan(slope)``, taken from the elevation model rather than
from a normalised gradient image, so the factor tracks the actual gravitational
work a drive motor does per metre travelled. ``k`` (``energy_slope_coefficient``)
is a tunable modelling constant, not a measured rover parameter - it is stored
with every plan so the number is auditable.

Two hazard layers
-----------------
``(1 + hazard)`` is bounded by 2, so on its own the hazard term is a *preference*
and never a *prohibition*: doubling the cost of one cell will never outweigh a
fifteen-cell detour, and the planner will happily drive straight through a
crater rim. Cost alone is therefore not enough.

So there are two layers, the same split the ROS navigation costmap uses:

* a **cost layer** - ``(1 + hazard)``, which shapes the route inside traversable
  ground, and
* a **lethal layer** - ``max_hazard`` and the rover's slope limit, which remove
  edges from the graph entirely.

Admissibility
-------------
The heuristic is straight-line distance in metres. Because ``hazard >= 0`` and
``energy_factor >= 1``, every edge satisfies ``cost(a, b) >= distance_m(a, b)``.
Summed over any path, the true cost is therefore never less than the straight-
line distance, so ``h`` never overestimates: the heuristic is admissible, and
since it also satisfies the triangle inequality it is consistent, meaning A*
returns an optimal path without needing to re-open closed nodes.

Why A* and not Dijkstra: identical optimality guarantee under an admissible
heuristic, but Dijkstra expands uniformly in every direction while A* biases
expansion toward the goal. ``nodes_expanded`` is recorded on every plan so the
difference is measurable rather than asserted - ``scripts/benchmark_planner.py``
runs both on the same grid.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

# 8-connected grid. Diagonals cost sqrt(2) cells, which the distance term
# handles naturally because it is computed in metres.
_NEIGHBOURS: tuple[tuple[int, int], ...] = (
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-1, -1), (-1, 1), (1, -1), (1, 1),
)


class PathNotFoundError(RuntimeError):
    """No traversable route exists between start and goal for this rover.

    Raised when the open set is exhausted - because the rover's slope limit or
    the lethal-hazard threshold walls off every corridor to the goal.
    """


@dataclass(slots=True)
class RoverSpec:
    """Planner-facing view of a rover configuration."""

    battery_capacity_kwh: float
    max_traversable_slope_deg: float
    energy_per_meter_kwh: float


@dataclass(slots=True)
class Waypoint:
    segment_id: int
    x: int  # original image pixel coordinates
    y: int
    hazard_score: float
    slope_deg: float
    step_distance_m: float
    energy_cost_kwh: float  # energy for the step into this waypoint
    cumulative_energy_kwh: float


@dataclass(slots=True)
class PlannedPath:
    waypoints: list[Waypoint]
    total_distance_m: float
    total_energy_cost_kwh: float
    total_cost: float
    metadata: dict = field(default_factory=dict)

    def hazard_series(self) -> list[float]:
        return [w.hazard_score for w in self.waypoints]


def energy_factor(rise_over_run: float, k: float) -> float:
    """Multiplier on per-metre energy draw for a given incline.

    Uses ``|rise/run|`` - descending is charged the same as climbing. Real
    rovers recover nothing on a descent but do spend less than on the
    equivalent climb, so this over-charges downhill segments. Documented as a
    known simplification rather than silently ignored (see docs/architecture.md).
    """
    return 1.0 + k * abs(rise_over_run)


def _to_grid(point: dict, scale: float, shape: tuple[int, int]) -> tuple[int, int]:
    """Image pixel {x, y} -> clamped grid (row, col)."""
    rows, cols = shape
    col = int(round(point["x"] / scale))
    row = int(round(point["y"] / scale))
    return max(0, min(rows - 1, row)), max(0, min(cols - 1, col))


def plan_path(
    hazard_grid: np.ndarray,
    elevation_grid: np.ndarray,
    start: dict,
    goal: dict,
    rover: RoverSpec,
    meters_per_cell: float,
    scale: float = 1.0,
    slope_coefficient: float = 0.5,
    max_hazard: float = 1.0,
    use_heuristic: bool = True,
) -> PlannedPath:
    """Run A* from ``start`` to ``goal``.

    ``start`` / ``goal`` are ``{"x": int, "y": int}`` in *original image* pixel
    coordinates; ``scale`` converts them into the (possibly downsampled)
    planning grid.

    ``max_hazard`` is the lethal-hazard threshold: cells at or above it are
    removed from the graph. The default of 1.0 disables the layer, since hazard
    scores are bounded to [0, 1].

    ``use_heuristic=False`` zeroes the heuristic, which reduces the identical
    search to Dijkstra - used by the benchmark to compare node expansions.
    """
    if hazard_grid.shape != elevation_grid.shape:
        raise ValueError("hazard and elevation grids must have the same shape")

    rows, cols = hazard_grid.shape
    start_rc = _to_grid(start, scale, hazard_grid.shape)
    goal_rc = _to_grid(goal, scale, hazard_grid.shape)

    if start_rc == goal_rc:
        raise ValueError("Start and goal resolve to the same planning cell")

    max_slope_tan = math.tan(math.radians(rover.max_traversable_slope_deg))

    def heuristic(rc: tuple[int, int]) -> float:
        if not use_heuristic:
            return 0.0
        return math.hypot(rc[0] - goal_rc[0], rc[1] - goal_rc[1]) * meters_per_cell

    g_score = np.full((rows, cols), np.inf, dtype=np.float64)
    g_score[start_rc] = 0.0
    came_from: dict[tuple[int, int], tuple[int, int]] = {}
    closed = np.zeros((rows, cols), dtype=bool)

    counter = 0  # FIFO tie-break; keeps the heap ordering deterministic
    open_heap: list[tuple[float, int, tuple[int, int]]] = [(heuristic(start_rc), counter, start_rc)]

    nodes_expanded = 0
    blocked_by_slope = 0
    blocked_by_hazard = 0

    while open_heap:
        _, _, current = heapq.heappop(open_heap)
        if closed[current]:
            continue
        closed[current] = True
        nodes_expanded += 1

        if current == goal_rc:
            return _reconstruct(
                came_from=came_from,
                goal_rc=goal_rc,
                start_rc=start_rc,
                hazard_grid=hazard_grid,
                elevation_grid=elevation_grid,
                rover=rover,
                meters_per_cell=meters_per_cell,
                scale=scale,
                slope_coefficient=slope_coefficient,
                total_cost=float(g_score[goal_rc]),
                nodes_expanded=nodes_expanded,
                blocked_by_slope=blocked_by_slope,
                blocked_by_hazard=blocked_by_hazard,
                max_hazard=max_hazard,
                grid_shape=(rows, cols),
                use_heuristic=use_heuristic,
            )

        cr, cc = current
        for dr, dc in _NEIGHBOURS:
            nr, nc = cr + dr, cc + dc
            if not (0 <= nr < rows and 0 <= nc < cols) or closed[nr, nc]:
                continue

            # Lethal-hazard layer: too dangerous to enter at any cost.
            if float(hazard_grid[nr, nc]) >= max_hazard:
                blocked_by_hazard += 1
                continue

            step_m = math.hypot(dr, dc) * meters_per_cell
            rise_m = float(elevation_grid[nr, nc] - elevation_grid[cr, cc])
            rise_over_run = rise_m / step_m

            # Hard traversability constraint - this is what makes a mission
            # genuinely INFEASIBLE rather than merely expensive.
            if abs(rise_over_run) > max_slope_tan:
                blocked_by_slope += 1
                continue

            step_cost = (
                step_m
                * (1.0 + float(hazard_grid[nr, nc]))
                * energy_factor(rise_over_run, slope_coefficient)
            )
            tentative = g_score[cr, cc] + step_cost
            if tentative < g_score[nr, nc]:
                g_score[nr, nc] = tentative
                came_from[(nr, nc)] = current
                counter += 1
                heapq.heappush(open_heap, (tentative + heuristic((nr, nc)), counter, (nr, nc)))

    raise PathNotFoundError(
        f"No traversable path from {start} to {goal} after expanding {nodes_expanded} "
        f"nodes: {blocked_by_slope} candidate moves exceeded the rover's "
        f"{rover.max_traversable_slope_deg} deg slope limit and {blocked_by_hazard} "
        f"were at or above the lethal hazard threshold of {max_hazard}."
    )


def _reconstruct(
    *,
    came_from: dict[tuple[int, int], tuple[int, int]],
    goal_rc: tuple[int, int],
    start_rc: tuple[int, int],
    hazard_grid: np.ndarray,
    elevation_grid: np.ndarray,
    rover: RoverSpec,
    meters_per_cell: float,
    scale: float,
    slope_coefficient: float,
    total_cost: float,
    nodes_expanded: int,
    blocked_by_slope: int,
    blocked_by_hazard: int,
    max_hazard: float,
    grid_shape: tuple[int, int],
    use_heuristic: bool,
) -> PlannedPath:
    cells: list[tuple[int, int]] = [goal_rc]
    while cells[-1] != start_rc:
        cells.append(came_from[cells[-1]])
    cells.reverse()

    waypoints: list[Waypoint] = []
    total_distance = 0.0
    cumulative_energy = 0.0

    for index, (row, col) in enumerate(cells):
        if index == 0:
            step_m = 0.0
            slope_deg = 0.0
            step_energy = 0.0
        else:
            prev_row, prev_col = cells[index - 1]
            step_m = math.hypot(row - prev_row, col - prev_col) * meters_per_cell
            rise_m = float(elevation_grid[row, col] - elevation_grid[prev_row, prev_col])
            rise_over_run = rise_m / step_m
            slope_deg = math.degrees(math.atan(rise_over_run))
            step_energy = (
                rover.energy_per_meter_kwh
                * step_m
                * energy_factor(rise_over_run, slope_coefficient)
            )

        total_distance += step_m
        cumulative_energy += step_energy
        waypoints.append(
            Waypoint(
                segment_id=index,
                x=int(round(col * scale)),
                y=int(round(row * scale)),
                hazard_score=round(float(hazard_grid[row, col]), 4),
                slope_deg=round(slope_deg, 3),
                step_distance_m=round(step_m, 3),
                energy_cost_kwh=round(step_energy, 6),
                cumulative_energy_kwh=round(cumulative_energy, 6),
            )
        )

    metadata = {
        "algorithm": "A_star" if use_heuristic else "dijkstra",
        "connectivity": 8,
        "heuristic": "euclidean_distance_m" if use_heuristic else "zero",
        "heuristic_admissible": True,
        "admissibility_argument": (
            "edge cost = distance_m * (1 + hazard) * energy_factor, with hazard >= 0 "
            "and energy_factor >= 1, so cost >= distance_m; straight-line distance "
            "therefore never overestimates remaining cost"
        ),
        "planning_grid_shape": {"rows": grid_shape[0], "cols": grid_shape[1]},
        "meters_per_cell": round(meters_per_cell, 4),
        "downsample_scale": round(scale, 4),
        "nodes_expanded": nodes_expanded,
        "moves_blocked_by_slope_limit": blocked_by_slope,
        "moves_blocked_by_lethal_hazard": blocked_by_hazard,
        "lethal_hazard_threshold": max_hazard,
        "energy_slope_coefficient": slope_coefficient,
        "cost_function": "distance_m * (1 + hazard) * (1 + k * |rise/run|)",
        "hard_constraints": (
            "edge removed if |slope| > max_traversable_slope_deg "
            "or hazard >= lethal_hazard_threshold"
        ),
        "energy_model": "energy_kwh = energy_per_meter_kwh * distance_m * (1 + k * |rise/run|)",
    }

    return PlannedPath(
        waypoints=waypoints,
        total_distance_m=round(total_distance, 3),
        total_energy_cost_kwh=round(cumulative_energy, 6),
        total_cost=round(total_cost, 4),
        metadata=metadata,
    )


def plan_path_dijkstra(*args, **kwargs) -> PlannedPath:
    """Dijkstra over the same cost function - A* with the heuristic zeroed.

    Exists so the benchmark script can compare ``nodes_expanded`` against A* on
    identical input and confirm both return the same total cost (which is the
    empirical check that the heuristic really is admissible).
    """
    kwargs["use_heuristic"] = False
    return plan_path(*args, **kwargs)
