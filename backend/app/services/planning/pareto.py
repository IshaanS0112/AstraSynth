"""The trade-off surface between distance, energy and risk."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np

from app.services.planning import astar
from app.services.planning.grid import Cell, PlanningGrid
from app.services.planning.trace import build_stepped_path, heading_changes

# Objectives are all minimised. Adding one means adding it here and in
# _measure; the dominance test is generic over whatever this contains.
OBJECTIVES = ("distance_m", "energy_kwh", "mean_hazard", "max_hazard")


@dataclass(slots=True)
class RouteCandidate:
    """One route, with the weights that produced it and its measured objectives."""

    label: str
    hazard_weight: float
    energy_weight: float
    cells: list[Cell]
    objectives: dict[str, float]
    nodes_expanded: int
    heading_changes: int
    path: object = field(default=None, repr=False)

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "search_weights": {
                "hazard": self.hazard_weight,
                "energy": self.energy_weight,
            },
            "objectives": {k: round(v, 6) for k, v in self.objectives.items()},
            "nodes_expanded": self.nodes_expanded,
            "heading_changes": self.heading_changes,
            "waypoint_count": len(self.cells),
        }


def dominates(a: dict[str, float], b: dict[str, float], objectives=OBJECTIVES) -> bool:
    """``a`` dominates ``b``: no worse on every objective, strictly better on one."""
    no_worse = all(a[key] <= b[key] for key in objectives)
    strictly_better = any(a[key] < b[key] for key in objectives)
    return no_worse and strictly_better


def pareto_front(candidates: list[RouteCandidate], objectives=OBJECTIVES) -> list[RouteCandidate]:
    """The non-dominated subset, in the order given."""
    front: list[RouteCandidate] = []
    for candidate in candidates:
        if any(
            dominates(other.objectives, candidate.objectives, objectives)
            for other in candidates
            if other is not candidate
        ):
            continue
        front.append(candidate)
    return front


def _measure(grid: PlanningGrid, cells: list[Cell]) -> dict[str, float]:
    """True objectives for a route, independent of the weights that found it."""
    distance = 0.0
    energy = 0.0
    hazards = [float(grid.hazard[cells[0]])]
    for previous, cell in itertools.pairwise(cells):
        distance += grid.distance_m(previous, cell)
        energy += grid.step_energy_kwh(previous, cell)
        hazards.append(float(grid.hazard[cell]))
    return {
        "distance_m": distance,
        "energy_kwh": energy,
        "mean_hazard": float(np.mean(hazards)),
        "max_hazard": float(max(hazards)),
    }


DEFAULT_SWEEP: tuple[tuple[float, float], ...] = (
    (0.0, 0.0),  # shortest geometric route the constraints allow
    (0.5, 1.0),
    (1.0, 1.0),  # the V1 default
    (2.0, 1.0),
    (4.0, 1.0),
    (8.0, 1.0),  # strongly hazard-averse
    (1.0, 0.0),  # ignore incline: pure hazard-vs-distance
    (1.0, 4.0),  # strongly energy-averse
    (4.0, 4.0),
)


def sweep(
    hazard_grid: np.ndarray,
    elevation_grid: np.ndarray,
    start: Cell,
    goal: Cell,
    rover,
    meters_per_cell: float,
    slope_coefficient: float = 0.5,
    max_hazard: float = 1.0,
    scale: float = 1.0,
    weights: tuple[tuple[float, float], ...] = DEFAULT_SWEEP,
) -> dict:
    """Plan at every weighting, measure every result, return the front."""
    # Measurement grid: unweighted, so every candidate is measured identically.
    measure_grid = PlanningGrid(
        hazard=hazard_grid,
        elevation=elevation_grid,
        meters_per_cell=meters_per_cell,
        rover=rover,
        slope_coefficient=slope_coefficient,
        max_hazard=max_hazard,
    )

    candidates: list[RouteCandidate] = []
    seen: set[tuple[Cell, ...]] = set()
    failures: list[dict] = []

    for hazard_weight, energy_weight in weights:
        search_grid = PlanningGrid(
            hazard=hazard_grid,
            elevation=elevation_grid,
            meters_per_cell=meters_per_cell,
            rover=rover,
            slope_coefficient=slope_coefficient,
            max_hazard=max_hazard,
            hazard_weight=hazard_weight,
            energy_weight=energy_weight,
        )
        result = astar.search(search_grid, start, goal)
        if not result.found:
            failures.append({"hazard_weight": hazard_weight, "energy_weight": energy_weight})
            continue

        key = tuple(result.cells)
        if key in seen:
            continue
        seen.add(key)

        objectives = _measure(measure_grid, result.cells)
        label = f"w_hazard={hazard_weight:g}, w_energy={energy_weight:g}"
        metadata = {
            "algorithm": "A_star",
            **search_grid.cost_model_description(),
            "nodes_expanded": result.nodes_expanded,
            **result.blocked,
        }
        candidates.append(
            RouteCandidate(
                label=label,
                hazard_weight=hazard_weight,
                energy_weight=energy_weight,
                cells=result.cells,
                objectives=objectives,
                nodes_expanded=result.nodes_expanded,
                heading_changes=heading_changes(result.cells),
                path=build_stepped_path(
                    measure_grid, result.cells, scale, metadata, total_cost=result.total_cost
                ),
            )
        )

    front = pareto_front(candidates)
    return {
        "method": (
            "weighted-sum scalarisation over the planner cost model; recovers only "
            "the convex hull of the true Pareto front"
        ),
        "objectives": list(OBJECTIVES),
        "weights_swept": [{"hazard": h, "energy": e} for h, e in weights],
        "candidates_planned": len(candidates),
        "weightings_with_no_route": failures,
        "front": [c.as_dict() for c in front],
        "dominated": [c.as_dict() for c in candidates if c not in front],
        "extremes": _extremes(front),
        "_front_objects": front,
    }


def _extremes(front: list[RouteCandidate]) -> dict:
    """The best route for each objective taken alone - the corners of the front."""
    if not front:
        return {}
    return {
        objective: min(front, key=lambda c: c.objectives[objective]).label
        for objective in OBJECTIVES
    }
