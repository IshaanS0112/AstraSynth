"""Driving a route against terrain the rover cannot fully see."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.planning.outcome import FailureMode
from app.services.simulation.belief import BeliefState
from app.services.simulation.execution import simulate_traverse
from app.services.simulation.sensors import RoverSensor

ROVER = RoverSpec(6.0, 25.0, 0.003)


def scenario(size: int = 40, seed: int = 1, obstacles: int = 20):
    rng = np.random.default_rng(seed)
    orbital = rng.random((size, size)) * 0.35
    elevation = rng.random((size, size)) * 0.4
    truth = orbital.copy()
    for _ in range(obstacles):
        row, col = int(rng.integers(3, size - 4)), int(rng.integers(3, size - 4))
        truth[row : row + 3, col : col + 3] = 0.97
    return orbital, elevation, truth


def template(orbital, elevation, rover=ROVER) -> PlanningGrid:
    return PlanningGrid(orbital.copy(), elevation, 2.0, rover, max_hazard=0.85)


class TestTraverse:
    def test_reaches_the_goal_and_discovers_terrain_on_the_way(self):
        orbital, elevation, truth = scenario()
        belief = BeliefState.from_prior(orbital)
        result = simulate_traverse(
            truth_hazard=truth,
            start=(1, 1),
            goal=(38, 38),
            belief=belief,
            sensor=RoverSensor(range_m=8.0),
            grid_template=template(orbital, elevation),
            rng=np.random.default_rng(5),
        )

        assert result.succeeded
        assert result.executed_cells[0] == (1, 1)
        assert result.executed_cells[-1] == (38, 38)
        assert result.replans > 0
        assert 0.0 < result.belief_summary["observed_fraction"] < 1.0

    def test_the_planner_is_never_handed_the_truth(self):
        """The separation the whole simulation rests on, asserted directly."""
        orbital, elevation, truth = scenario()
        belief = BeliefState.from_prior(orbital)
        grid_template = template(orbital, elevation)
        before = grid_template.hazard.copy()

        simulate_traverse(
            truth_hazard=truth,
            start=(1, 1),
            goal=(38, 38),
            belief=belief,
            sensor=RoverSensor(range_m=6.0),
            grid_template=grid_template,
            rng=np.random.default_rng(5),
        )

        assert np.array_equal(grid_template.hazard, before), "the template was mutated"
        unobserved = belief.observation_count == 0
        assert np.array_equal(belief.mean[unobserved], orbital[unobserved]), (
            "unobserved cells must still hold the orbital prior"
        )

    def test_energy_is_charged_against_the_true_terrain(self):
        orbital, elevation, truth = scenario()
        result = simulate_traverse(
            truth_hazard=truth,
            start=(1, 1),
            goal=(38, 38),
            belief=BeliefState.from_prior(orbital),
            sensor=RoverSensor(range_m=8.0),
            grid_template=template(orbital, elevation),
            rng=np.random.default_rng(5),
        )
        truth_grid = template(orbital, elevation).with_hazard(truth)
        expected = sum(
            truth_grid.step_energy_kwh(a, b) for a, b in itertools.pairwise(result.executed_cells)
        )
        assert result.energy_kwh == pytest.approx(expected, rel=1e-9)

    def test_reruns_with_the_same_seed_are_identical(self):
        orbital, elevation, truth = scenario()
        runs = [
            simulate_traverse(
                truth_hazard=truth,
                start=(1, 1),
                goal=(38, 38),
                belief=BeliefState.from_prior(orbital),
                sensor=RoverSensor(range_m=8.0),
                grid_template=template(orbital, elevation),
                rng=np.random.default_rng(11),
            )
            for _ in range(2)
        ]
        assert runs[0].executed_cells == runs[1].executed_cells
        assert runs[0].energy_kwh == runs[1].energy_kwh


class TestFailureModes:
    def test_an_exhausted_battery_is_reported_as_such(self):
        orbital, elevation, truth = scenario()
        result = simulate_traverse(
            truth_hazard=truth,
            start=(1, 1),
            goal=(38, 38),
            belief=BeliefState.from_prior(orbital),
            sensor=RoverSensor(range_m=8.0),
            grid_template=template(orbital, elevation),
            rng=np.random.default_rng(5),
            energy_budget_kwh=0.05,
        )

        assert not result.succeeded
        assert result.outcome.failure is FailureMode.LOW_BATTERY
        assert result.energy_kwh <= 0.05
        assert "energy_budget_kwh" in result.outcome.diagnostics
        assert any(event.kind == "battery_exhausted" for event in result.events)

    def test_an_unreachable_goal_stops_before_the_rover_moves(self):
        orbital = np.zeros((21, 21))
        orbital[10, :] = 0.99
        elevation = np.zeros((21, 21))
        result = simulate_traverse(
            truth_hazard=orbital,
            start=(2, 5),
            goal=(18, 5),
            belief=BeliefState.from_prior(orbital, prior_sigma=0.01),
            sensor=RoverSensor(range_m=4.0),
            grid_template=template(orbital, elevation),
            rng=np.random.default_rng(0),
        )

        assert not result.succeeded
        assert result.outcome.failure is FailureMode.NO_SAFE_PATH
        assert result.executed_cells == [(2, 5)]
        assert result.distance_m == 0.0


class TestEventLog:
    def test_events_are_ordered_and_typed(self):
        orbital, elevation, truth = scenario()
        result = simulate_traverse(
            truth_hazard=truth,
            start=(1, 1),
            goal=(38, 38),
            belief=BeliefState.from_prior(orbital),
            sensor=RoverSensor(range_m=8.0),
            grid_template=template(orbital, elevation),
            rng=np.random.default_rng(5),
        )

        times = [event.t_seconds for event in result.events]
        assert times == sorted(times)
        assert result.events[0].kind == "initial_plan"
        assert result.events[-1].kind == "goal_reached"
        assert {e.category for e in result.events} <= {
            "navigation",
            "planner",
            "energy",
            "risk",
            "system",
        }

    def test_a_reroute_is_distinguished_from_a_confirming_repair(self):
        orbital, elevation, truth = scenario()
        result = simulate_traverse(
            truth_hazard=truth,
            start=(1, 1),
            goal=(38, 38),
            belief=BeliefState.from_prior(orbital),
            sensor=RoverSensor(range_m=8.0),
            grid_template=template(orbital, elevation),
            rng=np.random.default_rng(5),
        )
        assert result.reroutes <= result.replans
        replan_events = [e for e in result.events if e.kind == "replan"]
        assert sum(1 for e in replan_events if e.detail["rerouted"]) == result.reroutes
