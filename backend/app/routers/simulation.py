"""Terrain layers for the 3-D view, and traverse simulation."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.models import TraverseRun
from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas.v3 import (
    TerrainGridOut,
    TraverseRequest,
    TraverseRunOut,
    TraverseRunSummary,
)
from app.services import autonomy

router = APIRouter(prefix="/missions", tags=["simulation"])


@router.get("/{mission_id}/terrain-grid", response_model=TerrainGridOut)
def get_terrain_grid(
    mission: CurrentMission,
    settings: AppSettings,
    max_dim: int = Query(default=128, ge=16, le=256),
):
    """Every per-cell layer the mission-control view renders, in one response."""
    try:
        return autonomy.terrain_payload(mission, settings, max_dim=max_dim)
    except autonomy.PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/{mission_id}/simulate-traverse", response_model=TraverseRunOut)
def simulate_traverse(
    payload: TraverseRequest,
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
):
    """Drive from start to goal without knowing the terrain in advance."""
    try:
        rover_config = autonomy.resolve_rover(db, payload.rover_config_id)
        return autonomy.run_traverse(
            db=db,
            mission=mission,
            rover_config=rover_config,
            start=payload.start.model_dump(),
            goal=payload.goal.model_dump(),
            settings=settings,
            seed=payload.seed,
            sensor_range_m=payload.sensor_range_m,
            sensor_noise_sigma=payload.sensor_noise_sigma,
            uncertainty_weight=payload.uncertainty_weight,
            unmapped_obstacles=payload.unmapped_obstacles,
            obstacle_radius_cells=payload.obstacle_radius_cells,
            terrain_sigma=payload.terrain_sigma,
            energy_budget_kwh=payload.energy_budget_kwh,
            obstacles_on_route=payload.obstacles_on_route,
        )
    except autonomy.PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/{mission_id}/traverses", response_model=list[TraverseRunSummary])
def list_traverses(mission: CurrentMission) -> list[TraverseRun]:
    return list(mission.traverse_runs)


@router.get("/{mission_id}/traverses/{run_id}", response_model=TraverseRunOut)
def get_traverse(run_id: uuid.UUID, mission: CurrentMission, db: DbSession) -> TraverseRun:
    row = db.get(TraverseRun, run_id)
    if row is None or row.mission_id != mission.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Traverse run {run_id} does not belong to this mission.",
        )
    return row


@router.get("/{mission_id}/traverses/{run_id}/events")
def get_traverse_events(
    run_id: uuid.UUID,
    mission: CurrentMission,
    db: DbSession,
    category: str | None = Query(default=None),
    kind: str | None = Query(default=None),
) -> list[dict]:
    """The mission event log, optionally filtered."""
    row = db.get(TraverseRun, run_id)
    if row is None or row.mission_id != mission.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Traverse run {run_id} does not belong to this mission.",
        )
    events = row.events or []
    if category:
        events = [e for e in events if e.get("category") == category]
    if kind:
        events = [e for e in events if e.get("kind") == kind]
    return events
