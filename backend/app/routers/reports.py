from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas import RiskReportOut
from app.services import mission_pipeline

router = APIRouter(prefix="/missions", tags=["reports"])


@router.post("/{mission_id}/generate-report", response_model=RiskReportOut)
def generate_report(mission: CurrentMission, db: DbSession, settings: AppSettings):
    """Narrate the stored structured context.

    Never returns 5xx for an LLM problem: if the model call fails, times out, or
    returns malformed JSON, the templated fallback is persisted instead and the
    response carries ``narrative_source = "template_fallback"``.
    """
    if not mission.reports:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Assess risk before generating a narrative report.",
        )
    return mission_pipeline.run_report_generation(db, mission, mission.reports[-1], settings)


@router.get("/{mission_id}/ai-report", response_model=RiskReportOut)
def get_ai_report(mission: CurrentMission):
    if not mission.reports or mission.reports[-1].ai_narrative is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No AI report has been generated for this mission yet.",
        )
    return mission.reports[-1]
