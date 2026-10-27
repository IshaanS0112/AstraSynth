"""Background jobs: submit, watch, cancel."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.enums import JobStatus
from app.models import Job
from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas.v3 import (
    FleetPlanRequest,
    JobOut,
    JobSummary,
    MonteCarloJobRequest,
    QueueStats,
    RouteStudyRequest,
    ScienceTourRequest,
)
from app.services import jobs as queue

router = APIRouter(tags=["jobs"])


def _submit(db: DbSession, mission, kind: str, payload: dict) -> Job:
    return queue.enqueue(db, mission, kind, payload)


@router.post(
    "/missions/{mission_id}/monte-carlo/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_monte_carlo(payload: MonteCarloJobRequest, mission: CurrentMission, db: DbSession):
    """Queue a robustness study. 202 with a job, not 200 with a result."""
    return _submit(db, mission, "monte_carlo", payload.model_dump(mode="json"))


@router.post(
    "/missions/{mission_id}/route-study/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_route_study(payload: RouteStudyRequest, mission: CurrentMission, db: DbSession):
    return _submit(db, mission, "route_study", payload.model_dump(mode="json"))


@router.post(
    "/missions/{mission_id}/fleet-plan/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_fleet_plan(payload: FleetPlanRequest, mission: CurrentMission, db: DbSession):
    return _submit(db, mission, "fleet_plan", payload.model_dump(mode="json"))


@router.post(
    "/missions/{mission_id}/science-tour/jobs",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_science_tour(payload: ScienceTourRequest, mission: CurrentMission, db: DbSession):
    return _submit(db, mission, "science_tour", payload.model_dump(mode="json"))


@router.get("/missions/{mission_id}/jobs", response_model=list[JobSummary])
def list_jobs(
    mission: CurrentMission, status_filter: str | None = Query(default=None, alias="status")
) -> list[Job]:
    rows = list(mission.jobs)
    return [row for row in rows if status_filter is None or row.status == status_filter]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: DbSession) -> Job:
    """Poll one job."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    return job


@router.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: uuid.UUID, db: DbSession) -> Job:
    """Ask a job to stop."""
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Job {job_id} not found")
    if not queue.request_cancel(db, job):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job {job_id} already finished with status {job.status}.",
        )
    return job


@router.get("/jobs", response_model=QueueStats)
def queue_stats(db: DbSession, settings: AppSettings) -> QueueStats:
    """Queue depth, and whether anything is consuming it."""
    oldest = db.execute(
        select(Job.created_at)
        .where(Job.status == JobStatus.QUEUED)
        .order_by(Job.created_at)
        .limit(1)
    ).scalar_one_or_none()
    return QueueStats(
        by_status=queue.queue_depth(db),
        in_process_workers=settings.worker_threads,
        oldest_queued_seconds=(
            round((queue.utcnow() - oldest).total_seconds(), 1) if oldest else None
        ),
    )
