import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:  # avoids a circular import at runtime
    from app.models.mission import Mission


class RoverPath(Base):
    __tablename__ = "rover_paths"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"), nullable=False)
    rover_config_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("rover_configs.id"), nullable=False
    )
    start_point: Mapped[dict] = mapped_column(JSONB, nullable=False)
    end_point: Mapped[dict] = mapped_column(JSONB, nullable=False)
    waypoints: Mapped[list] = mapped_column(JSONB, nullable=False)
    total_distance_m: Mapped[float | None] = mapped_column(Float)
    total_energy_cost_kwh: Mapped[float | None] = mapped_column(Float)
    algorithm_used: Mapped[str] = mapped_column(String(30), default="A_star")
    # Nodes expanded, grid size, cost-function constants -> lets the A* claim be checked.
    planner_metadata: Mapped[dict | None] = mapped_column(JSONB)
    planned_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="paths")
