"""The queue: claiming, leasing, finishing, and reclaiming background work.

The claim is one statement
--------------------------
::

    UPDATE jobs SET status = 'RUNNING', ...
    WHERE id = (
        SELECT id FROM jobs WHERE status = 'QUEUED'
        ORDER BY created_at
        FOR UPDATE SKIP LOCKED
        LIMIT 1
    )
    RETURNING id

Not two. The obvious "read the oldest queued row, then update it" is a race:
between the read and the write another worker reads the same row, and the job
runs twice. ``FOR UPDATE`` makes the read take a lock; ``SKIP LOCKED`` is what
stops the second worker from *waiting* on that lock and instead sends it to the
next row. Together they give a multi-consumer queue with no coordination, no
polling storm, and no broker.

Delivery is at-least-once
-------------------------
A worker can die holding a lease. The lease expires, another worker takes the
row, and the job runs a second time. That is tolerable here for a specific
reason rather than by hope: every job kind is deterministic in its recorded
seed, so a re-run produces the same result rather than a second, different one.
A job kind that was not deterministic would need an idempotency key before it
could go through this queue.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.enums import JobStatus
from app.models import Job, Mission

# How long a claim is good for without a heartbeat. Long enough that a slow
# trial does not look like a crash, short enough that a crash is noticed while
# someone is still watching the request that queued it.
DEFAULT_LEASE_SECONDS = 90.0


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

    # The claim is a raw UPDATE, so anything this session already has in its
    # identity map still holds pre-claim values - and the session is configured
    # with expire_on_commit=False, so the commit does not refresh them either.
    # Without this the worker reads status QUEUED on a job it just claimed.
    job = db.get(Job, claimed)
    db.refresh(job)
    return job


def heartbeat(
    db: Session, job_id: uuid.UUID, progress: float | None = None, detail: str | None = None
) -> bool:
    """Renew the lease and report progress. ``False`` means stop.

    The return value is how cancellation reaches a running job: the API writes
    ``CANCELLING`` and the worker learns about it here, at a point where it is
    safe to stop. Killing the thread instead would leave a half-written
    experiment and no way to say which half.
    """
    values: dict = {"heartbeat_at": datetime.utcnow()}
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
    job.finished_at = datetime.utcnow()
    job.error = None
    db.commit()
    db.refresh(job)
    return job


def fail(db: Session, job: Job, error: str) -> Job:
    """Record a failure, and re-queue it if the job has attempts left.

    Retrying is only correct because the work is deterministic; see the module
    docstring. The attempt counter is incremented at claim time, not here, so a
    worker that dies without reaching this function still burns an attempt and a
    permanently-crashing job cannot spin forever.
    """
    job.error = error[:4000]
    if job.attempts < job.max_attempts:
        job.status = JobStatus.QUEUED
        job.claimed_by = None
        job.heartbeat_at = None
        job.progress = 0.0
    else:
        job.status = JobStatus.FAILED
        job.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


def finish_cancelled(db: Session, job: Job) -> Job:
    job.status = JobStatus.CANCELLED
    job.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return job


def request_cancel(db: Session, job: Job) -> bool:
    """Ask a job to stop. ``False`` if it had already finished.

    A queued job is cancelled outright - nothing has started, so there is nothing
    to unwind. A running one only enters ``CANCELLING``; the worker decides when
    it is safe to stop and writes the terminal state itself.
    """
    if job.terminal:
        return False
    job.status = JobStatus.CANCELLED if job.status == JobStatus.QUEUED else JobStatus.CANCELLING
    if job.status == JobStatus.CANCELLED:
        job.finished_at = datetime.utcnow()
    db.commit()
    db.refresh(job)
    return True


def reclaim_stale(db: Session, lease_seconds: float = DEFAULT_LEASE_SECONDS) -> int:
    """Return jobs whose worker stopped heartbeating, and bury the hopeless ones.

    Runs before every claim rather than on a timer: a worker looking for work is
    exactly the moment when noticing abandoned work is useful, and it means the
    recovery path is exercised constantly instead of only during an incident.
    """
    cutoff = datetime.utcnow() - timedelta(seconds=lease_seconds)
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
