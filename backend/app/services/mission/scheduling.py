"""The mission as a timeline of activities rather than a line on a map."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ActivityKind(str, Enum):
    TRAVEL = "TRAVEL"
    SCIENCE = "SCIENCE"
    COMMUNICATE = "COMMUNICATE"
    CHARGE = "CHARGE"
    WAIT = "WAIT"
    REPLAN = "REPLAN"


@dataclass(slots=True)
class Activity:
    kind: ActivityKind
    start_seconds: float
    duration_seconds: float
    label: str
    detail: dict = field(default_factory=dict)

    @property
    def end_seconds(self) -> float:
        return self.start_seconds + self.duration_seconds

    def as_dict(self) -> dict:
        return {
            "kind": self.kind.value,
            "start_seconds": round(self.start_seconds, 1),
            "end_seconds": round(self.end_seconds, 1),
            "duration_seconds": round(self.duration_seconds, 1),
            "label": self.label,
            "detail": self.detail,
        }


@dataclass(slots=True)
class Timeline:
    activities: list[Activity] = field(default_factory=list)
    sol_seconds: float = 88775.0  # one Martian solar day

    def add(self, activity: Activity) -> None:
        if self.activities and activity.start_seconds < self.activities[-1].end_seconds - 1e-6:
            raise ValueError(
                f"activity '{activity.label}' starts at {activity.start_seconds:.1f}s, "
                f"before the previous one ends at {self.activities[-1].end_seconds:.1f}s"
            )
        self.activities.append(activity)

    @property
    def end_seconds(self) -> float:
        return self.activities[-1].end_seconds if self.activities else 0.0

    def sol_of(self, t_seconds: float) -> int:
        return int(t_seconds // self.sol_seconds)

    def duration_by_kind(self) -> dict[str, float]:
        totals: dict[str, float] = {}
        for activity in self.activities:
            totals[activity.kind.value] = (
                totals.get(activity.kind.value, 0.0) + activity.duration_seconds
            )
        return {k: round(v, 1) for k, v in totals.items()}

    def as_dict(self) -> dict:
        return {
            "sol_seconds": self.sol_seconds,
            "total_seconds": round(self.end_seconds, 1),
            "total_sols": round(self.end_seconds / self.sol_seconds, 3),
            "duration_by_kind": self.duration_by_kind(),
            "activities": [a.as_dict() for a in self.activities],
        }
