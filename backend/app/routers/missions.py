from __future__ import annotations

import shutil
import uuid
from pathlib import Path as FilePath

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from app.enums import MissionStatus
from app.models import Mission
from app.routers.deps import AppSettings, CurrentMission, DbSession
from app.schemas import MissionOut

router = APIRouter(prefix="/missions", tags=["missions"])

ALLOWED_SUFFIXES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


@router.post("", response_model=MissionOut, status_code=status.HTTP_201_CREATED)
def create_mission(
    db: DbSession,
    settings: AppSettings,
    name: str = Form(..., min_length=1, max_length=200),
    terrain_source: str | None = Form(None),
    terrain_image: UploadFile = File(...),
) -> Mission:
    """Create a mission from an uploaded terrain image.

    The extension is checked against an allowlist and the file is written under
    a generated UUID rather than its client-supplied name, so a hostile filename
    cannot escape the storage directory.
    """
    suffix = FilePath(terrain_image.filename or "").suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported terrain image type '{suffix}'. Allowed: {sorted(ALLOWED_SUFFIXES)}",
        )

    mission_id = uuid.uuid4()
    directory = settings.storage_dir / str(mission_id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"terrain{suffix}"

    with destination.open("wb") as handle:
        written = 0
        while chunk := terrain_image.file.read(1024 * 1024):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                handle.close()
                shutil.rmtree(directory, ignore_errors=True)
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=f"Terrain image exceeds {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                )
            handle.write(chunk)

    mission = Mission(
        id=mission_id,
        name=name,
        terrain_image_path=str(destination),
        terrain_source=terrain_source,
        status=MissionStatus.PENDING,
    )
    db.add(mission)
    db.commit()
    db.refresh(mission)
    return mission


@router.get("", response_model=list[MissionOut])
def list_missions(db: DbSession, limit: int = 50, offset: int = 0) -> list[Mission]:
    from sqlalchemy import select

    stmt = (
        select(Mission)
        .order_by(Mission.created_at.desc())
        .limit(min(limit, 200))
        .offset(offset)
    )
    return list(db.scalars(stmt))


@router.get("/{mission_id}", response_model=MissionOut)
def get_mission_detail(mission: CurrentMission) -> Mission:
    return mission
