"""The queue: claiming, leasing, finishing, and reclaiming background work.

The claim is ONE statement, not two - "read the oldest queued row, then update
it" is a race that runs the job twice. ``FOR UPDATE`` locks the read and
``SKIP LOCKED`` sends a second worker to the next row instead of making it wait,
which gives a multi-consumer queue with no broker and no coordination.

Delivery is therefore at-least-once: a worker can die holding a lease, and the
next one re-runs the job. That is safe only because every job kind is
deterministic in its recorded seed. A non-deterministic kind would need an
idempotency key first.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.enums import JobStatus
from app.models import Job, Mission

# How long a claim survives without a heartbeat before another worker may take
# it: long enough that a slow trial is not mistaken for a crash.
DEFAULT_LEASE_SECONDS = 90.0


def utcnow() -> datetime:
    """Naive UTC, to match the naive ``DateTime`` columns these values go into.

    Not ``utcnow()``: deprecated from 3.12, and this suite turns a
    DeprecationWarning into an error, so on 3.12+ every lease write raised.
    Returns exactly what ``utcnow()`` returned - making the columns timezone-aware
    would be the better model, but that is a migration and a wire-format change,
    not a fix for this.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


def enqueue(db: Session, mission: Mission, kind: str, payload: dict, max_attempts: int = 2) -> Job:
    job = Job(
        mission_id=mission.id,
        kind=kind,
        status=JobStatus.QUEUED,
        payload=payload,
        max_attempts=max_attempts,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim(db: Session, worker_id: str) -> Job | None:
    """Take the oldest queued job, atomically. ``None`` if the queue is empty."""
    claimed = db.execute(
        text(
            """
            UPDATE jobs
               SET status = :running,
                   claimed_by = :worker,
                   started_at = COALESCE(started_at, now()),
                   heartbeat_at = now(),
                   attempts = attempts + 1
             WHERE id = (
                   SELECT id FROM jobs
                    WHERE status = :queued
                    ORDER BY created_at
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1
             )
         RETURNING id
            """
        ),
        {"running": JobStatus.RUNNING.value, "queued": JobStatus.QUEUED.value, "worker": worker_id},
    ).scalar_one_or_none()
    db.commit()
    if claimed is None:
        return None

    # The claim is a raw UPDATE and the session uses expire_on_commit=False, so
    # without this refresh the worker reads QUEUED on a job it just claimed.
    job = db.get(Job, claimed)
    db.refresh(job)
    return job


def heartbeat(
    db: Session, job_id: uuid.UUID, progress: float | None = None, detail: str | None = None
) -> bool:
    """Renew the lease and report progress. ``False`` means stop."""
    values: dict = {"heartbeat_at": utcnow()}
    if progress is not None:
        values["progress"] = max(0.0, min(1.0, progress))
    if detail is not None:
        values["progress_detail"] = detail[:200]

    db.execute(update(Job).where(Job.id == job_id).values(**values))
    db.commit()
    db.expire_all()  # same reason as in claim(): raw writes bypass the identity map

    status = db.execute(select(Job.status).where(Job.id == job_id)).scalar_one_or_none()
    return status == JobStatus.RUNNING


def succeed(db: Session, job: Job, experiment_id: uuid.UUID | None) -> Job:
    job.status = JobStatus.SUCCEEDED
    job.experiment_id = experiment_id
    job.progress = 1.0
    job.finished_at = utcnow()
    job.error = None
    db.commit()
    db.refresh(job)
    return job


def fail(db: Session, job: Job, error: str) -> Job:
    """Record a failure, and re-queue it if the job has attempts left."""
    job.error = error[:4000]
    if job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.claimed_by = None
        job.heartbeat_at = None
        job.progress = 0.0
    else:
        job.status = JobStatus.FAILED
        job.finished_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def finish_cancelled(db: Session, job: Job) -> Job:
    job.status = JobStatus.CANCELLED
    job.finished_at = utcnow()
    db.commit()
    db.refresh(job)
    return job


def request_cancel(db: Session, job: Job) -> bool:
    """Ask a job to stop. ``False`` if it had already finished."""
    if job.terminal:
        return False
    job.status = JobStatus.CANCELLED if job.status == JobStatus.QUEUED else JobStatus.CANCELLING
    if job.status == JobStatus.CANCELLED:
        job.finished_at = utcnow()
    db.commit()
    db.refresh(job)
    return True


def reclaim_stale(db: Session, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> int:
    """Return jobs whose worker stopped heartbeating, and bury the hopeless ones."""
    cutoff = utcnow() - timedelta(seconds=lease_seconds)
    active = (JobStatus.RUNNING.value, JobStatus.CANCELLING.value)

    exhausted = db.execute(
        text(
            """
            UPDATE jobs
               SET status = :failed,
                   finished_at = now(),
                   error = COALESCE(error, 'worker stopped heartbeating; attempts exhausted')
             WHERE status = ANY(:active)
               AND heartbeat_at < :cutoff
               AND attempts >= max_attempts
         RETURNING id
            """
        ),
        {"failed": JobStatus.FAILED.value, "active": list(active), "cutoff": cutoff},
    ).rowcount

    requeued = db.execute(
        text(
            """
            UPDATE jobs
               SET status = :queued,
                   claimed_by = NULL,
                   heartbeat_at = NULL,
                   progress = 0.0
             WHERE status = ANY(:active)
               AND heartbeat_at < :cutoff
               AND attempts < max_attempts
         RETURNING id
            """
        ),
        {"queued": JobStatus.QUEUED.value, "active": list(active), "cutoff": cutoff},
    ).rowcount

    db.commit()
    if exhausted or requeued:
        db.expire_all()
    return int(exhausted) + int(requeued)


def queue_depth(db: Session) -> dict[str, int]:
    """Row counts by status. The one number an operator watching a queue wants."""
    rows = db.execute(select(Job.status, func.count()).group_by(Job.status)).all()
    return {status: int(count) for status, count in rows}
