"""Orchestration between the HTTP layer and the analysis engines.

Routers stay thin: they validate input, call one function here, and serialise
the result. All ordering rules ("you cannot plan a path before the terrain has
been analysed") live in this module.

Persistence note
----------------
The hazard and elevation grids are the analysis stage's real output, but they
are arrays, not rows. They are written to ``storage/<mission_id>/analysis.npz``
and referenced from ``analysis_metadata``, so path planning reloads them rather
than re-running the whole CV pipeline. Re-analysing a mission overwrites the
file; the analysis is deterministic, so a stale read is not a correctness risk,
only a wasted one.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.enums import MissionStatus
from app.models import (
    Mission,
    MissionRiskReport,
    RoverConfig,
    RoverPath,
    TerrainAnalysis as TerrainAnalysisRow,
)
from app.services import hazard_mapper, report_generator, risk_engine, terrain_analyzer
from app.services.path_planner import (
    PathNotFoundError,
    PlannedPath,
    RoverSpec,
    Waypoint,
    plan_path,
)


class PipelineError(RuntimeError):
    """A pipeline stage was invoked out of order or with missing inputs."""


def mission_storage_dir(settings: Settings, mission_id: uuid.UUID) -> Path:
    directory = settings.storage_dir / str(mission_id)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def rover_spec(config: RoverConfig) -> RoverSpec:
    return RoverSpec(
        battery_capacity_kwh=config.battery_capacity_kwh,
        max_traversable_slope_deg=config.max_traversable_slope_deg,
        energy_per_meter_kwh=config.energy_per_meter_kwh,
    )


# --- Stage 1: terrain analysis ---------------------------------------------


def run_terrain_analysis(
    db: Session, mission: Mission, settings: Settings
) -> TerrainAnalysisRow:
    analysis = terrain_analyzer.analyze_terrain(mission.terrain_image_path, settings)
    hazard = hazard_mapper.build_hazard_map(analysis, settings)

    directory = mission_storage_dir(settings, mission.id)
    slope_map_path = terrain_analyzer.render_slope_map(
        analysis.slope_deg, directory / "slope_map.png"
    )
    heatmap_path = hazard_mapper.render_hazard_heatmap(
        hazard, mission.terrain_image_path, directory / "hazard_heatmap.png"
    )

    # Planning grid: downsample once here so the planner and the stored
    # metadata agree on exactly which grid a path was computed over.
    hazard_grid, scale = hazard_mapper.downsample_for_planning(
        hazard.scores, settings.planning_grid_max_dim
    )
    elevation_grid, _ = hazard_mapper.downsample_for_planning(
        analysis.elevation_m.astype(np.float32), settings.planning_grid_max_dim
    )
    arrays_path = directory / "analysis.npz"
    np.savez_compressed(
        arrays_path, hazard_grid=hazard_grid, elevation_grid=elevation_grid
    )

    metadata = {
        **analysis.stats,
        "hazard_calculation_basis": hazard.calculation_basis,
        "planning_grid": {
            "rows": int(hazard_grid.shape[0]),
            "cols": int(hazard_grid.shape[1]),
            "downsample_scale": round(float(scale), 4),
            "meters_per_cell": round(settings.meters_per_pixel * float(scale), 4),
        },
        "arrays_path": str(arrays_path),
    }

    row = db.scalar(
        select(TerrainAnalysisRow).where(TerrainAnalysisRow.mission_id == mission.id)
    )
    if row is None:
        row = TerrainAnalysisRow(mission_id=mission.id)
        db.add(row)

    row.slope_map_path = slope_map_path
    row.hazard_heatmap_path = heatmap_path
    row.terrain_classification = analysis.classification.value
    row.obstacle_contours = [o.as_dict() for o in analysis.obstacles]
    row.analysis_metadata = metadata

    mission.status = MissionStatus.ANALYZED
    db.commit()
    db.refresh(row)
    return row


def load_planning_grids(analysis_row: TerrainAnalysisRow) -> tuple[np.ndarray, np.ndarray, float]:
    metadata = analysis_row.analysis_metadata or {}
    arrays_path = metadata.get("arrays_path")
    if not arrays_path or not Path(arrays_path).exists():
        raise PipelineError(
            "Planning grids are missing for this mission. Re-run POST /missions/{id}/analyze-terrain."
        )
    with np.load(arrays_path) as data:
        hazard_grid = data["hazard_grid"]
        elevation_grid = data["elevation_grid"]
    scale = float(metadata["planning_grid"]["downsample_scale"])
    return hazard_grid, elevation_grid, scale


# --- Stage 2: path planning -------------------------------------------------


def run_path_planning(
    db: Session,
    mission: Mission,
    rover_config: RoverConfig,
    start: dict,
    end: dict,
    settings: Settings,
) -> RoverPath:
    analysis_row = mission.terrain_analysis
    if analysis_row is None:
        raise PipelineError("Terrain must be analysed before a path can be planned.")

    hazard_grid, elevation_grid, scale = load_planning_grids(analysis_row)
    meters_per_cell = settings.meters_per_pixel * scale

    planned = plan_path(
        hazard_grid=hazard_grid,
        elevation_grid=elevation_grid,
        start=start,
        goal=end,
        rover=rover_spec(rover_config),
        meters_per_cell=meters_per_cell,
        scale=scale,
        slope_coefficient=settings.energy_slope_coefficient,
        max_hazard=settings.lethal_hazard_threshold,
    )

    row = RoverPath(
        mission_id=mission.id,
        rover_config_id=rover_config.id,
        start_point=start,
        end_point=end,
        waypoints=[waypoint_dict(w) for w in planned.waypoints],
        total_distance_m=planned.total_distance_m,
        total_energy_cost_kwh=planned.total_energy_cost_kwh,
        algorithm_used=planned.metadata["algorithm"],
        planner_metadata=planned.metadata,
    )
    db.add(row)
    mission.status = MissionStatus.PATH_PLANNED
    db.commit()
    db.refresh(row)
    return row


def waypoint_dict(waypoint: Waypoint) -> dict:
    return {
        "segment_id": waypoint.segment_id,
        "x": waypoint.x,
        "y": waypoint.y,
        "hazard_score": waypoint.hazard_score,
        "slope_deg": waypoint.slope_deg,
        "step_distance_m": waypoint.step_distance_m,
        "energy_cost_kwh": waypoint.energy_cost_kwh,
        "cumulative_energy_kwh": waypoint.cumulative_energy_kwh,
    }


def rehydrate_path(row: RoverPath) -> PlannedPath:
    """Rebuild the planner dataclass from a stored row.

    Risk assessment is pure and cheap, so it is recomputed from stored waypoints
    rather than cached - one fewer thing that can go stale relative to the path.
    """
    waypoints = [
        Waypoint(
            segment_id=w["segment_id"],
            x=w["x"],
            y=w["y"],
            hazard_score=w["hazard_score"],
            slope_deg=w["slope_deg"],
            step_distance_m=w["step_distance_m"],
            energy_cost_kwh=w["energy_cost_kwh"],
            cumulative_energy_kwh=w["cumulative_energy_kwh"],
        )
        for w in row.waypoints
    ]
    return PlannedPath(
        waypoints=waypoints,
        total_distance_m=row.total_distance_m or 0.0,
        total_energy_cost_kwh=row.total_energy_cost_kwh or 0.0,
        total_cost=(row.planner_metadata or {}).get("total_cost", 0.0),
        metadata=row.planner_metadata or {},
    )


# --- Stage 3 + 4: risk assessment and report --------------------------------


def resolve_path(db: Session, mission: Mission, path_id: uuid.UUID | None) -> RoverPath:
    if path_id is not None:
        row = db.get(RoverPath, path_id)
        if row is None or row.mission_id != mission.id:
            raise PipelineError("rover_path_id does not belong to this mission.")
        return row
    if not mission.paths:
        raise PipelineError("No path has been planned for this mission yet.")
    return mission.paths[-1]


def run_risk_assessment(
    db: Session, mission: Mission, path_row: RoverPath, settings: Settings
) -> MissionRiskReport:
    """Compute the deterministic half of the report and persist it.

    Deliberately stops before the LLM. The structured context is complete and
    queryable at this point; generating the narrative is a separate, optional,
    failure-tolerant step.
    """
    analysis_row = mission.terrain_analysis
    if analysis_row is None:
        raise PipelineError("Terrain must be analysed before risk can be assessed.")

    rover_config = db.get(RoverConfig, path_row.rover_config_id)
    if rover_config is None:
        raise PipelineError("Rover config for this path no longer exists.")

    spec = rover_spec(rover_config)
    planned = rehydrate_path(path_row)
    risk = risk_engine.assess_mission_risk(planned, spec, settings)

    context = report_generator.build_structured_context(
        mission_id=str(mission.id),
        mission_name=mission.name,
        terrain_source=mission.terrain_source,
        terrain_metadata=analysis_row.analysis_metadata or {},
        obstacle_count=len(analysis_row.obstacle_contours or []),
        classification=analysis_row.terrain_classification,
        path=planned,
        rover=spec,
        rover_name=rover_config.name,
        risk=risk,
    )

    row = db.scalar(
        select(MissionRiskReport).where(MissionRiskReport.rover_path_id == path_row.id)
    )
    if row is None:
        row = MissionRiskReport(mission_id=mission.id, rover_path_id=path_row.id)
        db.add(row)

    row.risk_score = risk.risk_tier.value
    row.feasibility = risk.feasibility.value
    row.structured_context = context
    mission.status = MissionStatus.RISK_ASSESSED
    db.commit()
    db.refresh(row)
    return row


def run_report_generation(
    db: Session, mission: Mission, report_row: MissionRiskReport, settings: Settings
) -> MissionRiskReport:
    narrative = report_generator.generate_narrative(report_row.structured_context, settings)
    report_row.ai_narrative = narrative
    report_row.narrative_source = narrative.get("generated_by", "unknown")
    mission.status = MissionStatus.REPORT_GENERATED
    db.commit()
    db.refresh(report_row)
    return report_row


__all__ = [
    "PathNotFoundError",
    "PipelineError",
    "load_planning_grids",
    "rehydrate_path",
    "resolve_path",
    "rover_spec",
    "run_path_planning",
    "run_report_generation",
    "run_risk_assessment",
    "run_terrain_analysis",
]
