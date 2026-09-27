"""Orchestration between the HTTP layer and the mission-autonomy engines.

Same rule as ``mission_pipeline``: routers validate and serialise, this module
decides ordering and persistence, and the engines beneath it stay pure functions
over arrays and dataclasses.

One thing this layer has to be explicit about. Every engine below it that
*executes* rather than *plans* needs a ground truth to execute against, and a
real mission does not have one - that is the whole point of the belief model. So
the truth is **synthesised here**, by perturbing the orbital map with hazards it
never resolved, from a recorded seed. The response says so, and the seed and
perturbation parameters are stored with the run, so "the rover was surprised"
is a reproducible statement about a named scenario rather than a vague one.
"""

from __future__ import annotations

import subprocess
import time
import uuid
from functools import lru_cache

import numpy as np
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Experiment, Mission, RoverConfig, ScienceTarget, TraverseRun
from app.services import mission_pipeline
from app.services.mission import comms as comms_module
from app.services.mission.science import ScienceTarget as ScienceTargetSpec
from app.services.mission.science import plan_science_tour
from app.services.planning import cbs, pareto
from app.services.planning.grid import Cell, PlanningGrid, RoverSpec
from app.services.planning.outcome import PlanningOutcome
from app.services.simulation import monte_carlo
from app.services.simulation.belief import BeliefState
from app.services.simulation.execution import simulate_traverse
from app.services.simulation.sensors import RoverSensor

PipelineError = mission_pipeline.PipelineError


@lru_cache(maxsize=1)
def code_revision() -> str:
    """The git revision this process is running, for experiment provenance.

    Best effort: a deployment from a tarball has no git metadata, and an
    experiment is still worth recording without it. Returns ``"unknown"``
    rather than raising, because provenance is evidence, not a precondition.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def rover_spec(config: RoverConfig) -> RoverSpec:
    return RoverSpec(
        battery_capacity_kwh=config.battery_capacity_kwh,
        max_traversable_slope_deg=config.max_traversable_slope_deg,
        energy_per_meter_kwh=config.energy_per_meter_kwh,
    )


class MissionGrids:
    """The arrays for one mission, plus the frame conversions everything needs."""

    __slots__ = ("elevation", "hazard", "meters_per_cell", "scale", "uncertainty")

    def __init__(
        self,
        hazard: np.ndarray,
        elevation: np.ndarray,
        uncertainty: np.ndarray,
        scale: float,
        meters_per_cell: float,
    ) -> None:
        self.hazard = hazard.astype(np.float64)
        self.elevation = elevation.astype(np.float64)
        self.uncertainty = uncertainty.astype(np.float64)
        self.scale = scale
        self.meters_per_cell = meters_per_cell

    @property
    def shape(self) -> tuple[int, int]:
        return self.hazard.shape  # type: ignore[return-value]

    def planning_hazard(self, uncertainty_weight: float) -> np.ndarray:
        """``hazard + k * sigma``, the surface a planner should actually search."""
        if uncertainty_weight <= 0:
            return self.hazard
        return np.clip(self.hazard + uncertainty_weight * self.uncertainty, 0.0, 1.0)

    def grid(self, rover: RoverSpec, settings: Settings, uncertainty_weight: float = 0.0):
        return PlanningGrid(
            hazard=self.planning_hazard(uncertainty_weight),
            elevation=self.elevation,
            meters_per_cell=self.meters_per_cell,
            rover=rover,
            slope_coefficient=settings.energy_slope_coefficient,
            max_hazard=settings.lethal_hazard_threshold,
        )

    def to_cell(self, point: dict) -> Cell:
        rows, cols = self.shape
        row = max(0, min(rows - 1, int(round(point["y"] / self.scale))))
        col = max(0, min(cols - 1, int(round(point["x"] / self.scale))))
        return row, col

    def to_pixel(self, cell: Cell) -> dict:
        return {"x": int(round(cell[1] * self.scale)), "y": int(round(cell[0] * self.scale))}

    def to_pixels(self, cells) -> list[dict]:
        return [self.to_pixel(cell) for cell in cells]


def load_grids(mission: Mission, settings: Settings) -> MissionGrids:
    analysis_row = mission.terrain_analysis
    if analysis_row is None:
        raise PipelineError("Terrain must be analysed before this mission can be simulated.")
    hazard, elevation, uncertainty, scale = mission_pipeline.load_planning_arrays(analysis_row)
    return MissionGrids(hazard, elevation, uncertainty, scale, settings.meters_per_pixel * scale)


# --- terrain payload for the 3-D view ---------------------------------------


def terrain_payload(mission: Mission, settings: Settings, max_dim: int = 128) -> dict:
    """Every per-cell layer the mission-control view renders, in one response.

    Deliberately one call rather than one per layer. The layers are the same
    shape, computed together and toggled together; fetching them separately
    would mean the viewer can show slope for a grid it is no longer displaying.

    Downsampled again to ``max_dim`` before serialising. A 192x192 planning grid
    is 37k floats per layer - about 1 MB of JSON across five layers, which the
    browser parses on every mission open. The renderer interpolates anyway, so
    resolution beyond what a mesh shows is bytes nobody sees.
    """
    from app.services import hazard_mapper

    grids = load_grids(mission, settings)
    hazard, extra_scale = hazard_mapper.downsample_for_planning(
        grids.hazard.astype(np.float32), max_dim
    )
    elevation, _ = hazard_mapper.downsample_for_planning(
        grids.elevation.astype(np.float32), max_dim
    )
    uncertainty, _ = hazard_mapper.downsample_for_planning(
        grids.uncertainty.astype(np.float32), max_dim
    )

    rows, cols = hazard.shape
    meters_per_cell = grids.meters_per_cell * extra_scale
    slope = _slope_degrees(elevation, meters_per_cell)

    # Round first, then derive the ranges from the rounded arrays. Taking the
    # range from the full-precision data instead lets a rounded value fall
    # outside the range shipped beside it, and a renderer that normalises a
    # layer by its stated range would then produce a value outside [0, 1] - an
    # out-of-gamut colour for the one cell at the extreme. The numbers the
    # client gets and the bounds it is told about have to be the same numbers.
    layers = {
        "elevation_m": np.round(elevation.astype(np.float64), 3),
        "hazard": np.round(hazard.astype(np.float64), 4),
        "uncertainty": np.round(uncertainty.astype(np.float64), 4),
        "slope_deg": np.round(slope.astype(np.float64), 2),
    }
    lethal = (layers["hazard"] >= settings.lethal_hazard_threshold).astype(np.uint8)

    return {
        "rows": int(rows),
        "cols": int(cols),
        # Pixels per rendered cell: the frame every waypoint the API returns is in.
        "pixel_scale": round(grids.scale * extra_scale, 4),
        "meters_per_cell": round(meters_per_cell, 4),
        "elevation_range_m": settings.elevation_range_m,
        "lethal_hazard_threshold": settings.lethal_hazard_threshold,
        "layers": {
            **{name: array.tolist() for name, array in layers.items()},
            "lethal": lethal.tolist(),
        },
        "ranges": {
            name: [float(array.min()), float(array.max())] for name, array in layers.items()
        },
    }


def _slope_degrees(elevation: np.ndarray, meters_per_cell: float) -> np.ndarray:
    """Per-cell slope from the rendered grid, for the slope layer.

    Recomputed from the downsampled elevation rather than downsampling the
    full-resolution slope map, because averaging slope over a block is not the
    slope of the averaged block - the first smooths a ridge into a ramp. What
    the viewer shows must be the slope of the surface it is drawing.
    """
    d_row, d_col = np.gradient(elevation.astype(np.float64), meters_per_cell)
    return np.degrees(np.arctan(np.hypot(d_row, d_col))).astype(np.float32)


# --- traverse simulation ----------------------------------------------------


def _synthesise_truth(
    grids: MissionGrids,
    rng: np.random.Generator,
    unmapped_obstacles: int,
    obstacle_radius_cells: int,
    terrain_sigma: float,
    settings: Settings,
    along: list[Cell] | None = None,
) -> np.ndarray:
    """Reality: the orbital map plus hazards it never resolved.

    When ``along`` is given, obstacles are placed on the route the rover is
    about to drive. Scattering them uniformly mostly misses - on a 192-cell grid
    a dozen two-cell rocks have a small chance of touching any particular route -
    and a simulation whose surprises never intersect the plan measures nothing
    about replanning. Placing them on the route is the honest way to exercise
    the thing being demonstrated, and the response says that is what happened.
    """
    truth = np.clip(grids.hazard + rng.normal(0.0, terrain_sigma, grids.hazard.shape), 0.0, 1.0)
    rows, cols = grids.shape
    radius = max(1, obstacle_radius_cells)

    interior = along[4:-4] if along and len(along) > 12 else None
    for _ in range(unmapped_obstacles):
        if interior:
            row, col = interior[int(rng.integers(0, len(interior)))]
        else:
            row = int(rng.integers(radius, max(radius + 1, rows - radius)))
            col = int(rng.integers(radius, max(radius + 1, cols - radius)))
        truth[max(0, row - radius) : row + radius + 1, max(0, col - radius) : col + radius + 1] = (
            min(0.99, settings.lethal_hazard_threshold + 0.1)
        )
    return truth


def run_traverse(
    db: Session,
    mission: Mission,
    rover_config: RoverConfig,
    start: dict,
    goal: dict,
    settings: Settings,
    *,
    seed: int = 0,
    sensor_range_m: float | None = None,
    sensor_noise_sigma: float = 0.05,
    uncertainty_weight: float = 0.0,
    unmapped_obstacles: int = 6,
    obstacle_radius_cells: int = 1,
    terrain_sigma: float = 0.02,
    energy_budget_kwh: float | None = None,
    obstacles_on_route: bool = True,
) -> TraverseRun:
    """Drive the mission under partial observability and persist the result."""
    grids = load_grids(mission, settings)
    spec = rover_spec(rover_config)
    template = grids.grid(spec, settings)  # belief-facing grid, no uncertainty bias

    start_cell = grids.to_cell(start)
    goal_cell = grids.to_cell(goal)
    if start_cell == goal_cell:
        raise ValueError("Start and goal resolve to the same planning cell")

    rng = np.random.default_rng(seed)

    planned: list[Cell] | None = None
    if obstacles_on_route:
        from app.services.planning import astar

        preview = astar.search(template, start_cell, goal_cell)
        planned = preview.cells if preview.found else None

    truth = _synthesise_truth(
        grids,
        rng,
        unmapped_obstacles,
        obstacle_radius_cells,
        terrain_sigma,
        settings,
        along=planned if obstacles_on_route else None,
    )

    # The rover's prior is the orbital map, at the uncertainty the hazard
    # mapper actually derived for it rather than a number picked here.
    prior_sigma = max(float(np.mean(grids.uncertainty)), 0.05) + 0.10
    belief = BeliefState.from_prior(grids.hazard, prior_sigma=prior_sigma)
    sensor = RoverSensor(
        range_m=sensor_range_m or 6 * grids.meters_per_cell,
        noise_sigma=sensor_noise_sigma,
    )

    result = simulate_traverse(
        truth_hazard=truth,
        start=start_cell,
        goal=goal_cell,
        belief=belief,
        sensor=sensor,
        grid_template=template,
        rng=rng,
        uncertainty_weight=uncertainty_weight,
        energy_budget_kwh=energy_budget_kwh,
    )

    row = TraverseRun(
        mission_id=mission.id,
        rover_config_id=rover_config.id,
        status=result.outcome.status,
        failure_mode=result.outcome.failure.value if result.outcome.failure else None,
        reason=(result.outcome.reason or None),
        start_point=start,
        goal_point=goal,
        parameters={
            "seed": seed,
            "uncertainty_weight": uncertainty_weight,
            "sensor": sensor.describe(),
            "prior_sigma": round(prior_sigma, 4),
            "energy_budget_kwh": energy_budget_kwh or rover_config.battery_capacity_kwh,
            "truth_model": {
                "note": (
                    "Ground truth is synthesised, not observed: the orbital hazard map "
                    "plus obstacles it never resolved. A real mission has no truth array; "
                    "this is what the rover is being tested against."
                ),
                "unmapped_obstacles": unmapped_obstacles,
                "obstacle_radius_cells": obstacle_radius_cells,
                "terrain_sigma": terrain_sigma,
                "placed_on_planned_route": bool(obstacles_on_route and planned),
            },
            "planning_grid": {"rows": grids.shape[0], "cols": grids.shape[1]},
            "pixel_scale": grids.scale,
            "code_revision": code_revision(),
        },
        distance_m=round(result.distance_m, 3),
        energy_kwh=round(result.energy_kwh, 6),
        elapsed_seconds=round(result.elapsed_seconds, 1),
        replans=result.replans,
        reroutes=result.reroutes,
        repair_expansions=result.repair_expansions,
        initial_plan=grids.to_pixels(result.initial_plan_cells),
        executed_path=grids.to_pixels(result.executed_cells),
        events=[event.as_dict() for event in result.events],
        belief_summary=result.belief_summary,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


# --- experiments ------------------------------------------------------------


def _record(
    db: Session,
    mission: Mission,
    kind: str,
    parameters: dict,
    outcome: PlanningOutcome | None,
    result: dict,
    runtime: float,
    seed: int | None = None,
) -> Experiment:
    row = Experiment(
        mission_id=mission.id,
        kind=kind,
        status=outcome.status if outcome else "SUCCESS",
        failure_mode=(outcome.failure.value if outcome and outcome.failure else None),
        reason=(outcome.reason or None) if outcome else None,
        parameters={**parameters, "code_revision": code_revision()},
        seed=seed,
        code_revision=code_revision(),
        result=result,
        runtime_seconds=round(runtime, 3),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_route_study(
    db: Session,
    mission: Mission,
    rover_config: RoverConfig,
    start: dict,
    goal: dict,
    settings: Settings,
) -> Experiment:
    """Sweep the objective weights and keep the non-dominated routes."""
    grids = load_grids(mission, settings)
    start_cell = grids.to_cell(start)
    goal_cell = grids.to_cell(goal)
    if start_cell == goal_cell:
        raise ValueError("Start and goal resolve to the same planning cell")

    started = time.perf_counter()
    sweep = pareto.sweep(
        grids.hazard,
        grids.elevation,
        start_cell,
        goal_cell,
        rover_spec(rover_config),
        grids.meters_per_cell,
        slope_coefficient=settings.energy_slope_coefficient,
        max_hazard=settings.lethal_hazard_threshold,
        scale=grids.scale,
    )
    runtime = time.perf_counter() - started

    front_objects = sweep.pop("_front_objects", [])
    routes = [
        {**candidate.as_dict(), "waypoints": grids.to_pixels(candidate.cells)}
        for candidate in front_objects
    ]
    result = {**sweep, "front": routes}

    outcome = None
    if not routes:
        from app.services.planning.outcome import FailureMode

        outcome = PlanningOutcome.failed(
            FailureMode.NO_SAFE_PATH,
            "No weighting of the cost model found a route between these points.",
        )

    return _record(
        db,
        mission,
        "route_study",
        {
            "rover_config_id": str(rover_config.id),
            "start": start,
            "goal": goal,
        },
        outcome,
        result,
        runtime,
    )


def run_monte_carlo_study(
    db: Session,
    mission: Mission,
    rover_config: RoverConfig,
    start: dict,
    goal: dict,
    settings: Settings,
    *,
    trials: int = 50,
    seed: int = 0,
    uncertainty_weight: float = 0.0,
    sensor_range_m: float | None = None,
    perturbations: monte_carlo.Perturbations | None = None,
    workers: int | None = None,
    progress=None,
) -> Experiment:
    grids = load_grids(mission, settings)
    spec = rover_spec(rover_config)
    template = grids.grid(spec, settings)
    start_cell = grids.to_cell(start)
    goal_cell = grids.to_cell(goal)
    if start_cell == goal_cell:
        raise ValueError("Start and goal resolve to the same planning cell")

    sensor = RoverSensor(range_m=sensor_range_m or 6 * grids.meters_per_cell)
    started = time.perf_counter()
    report = monte_carlo.run(
        orbital_hazard=grids.hazard,
        elevation=grids.elevation,
        start=start_cell,
        goal=goal_cell,
        grid_template=template,
        sensor=sensor,
        trials=trials,
        seed=seed,
        perturbations=perturbations,
        uncertainty_weight=uncertainty_weight,
        prior_sigma=max(float(np.mean(grids.uncertainty)), 0.05) + 0.10,
        workers=workers,
        progress=progress,
    )
    runtime = time.perf_counter() - started

    return _record(
        db,
        mission,
        "monte_carlo",
        {
            "rover_config_id": str(rover_config.id),
            "start": start,
            "goal": goal,
            "trials": trials,
            "uncertainty_weight": uncertainty_weight,
            "sensor": sensor.describe(),
            "workers": report.workers,
        },
        None,
        report.as_dict(),
        runtime,
        seed=seed,
    )


def run_fleet_plan(
    db: Session,
    mission: Mission,
    assignments: list[dict],
    settings: Settings,
    *,
    max_nodes: int = 600,
    time_budget_seconds: float = 30.0,
    coordination_max_dim: int | None = None,
) -> Experiment:
    """Deconflict a fleet, each rover on its own graph.

    Coordination runs on a coarser grid than navigation by default: the
    low-level state space is cells x ticks, so halving resolution cuts it by
    roughly eight, and the question being answered is who crosses the middle
    first, not which rock to pass on the left.
    """
    from app.services import hazard_mapper

    grids = load_grids(mission, settings)
    target_dim = coordination_max_dim or max(24, max(grids.shape) // 2)
    hazard, extra_scale = hazard_mapper.downsample_for_planning(
        grids.hazard.astype(np.float32), target_dim
    )
    elevation, _ = hazard_mapper.downsample_for_planning(
        grids.elevation.astype(np.float32), target_dim
    )
    coarse = MissionGrids(
        hazard,
        elevation,
        np.zeros_like(hazard),
        grids.scale * extra_scale,
        grids.meters_per_cell * extra_scale,
    )

    agents: list[cbs.Agent] = []
    for entry in assignments:
        config = entry["rover_config"]
        agents.append(
            cbs.Agent(
                agent_id=entry.get("label") or config.name,
                grid=coarse.grid(rover_spec(config), settings),
                start=coarse.to_cell(entry["start"]),
                goal=coarse.to_cell(entry["goal"]),
            )
        )

    started = time.perf_counter()
    outcome = cbs.solve(
        agents, max_high_level_nodes=max_nodes, time_budget_seconds=time_budget_seconds
    )
    runtime = time.perf_counter() - started

    result: dict = {}
    if outcome.succeeded:
        solution = outcome.diagnostics["solution"]
        # Verified independently of the solver's own report, as in the tests.
        residual = cbs.find_conflicts(solution.paths)
        result = {
            **solution.as_dict(),
            "verified_conflict_free": not residual,
            "residual_conflicts": [c.as_dict() for c in residual],
            "coordination_grid": {
                "rows": coarse.shape[0],
                "cols": coarse.shape[1],
                "meters_per_cell": round(coarse.meters_per_cell, 3),
                "note": "coarser than the navigation grid on purpose; see docs/planners.md",
            },
            "routes": {
                agent_id: coarse.to_pixels(path) for agent_id, path in solution.paths.items()
            },
        }
    else:
        result = {"diagnostics": outcome.diagnostics}

    return _record(
        db,
        mission,
        "fleet_plan",
        {
            "assignments": [
                {
                    "label": entry.get("label") or entry["rover_config"].name,
                    "rover_config_id": str(entry["rover_config"].id),
                    "start": entry["start"],
                    "goal": entry["goal"],
                }
                for entry in assignments
            ],
            "max_high_level_nodes": max_nodes,
            "time_budget_seconds": time_budget_seconds,
        },
        outcome,
        result,
        runtime,
    )


def run_science_tour(
    db: Session,
    mission: Mission,
    rover_config: RoverConfig,
    start: dict,
    settings: Settings,
    *,
    energy_budget_kwh: float | None = None,
    time_budget_seconds: float = 4 * 88775.0,
    instruments: list[str] | None = None,
    relay_at: dict | None = None,
) -> Experiment:
    """Select and order the mission's science targets, then evaluate the link."""
    grids = load_grids(mission, settings)
    spec = rover_spec(rover_config)
    grid = grids.grid(spec, settings)

    stored: list[ScienceTarget] = list(mission.science_targets)
    if not stored:
        raise PipelineError(
            "This mission has no science targets. Add some with "
            "POST /missions/{id}/science-targets first."
        )

    targets = [
        ScienceTargetSpec(
            target_id=row.label,
            cell=grids.to_cell({"x": row.x, "y": row.y}),
            value=row.value,
            observation_seconds=row.observation_seconds,
            required_instrument=row.required_instrument,
            priority=row.priority,
        )
        for row in stored
    ]

    started = time.perf_counter()
    outcome = plan_science_tour(
        grid,
        grids.to_cell(start),
        targets,
        energy_budget_kwh=energy_budget_kwh,
        time_budget_seconds=time_budget_seconds,
        instruments=frozenset(instruments or []),
    )
    runtime = time.perf_counter() - started

    result: dict = {}
    if outcome.succeeded:
        tour = outcome.diagnostics["tour"]
        result = {**tour.as_dict(), "waypoints": grids.to_pixels(tour.cells)}
        if relay_at is not None:
            plan = comms_module.CommunicationPlan(
                stations=[
                    comms_module.RelayStation(
                        "RELAY", grids.to_cell(relay_at), antenna_height_m=3.0
                    )
                ],
                orbiters=[comms_module.OrbiterPass("ORBITER")],
            )
            result["communications"] = plan.evaluate_route(grid, tour.cells)

    return _record(
        db,
        mission,
        "science_tour",
        {
            "rover_config_id": str(rover_config.id),
            "start": start,
            "energy_budget_kwh": energy_budget_kwh or rover_config.battery_capacity_kwh,
            "time_budget_seconds": time_budget_seconds,
            "instruments": sorted(instruments or []),
            "relay_at": relay_at,
        },
        outcome,
        result,
        runtime,
    )


def resolve_rover(db: Session, rover_config_id: uuid.UUID) -> RoverConfig:
    config = db.get(RoverConfig, rover_config_id)
    if config is None:
        raise PipelineError(f"Rover config {rover_config_id} not found")
    return config
