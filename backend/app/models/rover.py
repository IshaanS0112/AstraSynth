import uuid

from sqlalchemy import Float, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class RoverConfig(Base):
    __tablename__ = "rover_configs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    battery_capacity_kwh: Mapped[float] = mapped_column(Float, nullable=False)
    max_traversable_slope_deg: Mapped[float] = mapped_column(Float, nullable=False)
    energy_per_meter_kwh: Mapped[float] = mapped_column(Float, nullable=False)
