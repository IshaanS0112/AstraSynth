"""Risk tiering and feasibility.

Feasibility is the one verdict in this system with no interpretation in it -
it is battery arithmetic. These tests pin the boundaries exactly, including the
off-by-one at the margin threshold.
"""

from __future__ import annotations

import pytest

from app.enums import Feasibility, RiskTier
from app.services.path_planner import PlannedPath, RoverSpec, Waypoint
from app.services.risk_engine import (
    assess_feasibility,
    assess_mission_risk,
    tier_for_score,
    top_hazard_segments,
)


def make_path(hazards: list[float], energy_kwh: float, distance_m: float = 1000.0) -> PlannedPath:
    waypoints = [
        Waypoint(
            segment_id=index,
            x=index,
            y=index,
            hazard_score=hazard,
            slope_deg=float(index % 7),
            step_distance_m=distance_m / max(len(hazards), 1),
            energy_cost_kwh=energy_kwh / max(len(hazards), 1),
            cumulative_energy_kwh=energy_kwh * (index + 1) / max(len(hazards), 1),
        )
        for index, hazard in enumerate(hazards)
    ]
    return PlannedPath(
        waypoints=waypoints,
        total_distance_m=distance_m,
        total_energy_cost_kwh=energy_kwh,
        total_cost=distance_m,
        metadata={"algorithm": "A_star", "nodes_expanded": 100},
    )


class TestFeasibility:
    @pytest.mark.parametrize(
        ("energy", "expected"),
        [
            (1.0, Feasibility.FEASIBLE),
            (4.24, Feasibility.FEASIBLE),  # just under 0.85 * 5.0
            (4.25, Feasibility.FEASIBLE),  # exactly at the margin: not over it
            (4.26, Feasibility.FEASIBLE_WITH_MARGIN),
            (5.0, Feasibility.FEASIBLE_WITH_MARGIN),  # exactly at capacity: not over it
            (5.01, Feasibility.INFEASIBLE),
            (12.0, Feasibility.INFEASIBLE),
        ],
    )
    def test_boundaries(self, energy: float, expected: Feasibility):
        assert assess_feasibility(energy, 5.0, 0.85) == expected

    def test_rejects_non_positive_capacity(self):
        with pytest.raises(ValueError, match="battery_capacity_kwh must be positive"):
            assess_feasibility(1.0, 0.0, 0.85)


class TestRiskTiers:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.0, RiskTier.LOW),
            (0.299, RiskTier.LOW),
            (0.3, RiskTier.MEDIUM),
            (0.6, RiskTier.MEDIUM),
            (0.601, RiskTier.HIGH),
            (1.0, RiskTier.HIGH),
        ],
    )
    def test_thresholds(self, score: float, expected: RiskTier):
        assert tier_for_score(score, 0.3, 0.6) == expected

    def test_all_three_tiers_are_reachable(self, settings):
        """A tier no real input can produce would be decoration, not a signal."""
        rover = RoverSpec(6.0, 25.0, 0.003)

        low = assess_mission_risk(make_path([0.02] * 10, energy_kwh=0.5), rover, settings)
        medium = assess_mission_risk(make_path([0.3] * 10, energy_kwh=3.0), rover, settings)
        high = assess_mission_risk(make_path([0.8] * 10, energy_kwh=5.5), rover, settings)

        assert low.risk_tier == RiskTier.LOW
        assert medium.risk_tier == RiskTier.MEDIUM
        assert high.risk_tier == RiskTier.HIGH


class TestRiskScore:
    def test_score_is_length_normalised(self, settings):
        """The original design summed hazards, which made the score grow with
        path length. A safe 500-waypoint traverse must not outscore a nasty
        10-waypoint one purely because it is longer."""
        rover = RoverSpec(6.0, 25.0, 0.003)

        short_nasty = assess_mission_risk(make_path([0.9] * 10, energy_kwh=1.0), rover, settings)
        long_safe = assess_mission_risk(make_path([0.05] * 500, energy_kwh=1.0), rover, settings)

        assert short_nasty.risk_score > long_safe.risk_score

    def test_score_stays_in_range_when_energy_exceeds_capacity(self, settings):
        """An infeasible plan must not push the score outside [0, 1] and break tiering."""
        rover = RoverSpec(2.0, 25.0, 0.003)
        assessment = assess_mission_risk(make_path([0.5] * 10, energy_kwh=8.0), rover, settings)

        assert 0.0 <= assessment.risk_score <= 1.0
        assert assessment.feasibility == Feasibility.INFEASIBLE
        assert assessment.energy_utilisation == pytest.approx(4.0)  # unclamped, reported
        assert assessment.energy_margin_kwh == pytest.approx(-6.0)  # negative: real deficit

    def test_matches_the_documented_formula(self, settings):
        rover = RoverSpec(10.0, 25.0, 0.003)
        assessment = assess_mission_risk(make_path([0.4] * 5, energy_kwh=2.0), rover, settings)

        expected = settings.risk_weight_hazard * 0.4 + settings.risk_weight_energy * 0.2
        assert assessment.risk_score == pytest.approx(expected, abs=1e-4)

    def test_peak_hazard_is_reported_separately_from_the_mean(self, settings):
        """One lethal segment must stay visible even when the mean is benign."""
        rover = RoverSpec(6.0, 25.0, 0.003)
        assessment = assess_mission_risk(
            make_path([0.05] * 49 + [0.98], energy_kwh=1.0), rover, settings
        )

        assert assessment.mean_path_hazard < 0.1
        assert assessment.max_path_hazard == pytest.approx(0.98)

    def test_rejects_an_empty_path(self, settings):
        rover = RoverSpec(6.0, 25.0, 0.003)
        with pytest.raises(ValueError, match="empty path"):
            assess_mission_risk(make_path([], energy_kwh=0.0), rover, settings)


class TestHighHazardSegments:
    def test_returns_worst_first_and_respects_the_limit(self):
        path = make_path([0.1, 0.9, 0.3, 0.7, 0.5, 0.2], energy_kwh=1.0)
        segments = top_hazard_segments(path, limit=3)

        assert [s["segment_id"] for s in segments] == [1, 3, 4]
        assert [s["hazard_score"] for s in segments] == [0.9, 0.7, 0.5]

    def test_handles_paths_shorter_than_the_limit(self):
        path = make_path([0.4, 0.2], energy_kwh=1.0)
        assert len(top_hazard_segments(path, limit=5)) == 2
