"""Experiments: studies with a config, a seed, and a reproducible result.

Every endpoint here is **synchronous**, and the request bounds in
``schemas/v3.py`` are sized so that it can be. A 500-trial Monte Carlo or a
20,000-node constraint tree belongs behind a job queue; accepting one on a
request thread would trade a clear 422 for a silent gateway timeout.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from app.models import Experiment
from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas.v3 import (
    ExperimentOut,
    ExperimentSummary,
    FleetPlanRequest,
    MonteCarloRequest,
    RouteStudyRequest,
    ScienceTourRequest,
)
from app.services import autonomy
from app.services.simulation.monte_carlo import Perturbations

router = APIRouter(prefix="/missions", tags=["experiments"])


def _guard(call):
    try:
        return call()
    except autonomy.PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.post("/{mission_id}/route-study", response_model=ExperimentOut)
def route_study(
    payload: RouteStudyRequest,
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
):
    """Sweep the objective weights; return the non-dominated routes.

    A study rather than a plan: the result is the set of trade-offs, and which
    one to fly is a mission decision the API deliberately does not make.
    """
    return _guard(
        lambda: autonomy.run_route_study(
            db=db,
            mission=mission,
            rover_config=autonomy.resolve_rover(db, payload.rover_config_id),
            start=payload.start.model_dump(),
            goal=payload.goal.model_dump(),
            settings=settings,
        )
    )


@router.post("/{mission_id}/monte-carlo", response_model=ExperimentOut)
def monte_carlo_study(
    payload: MonteCarloRequest,
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
):
    """Run the mission repeatedly with the noise resampled.

    The seed is stored on the experiment, so the same row can be re-run and
    compared rather than merely re-read.
    """
    perturbations = Perturbations(
        terrain_sigma=payload.terrain_sigma,
        obstacle_count=payload.unmapped_obstacles,
        energy_factor_sigma=payload.energy_factor_sigma,
    )
    return _guard(
        lambda: autonomy.run_monte_carlo_study(
            db=db,
            mission=mission,
            rover_config=autonomy.resolve_rover(db, payload.rover_config_id),
            start=payload.start.model_dump(),
            goal=payload.goal.model_dump(),
            settings=settings,
            trials=payload.trials,
            seed=payload.seed,
            uncertainty_weight=payload.uncertainty_weight,
            sensor_range_m=payload.sensor_range_m,
            perturbations=perturbations,
        )
    )


@router.post("/{mission_id}/fleet-plan", response_model=ExperimentOut)
def fleet_plan(
    payload: FleetPlanRequest,
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
):
    """Deconflict a heterogeneous fleet with CBS.

    A failure here is a 200 with ``status: FAILED``, not an HTTP error: "these
    rovers cannot be deconflicted within this budget" is a mission finding worth
    storing and looking at, not a broken request.
    """
    assignments = [
        {
            "label": item.label,
            "rover_config": autonomy.resolve_rover(db, item.rover_config_id),
            "start": item.start.model_dump(),
            "goal": item.goal.model_dump(),
        }
        for item in payload.assignments
    ]
    return _guard(
        lambda: autonomy.run_fleet_plan(
            db=db,
            mission=mission,
            assignments=assignments,
            settings=settings,
            max_nodes=payload.max_high_level_nodes,
            time_budget_seconds=payload.time_budget_seconds,
            coordination_max_dim=payload.coordination_max_dim,
        )
    )


@router.post("/{mission_id}/science-tour", response_model=ExperimentOut)
def science_tour(
    payload: ScienceTourRequest,
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
):
    """Select and order this mission's science targets under budget."""
    return _guard(
        lambda: autonomy.run_science_tour(
            db=db,
            mission=mission,
            rover_config=autonomy.resolve_rover(db, payload.rover_config_id),
            start=payload.start.model_dump(),
            settings=settings,
            energy_budget_kwh=payload.energy_budget_kwh,
            time_budget_seconds=payload.time_budget_seconds,
            instruments=payload.instruments,
            relay_at=payload.relay_at.model_dump() if payload.relay_at else None,
        )
    )


@router.get("/{mission_id}/experiments", response_model=list[ExperimentSummary])
def list_experiments(
    mission: CurrentMission, kind: str | None = Query(default=None)
) -> list[Experiment]:
    rows = list(mission.experiments)
    return [row for row in rows if kind is None or row.kind == kind]


@router.get("/{mission_id}/experiments/{experiment_id}", response_model=ExperimentOut)
def get_experiment(experiment_id: uuid.UUID, mission: CurrentMission, db: DbSession) -> Experiment:
    row = db.get(Experiment, experiment_id)
    if row is None or row.mission_id != mission.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Experiment {experiment_id} does not belong to this mission.",
        )
    return row
