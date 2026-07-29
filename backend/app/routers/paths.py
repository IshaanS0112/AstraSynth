from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.models import RoverConfig
from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas import PlanPathRequest, RoverPathOut
from app.services import mission_pipeline
from app.services.path_planner import PathNotFoundError

router = APIRouter(prefix="/missions", tags=["paths"])


@router.post("/{mission_id}/plan-path", response_model=RoverPathOut)
def plan_path(
    payload: PlanPathRequest,
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
):
    rover_config = db.get(RoverConfig, payload.rover_config_id)
    if rover_config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Rover config {payload.rover_config_id} not found",
        )
    try:
        return mission_pipeline.run_path_planning(
            db=db,
            mission=mission,
            rover_config=rover_config,
            start=payload.start.model_dump(),
            end=payload.end.model_dump(),
            settings=settings,
        )
    except PathNotFoundError as exc:
        # 422, not 500: the request was well-formed, the terrain is the problem.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "no_traversable_path", "message": str(exc)},
        ) from exc
    except mission_pipeline.PipelineError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/{mission_id}/path", response_model=RoverPathOut)
def get_path(mission: CurrentMission):
    if not mission.paths:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No path has been planned for this mission yet.",
        )
    return mission.paths[-1]


@router.get("/{mission_id}/paths", response_model=list[RoverPathOut])
def list_paths(mission: CurrentMission):
    return mission.paths
