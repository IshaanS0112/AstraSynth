"""Structured planning and mission outcomes.

The V1 planner raised ``PathNotFoundError`` with a sentence describing what went
wrong. That is better than returning ``False``, but a sentence is not something
a caller can branch on, and the UI cannot colour a warning amber or red based on
prose.

So every failure here carries three things: a machine-readable
:class:`FailureMode`, a human sentence, and a ``diagnostics`` dictionary with the
measurements behind the verdict. "No safe path" and "battery cannot pay for the
safe path that exists" are different operational situations that need different
responses, and the type system now says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class FailureMode(str, Enum):
    """Why a mission or a plan could not proceed."""

    NO_SAFE_PATH = "NO_SAFE_PATH"  # graph search exhausted; goal unreachable
    EXCESSIVE_SLOPE = "EXCESSIVE_SLOPE"  # every corridor blocked by the slope limit
    LETHAL_TERRAIN = "LETHAL_TERRAIN"  # every corridor blocked by the hazard layer
    LOW_BATTERY = "LOW_BATTERY"  # route exists, energy exceeds capacity
    ROVER_BLOCKED = "ROVER_BLOCKED"  # rover is standing on a cell it cannot leave
    START_IS_GOAL = "START_IS_GOAL"  # degenerate request
    COMMUNICATION_LOSS = "COMMUNICATION_LOSS"  # reserved: no link within the window
    SENSOR_UNCERTAINTY = "SENSOR_UNCERTAINTY"  # belief too uncertain to commit
    TIMEOUT = "TIMEOUT"  # search exceeded its node or iteration budget
    CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"  # CBS could not deconflict the fleet
    MISSION_ABORT = "MISSION_ABORT"  # operator or policy layer stopped it


# Failure modes that describe the terrain or the fleet rather than the rover's
# own state. Split out because the UI groups them differently: a terrain failure
# means "re-plan the objective", a rover failure means "send a different rover".
TERRAIN_FAILURES = frozenset(
    {
        FailureMode.NO_SAFE_PATH,
        FailureMode.EXCESSIVE_SLOPE,
        FailureMode.LETHAL_TERRAIN,
        FailureMode.CONFLICT_UNRESOLVED,
    }
)


@dataclass(slots=True)
class PlanningOutcome:
    """The result of asking for a plan: either a path, or a diagnosed refusal."""

    status: str  # "SUCCESS" | "FAILED"
    failure: FailureMode | None = None
    reason: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)
    path: Any = None  # PlannedPath on success; typed loosely to avoid a cycle

    @property
    def succeeded(self) -> bool:
        return self.status == "SUCCESS"

    @classmethod
    def success(cls, path: Any, **diagnostics: Any) -> PlanningOutcome:
        return cls(status="SUCCESS", path=path, diagnostics=diagnostics)

    @classmethod
    def failed(cls, failure: FailureMode, reason: str, **diagnostics: Any) -> PlanningOutcome:
        return cls(status="FAILED", failure=failure, reason=reason, diagnostics=diagnostics)

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "failure": self.failure.value if self.failure else None,
            "reason": self.reason,
            "diagnostics": self.diagnostics,
        }


def diagnose_search_failure(
    blocked: dict[str, int],
    nodes_expanded: int,
    start: tuple[int, int],
    goal: tuple[int, int],
) -> PlanningOutcome:
    """Turn an exhausted search into the most specific failure it supports.

    Which constraint did the walling-off is the question an operator actually
    asks, and the two counters answer it: a corridor closed almost entirely by
    slope rejections is a rover-capability problem (send the Heavy class), one
    closed by hazard rejections is a terrain problem (the objective needs to
    move). The dominant counter picks the mode; a tie or an empty count falls
    back to the generic NO_SAFE_PATH rather than guessing.
    """
    by_slope = blocked.get("moves_blocked_by_slope_limit", 0)
    by_hazard = blocked.get("moves_blocked_by_lethal_hazard", 0)
    total = by_slope + by_hazard

    if total == 0:
        mode = FailureMode.NO_SAFE_PATH
        reason = (
            f"No route from {start} to {goal}: the search exhausted "
            f"{nodes_expanded} nodes without any edge being rejected, so the goal "
            "is in a region the start cannot reach at all."
        )
    elif by_slope >= 2 * by_hazard:
        mode = FailureMode.EXCESSIVE_SLOPE
        reason = (
            f"No route from {start} to {goal}: {by_slope} candidate moves exceeded "
            f"the rover's slope limit against {by_hazard} blocked by lethal hazard. "
            "A rover with a higher slope limit may find this route."
        )
    elif by_hazard >= 2 * by_slope:
        mode = FailureMode.LETHAL_TERRAIN
        reason = (
            f"No route from {start} to {goal}: {by_hazard} candidate moves were at "
            f"or above the lethal hazard threshold against {by_slope} blocked by "
            "slope. No rover configuration crosses this; the objective must move."
        )
    else:
        mode = FailureMode.NO_SAFE_PATH
        reason = (
            f"No route from {start} to {goal}: blocked roughly equally by slope "
            f"({by_slope} moves) and lethal hazard ({by_hazard} moves)."
        )

    return PlanningOutcome.failed(
        mode,
        reason,
        nodes_expanded=nodes_expanded,
        moves_blocked_by_slope_limit=by_slope,
        moves_blocked_by_lethal_hazard=by_hazard,
        start_cell=list(start),
        goal_cell=list(goal),
    )
