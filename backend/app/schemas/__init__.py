from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

from app.config import get_settings


def static_url(path: str | None) -> str | None:
    """Map an absolute file path under ``storage_dir`` to its served URL.

    Absolute paths are what the CV stage needs on disk, but they must never
    reach the client. Anything outside ``storage_dir`` resolves to ``None``
    rather than leaking a filesystem location.
    """
    if not path:
        return None
    try:
        relative = Path(path).resolve().relative_to(get_settings().storage_dir.resolve())
    except (ValueError, OSError):
        return None
    return f"/static/{relative.as_posix()}"


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Rover configs ----------------------------------------------------------


class RoverConfigCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    battery_capacity_kwh: float = Field(gt=0)
    max_traversable_slope_deg: float = Field(gt=0, le=89)
    energy_per_meter_kwh: float = Field(gt=0)


class RoverConfigOut(ORMModel):
    id: uuid.UUID
    name: str
    battery_capacity_kwh: float
    max_traversable_slope_deg: float
    energy_per_meter_kwh: float


# --- Missions ---------------------------------------------------------------


class MissionOut(ORMModel):
    id: uuid.UUID
    name: str
    terrain_image_path: str = Field(exclude=True)
    terrain_source: str | None
    status: str
    created_at: datetime

    @computed_field
    @property
    def terrain_image_url(self) -> str | None:
        return static_url(self.terrain_image_path)


# --- Terrain ----------------------------------------------------------------


class TerrainAnalysisOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    slope_map_path: str | None = Field(default=None, exclude=True)
    hazard_heatmap_path: str | None = Field(default=None, exclude=True)
    terrain_classification: str | None
    obstacle_contours: list[dict[str, Any]] | None
    analysis_metadata: dict[str, Any] | None
    analyzed_at: datetime

    @field_validator("analysis_metadata")
    @classmethod
    def strip_internal_paths(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        """``arrays_path`` is a server filesystem location - never send it out."""
        if not value:
            return value
        return {k: v for k, v in value.items() if k != "arrays_path"}

    @computed_field
    @property
    def slope_map_url(self) -> str | None:
        return static_url(self.slope_map_path)

    @computed_field
    @property
    def hazard_heatmap_url(self) -> str | None:
        return static_url(self.hazard_heatmap_path)


# --- Paths ------------------------------------------------------------------


class Point(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)


class PlanPathRequest(BaseModel):
    start: Point
    end: Point
    rover_config_id: uuid.UUID


class RoverPathOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    rover_config_id: uuid.UUID
    start_point: dict[str, Any]
    end_point: dict[str, Any]
    waypoints: list[dict[str, Any]]
    total_distance_m: float | None
    total_energy_cost_kwh: float | None
    algorithm_used: str
    planner_metadata: dict[str, Any] | None
    planned_at: datetime


# --- Risk / reports ---------------------------------------------------------


class AssessRiskRequest(BaseModel):
    rover_path_id: uuid.UUID | None = None  # defaults to the mission's latest path


class RiskReportOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    rover_path_id: uuid.UUID
    risk_score: str | None
    feasibility: str | None
    structured_context: dict[str, Any]
    ai_narrative: dict[str, Any] | None
    narrative_source: str | None
    generated_at: datetime
