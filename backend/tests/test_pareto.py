"""Multi-objective route selection and the Pareto front."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.planning import pareto
from app.services.planning.grid import RoverSpec

ROVER = RoverSpec(6.0, 25.0, 0.003)


def candidate(label: str, **objectives) -> pareto.RouteCandidate:
    filled = {key: objectives.get(key, 0.0) for key in pareto.OBJECTIVES}
    return pareto.RouteCandidate(label, 1.0, 1.0, [(0, 0)], filled, 0, 0)


class TestDominance:
    def test_strictly_better_dominates(self):
        better = candidate("A", distance_m=10, energy_kwh=1)
        worse = candidate("B", distance_m=20, energy_kwh=2)
        assert pareto.dominates(better.objectives, worse.objectives)
        assert not pareto.dominates(worse.objectives, better.objectives)

    def test_a_trade_off_is_not_domination(self):
        fast = candidate("fast", distance_m=10, mean_hazard=0.9)
        safe = candidate("safe", distance_m=20, mean_hazard=0.1)
        assert not pareto.dominates(fast.objectives, safe.objectives)
        assert not pareto.dominates(safe.objectives, fast.objectives)

    def test_identical_routes_do_not_dominate_each_other(self):
        first = candidate("one", distance_m=10)
        second = candidate("two", distance_m=10)
        assert not pareto.dominates(first.objectives, second.objectives)

    def test_the_front_keeps_only_non_dominated_routes(self):
        fast = candidate("fast", distance_m=10, mean_hazard=0.9)
        safe = candidate("safe", distance_m=20, mean_hazard=0.1)
        useless = candidate("useless", distance_m=25, mean_hazard=0.95)
        front = pareto.pareto_front([fast, safe, useless])
        assert {c.label for c in front} == {"fast", "safe"}

    def test_no_member_of_the_front_dominates_another(self):
        rng = np.random.default_rng(0)
        candidates = [
            candidate(
                str(i),
                distance_m=float(rng.random()),
                energy_kwh=float(rng.random()),
                mean_hazard=float(rng.random()),
            )
            for i in range(60)
        ]
        front = pareto.pareto_front(candidates)
        assert front
        for a in front:
            for b in front:
                assert not pareto.dominates(a.objectives, b.objectives)


class TestSweep:
    def test_the_sweep_finds_a_genuine_trade_off(self):
        rng = np.random.default_rng(9)
        hazard = rng.random((50, 50)) * 0.8
        elevation = rng.random((50, 50)) * 3.0
        result = pareto.sweep(hazard, elevation, (1, 1), (48, 48), ROVER, 2.0, max_hazard=0.85)

        assert result["candidates_planned"] >= 3
        assert len(result["front"]) >= 2

        shortest = min(result["front"], key=lambda c: c["objectives"]["distance_m"])
        safest = min(result["front"], key=lambda c: c["objectives"]["mean_hazard"])
        assert shortest["label"] != safest["label"]
        assert safest["objectives"]["distance_m"] > shortest["objectives"]["distance_m"]
        assert safest["objectives"]["mean_hazard"] < shortest["objectives"]["mean_hazard"]

    def test_objectives_are_measured_on_the_unweighted_model(self):
        """Two routes found at different weights must remain comparable."""
        rng = np.random.default_rng(3)
        hazard = rng.random((30, 30)) * 0.7
        elevation = np.zeros((30, 30))
        result = pareto.sweep(hazard, elevation, (1, 1), (28, 28), ROVER, 2.0, max_hazard=0.85)

        for entry in result["front"] + result["dominated"]:
            objectives = entry["objectives"]
            assert 0.0 <= objectives["mean_hazard"] <= 1.0
            assert objectives["max_hazard"] >= objectives["mean_hazard"]
            assert objectives["distance_m"] > 0

    def test_zero_weights_give_the_shortest_route_the_constraints_allow(self):
        hazard = np.zeros((20, 20))
        elevation = np.zeros((20, 20))
        result = pareto.sweep(hazard, elevation, (0, 0), (19, 19), ROVER, 1.0)
        # On uniform terrain every weighting finds the same diagonal, so the
        # front collapses to one route - which is the correct answer, not a bug.
        assert len(result["front"]) == 1
        assert result["front"][0]["objectives"]["distance_m"] == pytest.approx(
            19 * 2**0.5, rel=1e-6
        )

    def test_weightings_with_no_route_are_reported_not_dropped(self):
        hazard = np.zeros((21, 21))
        hazard[10, :] = 0.99
        elevation = np.zeros((21, 21))
        result = pareto.sweep(hazard, elevation, (2, 5), (18, 5), ROVER, 1.0, max_hazard=0.85)
        assert result["candidates_planned"] == 0
        assert len(result["weightings_with_no_route"]) == len(pareto.DEFAULT_SWEEP)
        assert result["front"] == []
