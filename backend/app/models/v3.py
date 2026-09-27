"""Persistence for the mission-autonomy layer.

All three tables are **new**, not alterations of existing ones, so the V1
``create_all`` bootstrap still covers the schema. A migration tool becomes
necessary the moment an existing column changes shape; adding tables beside the
old ones is still append-only, and pulling in Alembic before it is needed would
be ceremony rather than engineering. The note in docs/architecture.md stands:
the first column change is what buys Alembic.

``Experiment`` carries provenance - the git revision, the seed, and the exact
parameter set - because a stochastic result that cannot be reproduced is an
anecdote. Everything needed to re-run a study is in its row.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:  # avoids a circular import at runtime
    from app.models.mission import Mission


class ScienceTarget(Base):
    """A place worth stopping at, stored per mission.

    Coordinates are in **original image pixels**, the same frame the UI clicks
    in and the same frame ``RoverPath`` waypoints use. Planning-grid cells are
    derived on demand from the mission's downsample scale, never stored - the
    scale is a property of an analysis run and would go stale here.
    """

    __tablename__ = "science_targets"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    label: Mapped[str] = mapped_column(String(60), nullable=False)
    x: Mapped[int] = mapped_column(Integer, nullable=False)
    y: Mapped[int] = mapped_column(Integer, nullable=False)
    value: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    priority: Mapped[str] = mapped_column(String(10), nullable=False, default="medium")
    observation_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=1200.0)
    required_instrument: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="science_targets")


class TraverseRun(Base):
    """One simulated drive under partial observability.

    The executed path, the initial plan and the full event log are stored as
    JSONB rather than normalised into rows. They are read as a whole, written
    once and never queried field-by-field, so a table per waypoint would buy
    nothing and cost a join per replay.
    """

    __tablename__ = "traverse_runs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rover_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rover_configs.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    failure_mode: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(1000))

    start_point: Mapped[dict] = mapped_column(JSONB, nullable=False)
    goal_point: Mapped[dict] = mapped_column(JSONB, nullable=False)
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)

    distance_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    energy_kwh: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    elapsed_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    replans: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reroutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    repair_expansions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    initial_plan: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    executed_path: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    events: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    belief_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="traverse_runs")


class Experiment(Base):
    """A study with a config, a seed, and a reproducible result.

    One table with a ``kind`` discriminator rather than one per study type. The
    four kinds - Monte Carlo, a Pareto sweep, a fleet deconfliction, a science
    tour - differ entirely in their payloads and not at all in their lifecycle:
    each is a parameter set in, a JSON result out, run once and read many times.
    Four near-identical tables would be four places to add provenance to.
    """

    __tablename__ = "experiments"
    __table_args__ = (Index("ix_experiments_mission_kind", "mission_id", "kind"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("missions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SUCCESS")
    failure_mode: Mapped[str | None] = mapped_column(String(40))
    reason: Mapped[str | None] = mapped_column(String(1000))

    # Everything needed to re-run this exact study.
    parameters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    seed: Mapped[int | None] = mapped_column(Integer)
    code_revision: Mapped[str | None] = mapped_column(String(64))

    result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    runtime_seconds: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="experiments")
