"""Science targets: the objectives a mission is actually for."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.models import ScienceTarget
from app.routers.deps import CurrentMission, DbSession
from app.schemas.v3 import ScienceTargetCreate, ScienceTargetOut

router = APIRouter(prefix="/missions", tags=["science"])


@router.post(
    "/{mission_id}/science-targets",
    response_model=ScienceTargetOut,
    status_code=status.HTTP_201_CREATED,
)
def create_science_target(
    payload: ScienceTargetCreate, mission: CurrentMission, db: DbSession
) -> ScienceTarget:
    """Add a target in original-image pixel coordinates.

    Deliberately not validated against the terrain here. A target on
    untraversable ground is a legitimate thing to record - the tour planner
    reports it as ``target_on_lethal_terrain`` rather than refusing it, which
    is the answer an operator needs.
    """
    row = ScienceTarget(mission_id=mission.id, **payload.model_dump())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/{mission_id}/science-targets", response_model=list[ScienceTargetOut])
def list_science_targets(mission: CurrentMission) -> list[ScienceTarget]:
    return list(mission.science_targets)


@router.delete("/{mission_id}/science-targets/{target_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_science_target(target_id: uuid.UUID, mission: CurrentMission, db: DbSession) -> None:
    row = db.get(ScienceTarget, target_id)
    if row is None or row.mission_id != mission.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Science target {target_id} does not belong to this mission.",
        )
    db.delete(row)
    db.commit()
