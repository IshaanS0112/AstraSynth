"""Mission risk assessment.

Two independent judgements, deliberately kept separate:

1. **Risk tier** - a normalised 0-1 blend of path hazard exposure and battery
   draw, bucketed into LOW / MEDIUM / HIGH. Soft, comparative.
2. **Feasibility** - a hard check of total energy against battery capacity.
   Binary engineering constraint, no interpretation involved.

A path can be HIGH risk and still FEASIBLE (short but nasty terrain), or LOW
risk and INFEASIBLE (gentle but far beyond battery range). Collapsing the two
into one score would hide exactly the case a mission planner most needs to see.

Note on the hazard term
-----------------------
The original design summed hazard scores along the path. That sum grows with
path length, so a long safe traverse scores worse than a short lethal one and
nothing maps onto the 0-1 tier thresholds. The length-normalised **mean**
hazard is used instead, with the peak reported separately so a single extreme
segment is not averaged away.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import Settings
from app.enums import Feasibility, RiskTier
from app.services.path_planner import PlannedPath, RoverSpec


@dataclass(slots=True)
class RiskAssessment:
    risk_score: float
    risk_tier: RiskTier
    feasibility: Feasibility
    energy_margin_kwh: float
    energy_utilisation: float
    mean_path_hazard: float
    max_path_hazard: float
    high_hazard_segments: list[dict]
    max_slope_encountered_deg: float
    calculation_basis: dict = field(default_factory=dict)


def assess_feasibility(
    total_energy_kwh: float, battery_capacity_kwh: float, margin_fraction: float
) -> Feasibility:
    """Battery arithmetic. No model, no heuristic, no LLM."""
    if battery_capacity_kwh <= 0:
        raise ValueError("battery_capacity_kwh must be positive")
    if total_energy_kwh > battery_capacity_kwh:
        return Feasibility.INFEASIBLE
    if total_energy_kwh > margin_fraction * battery_capacity_kwh:
        return Feasibility.FEASIBLE_WITH_MARGIN
    return Feasibility.FEASIBLE


def tier_for_score(score: float, low: float, medium: float) -> RiskTier:
    if score < low:
        return RiskTier.LOW
    if score <= medium:
        return RiskTier.MEDIUM
    return RiskTier.HIGH


def top_hazard_segments(path: PlannedPath, limit: int = 5) -> list[dict]:
    """The ``limit`` highest-hazard waypoints, worst first.

    These segment IDs are the only ones the report generator is permitted to
    cite, which is what stops the narrative referencing a segment that does not
    exist.
    """
    ranked = sorted(path.waypoints, key=lambda w: w.hazard_score, reverse=True)[:limit]
    return [
        {
            "segment_id": w.segment_id,
            "hazard_score": w.hazard_score,
            "slope_deg": w.slope_deg,
            "x": w.x,
            "y": w.y,
            "cumulative_energy_kwh": round(w.cumulative_energy_kwh, 4),
        }
        for w in ranked
    ]


def assess_mission_risk(path: PlannedPath, rover: RoverSpec, settings: Settings) -> RiskAssessment:
    hazards = path.hazard_series()
    if not hazards:
        raise ValueError("Cannot assess risk for an empty path")

    mean_hazard = sum(hazards) / len(hazards)
    max_hazard = max(hazards)
    energy_utilisation = path.total_energy_cost_kwh / rover.battery_capacity_kwh

    # Energy utilisation can exceed 1 on an infeasible plan; clamping keeps the
    # risk score inside [0, 1] so the tier thresholds stay meaningful. The
    # unclamped value is reported separately and drives the feasibility verdict.
    risk_score = settings.risk_weight_hazard * mean_hazard + settings.risk_weight_energy * min(
        energy_utilisation, 1.0
    )

    tier = tier_for_score(risk_score, settings.risk_threshold_low, settings.risk_threshold_medium)
    feasibility = assess_feasibility(
        path.total_energy_cost_kwh,
        rover.battery_capacity_kwh,
        settings.energy_margin_fraction,
    )

    return RiskAssessment(
        risk_score=round(risk_score, 4),
        risk_tier=tier,
        feasibility=feasibility,
        energy_margin_kwh=round(rover.battery_capacity_kwh - path.total_energy_cost_kwh, 4),
        energy_utilisation=round(energy_utilisation, 4),
        mean_path_hazard=round(mean_hazard, 4),
        max_path_hazard=round(max_hazard, 4),
        high_hazard_segments=top_hazard_segments(path),
        max_slope_encountered_deg=round(max(abs(w.slope_deg) for w in path.waypoints), 3),
        calculation_basis={
            "risk_formula": (
                "risk = w_hazard * mean(hazard along path) "
                "+ w_energy * min(total_energy / battery_capacity, 1)"
            ),
            "weights": {
                "hazard": settings.risk_weight_hazard,
                "energy": settings.risk_weight_energy,
            },
            "tier_thresholds": {
                "LOW": f"< {settings.risk_threshold_low}",
                "MEDIUM": f"{settings.risk_threshold_low} - {settings.risk_threshold_medium}",
                "HIGH": f"> {settings.risk_threshold_medium}",
            },
            "feasibility_rule": (
                "INFEASIBLE if energy > capacity; FEASIBLE_WITH_MARGIN if energy > "
                f"{settings.energy_margin_fraction} * capacity; else FEASIBLE"
            ),
            "inputs": {
                "total_energy_kwh": path.total_energy_cost_kwh,
                "battery_capacity_kwh": rover.battery_capacity_kwh,
                "waypoint_count": len(path.waypoints),
            },
        },
    )
