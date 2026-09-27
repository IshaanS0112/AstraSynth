"""Request and response models for the mission-autonomy endpoints.

Bounds are on the schema rather than in the handlers. A Monte Carlo study with
a million trials or a sensor with a negative range is a bad request, not a
server error, and pydantic answers it with a 422 before any engine is touched.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import Point


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- science targets --------------------------------------------------------


class ScienceTargetCreate(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    value: float = Field(default=0.5, ge=0.0, le=1.0)
    priority: Literal["low", "medium", "high"] = "medium"
    observation_seconds: float = Field(default=1200.0, gt=0, le=86400.0)
    required_instrument: str | None = Field(default=None, max_length=40)


class ScienceTargetOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    label: str
    x: int
    y: int
    value: float
    priority: str
    observation_seconds: float
    required_instrument: str | None
    created_at: datetime


# --- terrain grid -----------------------------------------------------------


class TerrainGridOut(BaseModel):
    """Per-cell layers for the 3-D view. Every layer is the same shape."""

    rows: int
    cols: int
    pixel_scale: float
    meters_per_cell: float
    elevation_range_m: float
    lethal_hazard_threshold: float
    layers: dict[str, list[list[float]]]
    ranges: dict[str, list[float]]


# --- traverse ---------------------------------------------------------------


class TraverseRequest(BaseModel):
    start: Point
    goal: Point
    rover_config_id: uuid.UUID
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    # 0 reproduces V1: the planner searches the hazard mean and ignores how well
    # that mean is known.
    uncertainty_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    sensor_range_m: float | None = Field(default=None, gt=0, le=10_000.0)
    sensor_noise_sigma: float = Field(default=0.05, gt=0, le=0.5)
    unmapped_obstacles: int = Field(default=6, ge=0, le=200)
    obstacle_radius_cells: int = Field(default=1, ge=1, le=10)
    terrain_sigma: float = Field(default=0.02, ge=0.0, le=0.5)
    obstacles_on_route: bool = True
    energy_budget_kwh: float | None = Field(default=None, gt=0)


class TraverseRunOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    rover_config_id: uuid.UUID
    status: str
    failure_mode: str | None
    reason: str | None
    start_point: dict[str, Any]
    goal_point: dict[str, Any]
    parameters: dict[str, Any]
    distance_m: float
    energy_kwh: float
    elapsed_seconds: float
    replans: int
    reroutes: int
    repair_expansions: int
    initial_plan: list[dict[str, Any]]
    executed_path: list[dict[str, Any]]
    events: list[dict[str, Any]]
    belief_summary: dict[str, Any]
    created_at: datetime


class TraverseRunSummary(ORMModel):
    """The list view. Omits the paths and the event log, which dominate the size."""

    id: uuid.UUID
    status: str
    failure_mode: str | None
    distance_m: float
    energy_kwh: float
    elapsed_seconds: float
    replans: int
    reroutes: int
    repair_expansions: int
    created_at: datetime


# --- experiments ------------------------------------------------------------


class RouteStudyRequest(BaseModel):
    start: Point
    goal: Point
    rover_config_id: uuid.UUID


class MonteCarloRequest(BaseModel):
    start: Point
    goal: Point
    rover_config_id: uuid.UUID
    # Capped because this is synchronous. A thousand-trial study belongs in a
    # background worker, and pretending otherwise would just time the request out.
    trials: int = Field(default=50, ge=1, le=500)
    seed: int = Field(default=0, ge=0, le=2**31 - 1)
    uncertainty_weight: float = Field(default=0.0, ge=0.0, le=10.0)
    sensor_range_m: float | None = Field(default=None, gt=0, le=10_000.0)
    terrain_sigma: float = Field(default=0.08, ge=0.0, le=0.5)
    unmapped_obstacles: int = Field(default=12, ge=0, le=200)
    energy_factor_sigma: float = Field(default=0.12, ge=0.0, le=1.0)


class FleetAssignment(BaseModel):
    label: str | None = Field(default=None, max_length=40)
    rover_config_id: uuid.UUID
    start: Point
    goal: Point


class FleetPlanRequest(BaseModel):
    assignments: list[FleetAssignment] = Field(min_length=1, max_length=8)
    max_high_level_nodes: int = Field(default=600, ge=1, le=20_000)
    time_budget_seconds: float = Field(default=30.0, gt=0, le=300.0)
    coordination_max_dim: int | None = Field(default=None, ge=8, le=512)


class ScienceTourRequest(BaseModel):
    start: Point
    rover_config_id: uuid.UUID
    energy_budget_kwh: float | None = Field(default=None, gt=0)
    time_budget_seconds: float = Field(default=4 * 88775.0, gt=0)
    instruments: list[str] = Field(default_factory=list, max_length=20)
    relay_at: Point | None = None


# --- background jobs --------------------------------------------------------


class MonteCarloJobRequest(MonteCarloRequest):
    """A Monte Carlo study submitted to the queue rather than run inline.

    The trial cap is two orders of magnitude higher than the synchronous one for
    a simple reason: nothing is waiting on an HTTP connection, so the limit is
    what the machine can finish rather than what a proxy will hold open.
    """

    trials: int = Field(default=200, ge=1, le=5000)
    workers: int | None = Field(default=None, ge=1, le=64)


class JobOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    kind: str
    status: str
    progress: float
    progress_detail: str | None
    experiment_id: uuid.UUID | None
    error: str | None
    attempts: int
    max_attempts: int
    claimed_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobSummary(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    progress: float
    progress_detail: str | None
    experiment_id: uuid.UUID | None
    created_at: datetime
    finished_at: datetime | None


class QueueStats(BaseModel):
    """Depth by status, plus whether anything is actually consuming the queue."""

    by_status: dict[str, int]
    in_process_workers: int
    oldest_queued_seconds: float | None


class ExperimentOut(ORMModel):
    id: uuid.UUID
    mission_id: uuid.UUID
    kind: str
    status: str
    failure_mode: str | None
    reason: str | None
    parameters: dict[str, Any]
    seed: int | None
    code_revision: str | None
    result: dict[str, Any]
    runtime_seconds: float
    created_at: datetime


class ExperimentSummary(ORMModel):
    id: uuid.UUID
    kind: str
    status: str
    failure_mode: str | None
    seed: int | None
    code_revision: str | None
    runtime_seconds: float
    created_at: datetime
