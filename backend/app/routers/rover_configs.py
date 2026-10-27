from __future__ import annotations

from fastapi import APIRouter, status
from sqlalchemy import select

from app.models import RoverConfig
from app.routers.deps import DbSession
from app.schemas import RoverConfigCreate, RoverConfigOut

router = APIRouter(prefix="/rover-configs", tags=["rover-configs"])


@router.get("", response_model=list[RoverConfigOut])
def list_rover_configs(db: DbSession) -> list[RoverConfig]:
    return list(db.scalars(select(RoverConfig).order_by(RoverConfig.name)))


@router.post("", response_model=RoverConfigOut, status_code=status.HTTP_201_CREATED)
def create_rover_config(payload: RoverConfigCreate, db: DbSession) -> RoverConfig:
    config = RoverConfig(**payload.model_dump())
    db.add(config)
    db.commit()
    db.refresh(config)
    return config
