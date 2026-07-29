"""Shared router dependencies."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import get_db
from app.models import Mission

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def get_mission(
    mission_id: Annotated[uuid.UUID, Path()],
    db: DbSession,
) -> Mission:
    mission = db.get(Mission, mission_id)
    if mission is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Mission {mission_id} not found"
        )
    return mission


CurrentMission = Annotated[Mission, Depends(get_mission)]
