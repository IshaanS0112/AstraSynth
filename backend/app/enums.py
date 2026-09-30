"""Domain enumerations."""

from enum import Enum


class MissionStatus(str, Enum):
    PENDING = "PENDING"
    ANALYZED = "ANALYZED"
    PATH_PLANNED = "PATH_PLANNED"
    RISK_ASSESSED = "RISK_ASSESSED"
    REPORT_GENERATED = "REPORT_GENERATED"


class TerrainClass(str, Enum):
    ROCKY_HIGHLAND = "rocky_highland"
    SANDY_PLAIN = "sandy_plain"
    CRATER_FIELD = "crater_field"


class RiskTier(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class JobStatus(str, Enum):
    """Where a background job is."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCELLING = "CANCELLING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Feasibility(str, Enum):
    FEASIBLE = "FEASIBLE"
    FEASIBLE_WITH_MARGIN = "FEASIBLE_WITH_MARGIN"
    INFEASIBLE = "INFEASIBLE"
