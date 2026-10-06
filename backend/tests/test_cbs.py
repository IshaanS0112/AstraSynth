"""Conflict-Based Search over a heterogeneous fleet."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.planning import cbs
from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.planning.outcome import FailureMode

SCOUT = RoverSpec(2.0, 20.0, 0.0018)
HEAVY = RoverSpec(9.0, 30.0, 0.0062)


def corridor(rows: int = 7, cols: int = 12) -> np.ndarray:
    """Open ground walled top and bottom, so rovers must share the middle."""
    hazard = np.zeros((rows, cols))
    hazard[0, :] = 0.99
    hazard[-1, :] = 0.99
    return hazard


def board(hazard, rover=SCOUT, elevation=None) -> PlanningGrid:
    elevation = np.zeros_like(hazard) if elevation is None else elevation
    return PlanningGrid(hazard, elevation, 1.0, rover, max_hazard=0.85)


class TestDeconfliction:
    def test_head_on_swap_is_resolved(self):
        hazard = corridor()
        agents = [
            cbs.Agent("R1", board(hazard), (3, 0), (3, 11)),
            cbs.Agent("R2", board(hazard), (3, 11), (3, 0)),
        ]
        outcome = cbs.solve(agents)

        assert outcome.succeeded
        solution = outcome.diagnostics["solution"]
        assert cbs.find_conflicts(solution.paths) == []
        assert solution.conflicts_branched, "a head-on swap must produce a conflict to resolve"

    def test_three_rovers_share_the_corridor(self):
        hazard = corridor()
        agents = [
            cbs.Agent("R1", board(hazard), (3, 0), (3, 11)),
            cbs.Agent("R2", board(hazard, HEAVY), (3, 11), (3, 0)),
            cbs.Agent("R3", board(hazard), (1, 0), (5, 11)),
        ]
        outcome = cbs.solve(agents)

        assert outcome.succeeded
        solution = outcome.diagnostics["solution"]
        assert cbs.find_conflicts(solution.paths) == []
        assert set(solution.paths) == {"R1", "R2", "R3"}
        for agent in agents:
            path = solution.paths[agent.agent_id]
            assert path[0] == agent.start
            assert path[-1] == agent.goal

    def test_solution_costs_at_least_the_unconstrained_optimum(self):
        """Deconfliction can only make things worse; a cheaper answer means a bug."""
        hazard = corridor()
        agents = [
            cbs.Agent("R1", board(hazard), (3, 0), (3, 11)),
            cbs.Agent("R2", board(hazard), (3, 11), (3, 0)),
        ]
        independent = sum(cbs.plan_with_constraints(agent, frozenset(), 100)[1] for agent in agents)
        outcome = cbs.solve(agents)
        assert outcome.diagnostics["solution"].sum_of_costs >= independent - 1e-9


class TestHeterogeneity:
    def test_each_rover_searches_its_own_graph(self):
        """A ridge the Heavy can climb and the Scout cannot."""
        hazard = np.zeros((5, 9))
        elevation = np.zeros((5, 9))
        # 0.55 m over 1 m cells. Orthogonally that is 28.8 degrees; the shallowest
        # approach is the diagonal, at atan(0.55 / sqrt(2)) = 21.3 degrees. So the
        # Heavy (30 deg) crosses and the Scout (20 deg) cannot, by either route -
        # the diagonal has to be ruled out explicitly or the Scout sneaks over it.
        elevation[:, 4] = 0.55

        scout = cbs.Agent("SCOUT", board(hazard, SCOUT, elevation), (2, 0), (2, 8))
        heavy = cbs.Agent("HEAVY", board(hazard, HEAVY, elevation), (2, 0), (2, 8))

        assert cbs.plan_with_constraints(scout, frozenset(), 60) is None
        assert cbs.plan_with_constraints(heavy, frozenset(), 60) is not None


class TestFailureModes:
    def test_unpassable_corridor_reports_a_diagnosed_failure(self):
        """One cell wide, two rovers, opposite ends: genuinely unsolvable."""
        hazard = np.zeros((3, 12))
        hazard[0, :] = 0.99
        hazard[2, :] = 0.99
        agents = [
            cbs.Agent("R1", board(hazard), (1, 0), (1, 11)),
            cbs.Agent("R2", board(hazard), (1, 11), (1, 0)),
        ]
        outcome = cbs.solve(agents, max_high_level_nodes=50)

        assert not outcome.succeeded
        assert outcome.failure is FailureMode.CONFLICT_UNRESOLVED
        assert "solution" not in outcome.diagnostics

    def test_an_unreachable_goal_is_reported_before_deconfliction(self):
        hazard = np.zeros((7, 12))
        hazard[:, 6] = 0.99  # full-height wall
        agents = [cbs.Agent("R1", board(hazard), (3, 0), (3, 11))]
        outcome = cbs.solve(agents)

        assert not outcome.succeeded
        assert outcome.failure is FailureMode.NO_SAFE_PATH
        assert outcome.diagnostics["agent_id"] == "R1"

    def test_the_wall_clock_budget_is_enforced(self):
        """A node budget is not enough: node cost grows with the instance."""
        hazard = corridor(rows=9, cols=30)
        agents = [
            cbs.Agent("R1", board(hazard), (4, 0), (4, 29)),
            cbs.Agent("R2", board(hazard), (4, 29), (4, 0)),
            cbs.Agent("R3", board(hazard), (2, 0), (6, 29)),
            cbs.Agent("R4", board(hazard), (6, 29), (2, 0)),
        ]
        outcome = cbs.solve(agents, max_high_level_nodes=10**6, time_budget_seconds=0.05)

        assert not outcome.succeeded
        assert outcome.failure is FailureMode.TIMEOUT
        assert "solution" not in outcome.diagnostics
        assert outcome.diagnostics["elapsed_seconds"] >= 0.05

    def test_duplicate_agent_ids_are_rejected(self):
        hazard = corridor()
        agents = [
            cbs.Agent("R1", board(hazard), (3, 0), (3, 11)),
            cbs.Agent("R1", board(hazard), (3, 11), (3, 0)),
        ]
        with pytest.raises(ValueError, match="unique"):
            cbs.solve(agents)


class TestConflictDetection:
    def test_detects_a_vertex_conflict(self):
        paths = {"A": [(0, 0), (0, 1), (0, 2)], "B": [(1, 1), (0, 1), (1, 2)]}
        conflicts = cbs.find_conflicts(paths)
        assert any(c.kind == "vertex" and c.time == 1 and c.cell == (0, 1) for c in conflicts)

    def test_detects_an_edge_swap(self):
        paths = {"A": [(0, 0), (0, 1)], "B": [(0, 1), (0, 0)]}
        conflicts = cbs.find_conflicts(paths)
        assert any(c.kind == "edge" for c in conflicts)

    def test_an_agent_parked_on_its_goal_still_blocks(self):
        """Arriving early must not make a rover invisible to the collision check."""
        paths = {"A": [(0, 0)], "B": [(1, 1), (0, 0)]}
        conflicts = cbs.find_conflicts(paths)
        assert any(c.kind == "vertex" and c.time == 1 for c in conflicts)
