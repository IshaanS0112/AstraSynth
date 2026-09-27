"""Durable background work.

Why a table and not a queue server
-----------------------------------
This is a research platform, not a fleet of services. Adding Redis or RabbitMQ
would add an operational dependency, a second place for state to live, and a
second thing to get wrong in a deployment - to buy throughput this workload does
not need. A Monte Carlo study is measured in seconds and arrives a few at a time.

PostgreSQL already holds every other durable fact here, and
``SELECT ... FOR UPDATE SKIP LOCKED`` is a correct multi-consumer queue: each
worker locks a different row and none of them block. The pattern is
well-understood, survives a restart because the rows are durable, and is exactly
as transactional as the results the jobs write.

The lease
---------
A worker that dies mid-job would otherwise leave its row ``RUNNING`` forever. So
a claim is a **lease**: the worker stamps ``heartbeat_at`` as it works, and a job
whose heartbeat has gone stale is returned to the queue by the next worker that
looks. That makes delivery *at-least-once* rather than exactly-once, which is
only safe because these jobs are deterministic in their seed - re-running one
produces the same result rather than a second, different one.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.enums import JobStatus

if TYPE_CHECKING:  # avoids a circular import at runtime
    from app.models.mission import Mission


class Job(Base):
    """One unit of background work, its lease, and its outcome."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Matches the experiment kinds: monte_carlo | route_study | fleet_plan | science_tour
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED, index=True
    )

    # The request, verbatim, so a job can be re-run or inspected without the
    # caller who submitted it.
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # 0-1. Written by the worker as it goes; the only field a client polls for.
    progress: Mapped[float] = mapped_column(nullable=False, default=0.0)
    progress_detail: Mapped[str | None] = mapped_column(String(200))

    # Where the answer ended up. Nullable because a job can fail before it has one.
    experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL")
    )
    error: Mapped[str | None] = mapped_column(Text)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    # Identifies the process holding the lease, for diagnosing a stuck queue.
    claimed_by: Mapped[str | None] = mapped_column(String(80))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    mission: Mapped["Mission"] = relationship(back_populates="jobs")

    @property
    def terminal(self) -> bool:
        return self.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}
