import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base
from app.enums import MissionStatus


class Mission(Base):
    __tablename__ = "missions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    terrain_image_path: Mapped[str] = mapped_column(String(500), nullable=False)
    terrain_source: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default=MissionStatus.PENDING)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    terrain_analysis: Mapped["TerrainAnalysis | None"] = relationship(
        back_populates="mission", uselist=False, cascade="all, delete-orphan"
    )
    paths: Mapped[list["RoverPath"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", order_by="RoverPath.planned_at"
    )
    reports: Mapped[list["MissionRiskReport"]] = relationship(
        back_populates="mission", cascade="all, delete-orphan", order_by="MissionRiskReport.generated_at"
    )


class TerrainAnalysis(Base):
    __tablename__ = "terrain_analyses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"), nullable=False)
    slope_map_path: Mapped[str | None] = mapped_column(String(500))
    obstacle_contours: Mapped[list | None] = mapped_column(JSONB)
    terrain_classification: Mapped[str | None] = mapped_column(String(50))
    hazard_heatmap_path: Mapped[str | None] = mapped_column(String(500))
    # Auditability: the exact parameters + aggregate statistics behind this run.
    analysis_metadata: Mapped[dict | None] = mapped_column(JSONB)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mission: Mapped[Mission] = relationship(back_populates="terrain_analysis")
