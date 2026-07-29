import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class MissionRiskReport(Base):
    __tablename__ = "mission_risk_reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mission_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("missions.id"), nullable=False)
    rover_path_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rover_paths.id"), nullable=False)
    risk_score: Mapped[str | None] = mapped_column(String(20))
    feasibility: Mapped[str | None] = mapped_column(String(30))
    # Everything the deterministic engines computed, frozen before any LLM call.
    structured_context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    ai_narrative: Mapped[dict | None] = mapped_column(JSONB)
    narrative_source: Mapped[str | None] = mapped_column(String(20))  # llm | fallback
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    mission: Mapped["Mission"] = relationship(back_populates="reports")
