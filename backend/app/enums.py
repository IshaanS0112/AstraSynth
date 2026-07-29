"""Domain enumerations.

``str, Enum`` rather than ``StrEnum`` so the backend runs on Python 3.10 as well
as 3.11+. Values are always accessed via ``.value`` when serialising, so the
3.11 change to ``__format__`` is not a hazard either way.
"""

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


class Feasibility(str, Enum):
    FEASIBLE = "FEASIBLE"
    FEASIBLE_WITH_MARGIN = "FEASIBLE_WITH_MARGIN"
    INFEASIBLE = "INFEASIBLE"
