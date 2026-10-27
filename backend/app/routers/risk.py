from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas import AssessRiskRequest, RiskReportOut
from app.services import mission_pipeline

router = APIRouter(prefix="/missions", tags=["risk"])


@router.post("/{mission_id}/assess-risk", response_model=RiskReportOut)
def assess_risk(
    mission: CurrentMission,
    db: DbSession,
    settings: AppSettings,
    payload: AssessRiskRequest | None = None,
):
    """Deterministic risk + feasibility only. No LLM call happens here."""
    payload = payload or AssessRiskRequest()
    try:
        path_row = mission_pipeline.resolve_path(db, mission, payload.rover_path_id)
        return mission_pipeline.run_risk_assessment(db, mission, path_row, settings)
    except mission_pipeline.PipelineError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/{mission_id}/risk-report", response_model=RiskReportOut)
def get_risk_report(mission: CurrentMission):
    if not mission.reports:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Risk has not been assessed for this mission yet.",
        )
    return mission.reports[-1]
