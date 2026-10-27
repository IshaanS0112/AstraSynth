"""The traverse executive: what happens when the drive cannot continue."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.planning.outcome import FailureMode
from app.services.simulation.behavior_tree import Blackboard, Status
from app.services.simulation.belief import BeliefState
from app.services.simulation.execution import simulate_traverse
from app.services.simulation.navigator import RECOVERABLE, back_up, build_navigator, compute_path
from app.services.simulation.sensors import RoverSensor
from tests.test_execution import scenario, template

ROVER = RoverSpec(6.0, 25.0, 0.003)


def drive(truth, orbital, elevation, start, goal, *, prior_sigma=0.25, seed=5, **kwargs):
    return simulate_traverse(
        truth_hazard=truth,
        start=start,
        goal=goal,
        belief=BeliefState.from_prior(orbital, prior_sigma=prior_sigma),
        sensor=RoverSensor(range_m=8.0),
        grid_template=template(orbital, elevation),
        rng=np.random.default_rng(seed),
        **kwargs,
    )


def uncertainty_wall(size: int = 30):
    """Safe ground everywhere, but unsurveyed: mean + 3 sigma is lethal."""
    orbital = np.full((size, size), 0.2)
    elevation = np.zeros((size, size))
    return orbital.copy(), orbital, elevation


class TestNoFailureMeansNoChange:
    def test_a_clean_traverse_is_identical_with_and_without_recovery(self):
        """The tree adds an answer to failure; it must not change success."""
        orbital, elevation, truth = scenario()
        without = drive(truth, orbital, elevation, (1, 1), (38, 38), max_recoveries=0)
        with_ = drive(truth, orbital, elevation, (1, 1), (38, 38), max_recoveries=3)

        assert without.succeeded and with_.succeeded
        assert with_.executed_cells == without.executed_cells
        assert with_.energy_kwh == without.energy_kwh
        assert with_.replans == without.replans
        assert [e.as_dict() for e in with_.events] == [e.as_dict() for e in without.events]
        assert with_.executive["recoveries"] == []


class TestFailureClassification:
    """The planner only knows there is no route from here; the executive knows where here is."""

    @staticmethod
    def stuck(executed):
        class Unreachable:
            reachable = False

        return Blackboard(
            planner=Unreachable(),
            needs_fresh_plan=False,
            executed=list(executed),
            position=executed[-1],
            start=executed[0],
            goal=(9, 9),
            log=lambda *a, **k: None,
        )

    def test_never_having_moved_is_no_safe_path(self):
        bb = self.stuck([(1, 1)])
        assert compute_path(bb) is Status.FAILURE
        assert bb.error is FailureMode.NO_SAFE_PATH

    def test_cut_off_after_driving_is_rover_blocked(self):
        bb = self.stuck([(1, 1), (2, 2), (3, 3)])
        assert compute_path(bb) is Status.FAILURE
        assert bb.error is FailureMode.ROVER_BLOCKED
        assert bb.failure.diagnostics["steps_taken"] == 2


class TestRecovery:
    def test_a_mission_the_loop_would_abort_is_completed(self):
        truth, orbital, elevation = uncertainty_wall()
        start, goal = (2, 2), (27, 27)

        aborted = drive(
            truth, orbital, elevation, start, goal, uncertainty_weight=3.0, max_recoveries=0
        )
        assert aborted.outcome.failure is FailureMode.NO_SAFE_PATH
        assert aborted.distance_m == 0.0, "the old executive never started"

        rescued = drive(
            truth, orbital, elevation, start, goal, uncertainty_weight=3.0, max_recoveries=3
        )
        assert rescued.succeeded
        assert rescued.executed_cells[-1] == goal
        tried = [r["recovery"] for r in rescued.executive["recoveries"]]
        assert tried == ["ReSense", "RelaxUncertainty"], "escalated, in order"
        assert rescued.executive["final_uncertainty_weight"] == 1.5
        assert rescued.outcome.diagnostics["recoveries"] == 2

    def test_recoveries_appear_in_the_event_log(self):
        truth, orbital, elevation = uncertainty_wall()
        result = drive(truth, orbital, elevation, (2, 2), (27, 27), uncertainty_weight=3.0)
        executive = [e for e in result.events if e.category == "executive"]
        assert [e.detail["recovery"] for e in executive] == ["ReSense", "RelaxUncertainty"]
        assert all(e.detail["after_error"] == "NO_SAFE_PATH" for e in executive)

    def test_a_panorama_costs_time(self):
        truth, orbital, elevation = uncertainty_wall()
        result = drive(truth, orbital, elevation, (2, 2), (27, 27), uncertainty_weight=3.0)
        assert result.elapsed_seconds >= 600.0

    def test_a_hopeless_goal_still_fails_with_its_real_cause(self):
        """A goal walled off in TRUTH: recovery is tried, and honestly fails."""
        size = 24
        orbital = np.full((size, size), 0.1)
        orbital[:, 12] = 0.99  # surveyed, and genuinely lethal
        elevation = np.zeros((size, size))
        result = drive(
            orbital.copy(), orbital, elevation, (5, 2), (5, 20), prior_sigma=0.02, max_recoveries=3
        )

        assert not result.succeeded
        assert result.outcome.failure is FailureMode.NO_SAFE_PATH
        assert result.distance_m == 0.0


class TestGating:
    """Nav2's WouldAPlannerRecoveryHelp: some failures no recovery can fix."""

    @pytest.mark.parametrize("mode", [FailureMode.LOW_BATTERY, FailureMode.TIMEOUT])
    def test_exhausted_resources_are_not_recoverable(self, mode):
        assert mode not in RECOVERABLE

    def test_a_flat_battery_is_final(self):
        orbital, elevation, truth = scenario()
        result = drive(
            truth, orbital, elevation, (1, 1), (38, 38), energy_budget_kwh=0.02, max_recoveries=3
        )
        assert result.outcome.failure is FailureMode.LOW_BATTERY
        assert result.executive["recoveries"] == []

    def test_a_step_budget_is_final(self):
        orbital, elevation, truth = scenario()
        result = drive(truth, orbital, elevation, (1, 1), (38, 38), max_steps=5, max_recoveries=3)
        assert result.outcome.failure is FailureMode.TIMEOUT
        assert result.executive["recoveries"] == []


class TestBackUp:
    def _board(self, executed):
        orbital = np.full((10, 10), 0.1)
        grid = PlanningGrid(orbital, np.zeros((10, 10)), 2.0, ROVER, max_hazard=0.85)

        class Planner:
            def __init__(self):
                self.moves = []

            def move_to(self, cell):
                self.moves.append(cell)

        bb = Blackboard(
            truth_grid=grid,
            executed=list(executed),
            position=executed[-1],
            distance=0.0,
            energy=0.0,
            clock=0.0,
            budget=6.0,
            planner=Planner(),
            recoveries=[],
            error=FailureMode.ROVER_BLOCKED,
            log=lambda *a, **k: None,
        )
        return bb

    def test_it_retreats_along_the_driven_path(self):
        bb = self._board([(0, 0), (1, 1), (2, 2), (3, 3), (4, 4)])
        assert back_up(bb) is Status.SUCCESS
        assert bb.position == (1, 1), "three cells back the way it came"
        assert bb.executed[-3:] == [(3, 3), (2, 2), (1, 1)], "the retreat is driven, and logged"
        assert bb.distance > 0 and bb.energy > 0, "reversing is not free"

    def test_it_cannot_back_up_from_the_start(self):
        bb = self._board([(0, 0)])
        assert back_up(bb) is Status.FAILURE


class TestTheTree:
    def test_it_has_nav2s_shape(self):
        described = build_navigator(3).describe()
        assert described["type"] == "RecoveryNode" and described["retries"] == 3
        main, recovery = described["children"]
        assert [c["name"] for c in main["children"]] == ["ComputePathToGoal", "FollowPath"]
        cycle = recovery["children"][1]
        assert cycle["type"] == "RoundRobin"
        assert [c["name"] for c in cycle["children"]] == ["ReSense", "RelaxUncertainty", "BackUp"]
