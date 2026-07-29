from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas import TerrainAnalysisOut
from app.services import mission_pipeline

router = APIRouter(prefix="/missions", tags=["terrain"])


@router.post("/{mission_id}/analyze-terrain", response_model=TerrainAnalysisOut)
def analyze_terrain(mission: CurrentMission, db: DbSession, settings: AppSettings):
    """Run the OpenCV pipeline. Idempotent - re-running overwrites the analysis."""
    try:
        return mission_pipeline.run_terrain_analysis(db, mission, settings)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Terrain image could not be read: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


@router.get("/{mission_id}/terrain-analysis", response_model=TerrainAnalysisOut)
def get_terrain_analysis(mission: CurrentMission):
    if mission.terrain_analysis is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Terrain has not been analysed for this mission yet.",
        )
    return mission.terrain_analysis
