"""Properties the planners must satisfy on *any* terrain, not just the fixtures.

Example-based tests check the cases somebody thought of. The failures that
matter in a search algorithm are the ones nobody thought of: a one-cell corridor,
a grid where every diagonal clips a lethal corner, a start already walled in. So
these are stated as invariants and Hypothesis goes looking for terrain that
breaks them.

The invariants chosen are *metamorphic* - they relate two runs to each other
rather than asserting an expected output. That matters because the expected
output of A* on random terrain is "whatever A* computes", which no test can
independently know. What a test can know is that making the ground worse must
never make the route cheaper, and that two planners over one cost model must
agree on the optimum.
"""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.services.planning import astar
from app.services.planning.dstar_lite import DStarLite
from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.planning.theta_star import search as theta_search

ROVER = RoverSpec(6.0, 25.0, 0.003)

# Grids stay small and the example count modest: every example runs real searches,
# and a property suite that takes ten minutes is a property suite people skip.
SLOW = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)


@st.composite
def terrain(draw, min_size: int = 6, max_size: int = 14):
    """Random hazard and relief, plus the endpoints, as one drawn value.

    Hazard reaches past the lethal threshold and relief past the slope limit on
    purpose: a generator that only produces drivable ground would never exercise
    the constraint layers, which is where the interesting failures live.
    """
    rows = draw(st.integers(min_value=min_size, max_value=max_size))
    cols = draw(st.integers(min_value=min_size, max_value=max_size))
    seed = draw(st.integers(min_value=0, max_value=2**31 - 1))
    hazard_scale = draw(st.floats(min_value=0.1, max_value=1.0))
    relief_scale = draw(st.floats(min_value=0.0, max_value=4.0))

    rng = np.random.default_rng(seed)
    hazard = rng.random((rows, cols)) * hazard_scale
    elevation = rng.random((rows, cols)) * relief_scale

    start = (draw(st.integers(0, rows - 1)), draw(st.integers(0, cols - 1)))
    goal = (draw(st.integers(0, rows - 1)), draw(st.integers(0, cols - 1)))
    return hazard, elevation, start, goal


def build(hazard, elevation, **kwargs) -> PlanningGrid:
    kwargs.setdefault("max_hazard", 0.85)
    return PlanningGrid(hazard.copy(), elevation, 2.0, ROVER, **kwargs)


def route_cost(grid: PlanningGrid, cells) -> float:
    total = 0.0
    for previous, cell in itertools.pairwise(cells):
        edge = grid.edge_cost(previous, cell)
        assert edge is not None, f"route uses a blocked edge {previous}->{cell}"
        total += edge
    return total


class TestOptimality:
    @given(terrain())
    @SLOW
    def test_astar_and_dijkstra_agree_on_the_optimum(self, drawn):
        """The empirical admissibility check, over arbitrary terrain.

        An inadmissible heuristic shows up here as A* finding a *cheaper* cost
        than Dijkstra - cheaper because it stopped early on a route that is not
        actually optimal.
        """
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        with_heuristic = astar.search(build(hazard, elevation), start, goal)
        without = astar.search(build(hazard, elevation), start, goal, use_heuristic=False)

        assert with_heuristic.found == without.found
        if with_heuristic.found:
            assert with_heuristic.total_cost == pytest.approx(without.total_cost, rel=1e-9)

    @given(terrain())
    @SLOW
    def test_the_reported_cost_is_the_cost_of_the_route_returned(self, drawn):
        """Guards the gap between what the search accumulated and what it emitted."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        grid = build(hazard, elevation)
        result = astar.search(grid, start, goal)
        if not result.found:
            return

        assert result.cells[0] == start
        assert result.cells[-1] == goal
        assert route_cost(grid, result.cells) == pytest.approx(result.total_cost, rel=1e-9)

    @given(terrain())
    @SLOW
    def test_no_route_ever_revisits_a_cell(self, drawn):
        """Positive edge costs make a cycle strictly worse, so one is a bug."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return
        result = astar.search(build(hazard, elevation), start, goal)
        assert len(set(result.cells)) == len(result.cells)


class TestMonotonicity:
    @given(terrain(), st.integers(0, 2**31 - 1))
    @SLOW
    def test_making_the_ground_worse_never_makes_the_route_cheaper(self, drawn, seed):
        """The invariant that catches a sign error anywhere in the cost model."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        before = astar.search(build(hazard, elevation), start, goal)
        if not before.found:
            return

        rng = np.random.default_rng(seed)
        worse = np.clip(hazard + rng.random(hazard.shape) * 0.3, 0.0, 1.0)
        after = astar.search(build(worse, elevation), start, goal)

        if after.found:
            assert after.total_cost >= before.total_cost - 1e-9

    @given(terrain())
    @SLOW
    def test_a_stricter_rover_never_finds_a_cheaper_route(self, drawn):
        """Tightening the slope limit removes edges; it cannot add one."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        permissive = PlanningGrid(
            hazard.copy(), elevation, 2.0, RoverSpec(6.0, 40.0, 0.003), max_hazard=0.85
        )
        strict = PlanningGrid(
            hazard.copy(), elevation, 2.0, RoverSpec(6.0, 10.0, 0.003), max_hazard=0.85
        )

        loose = astar.search(permissive, start, goal)
        tight = astar.search(strict, start, goal)

        if tight.found:
            assert loose.found
            assert tight.total_cost >= loose.total_cost - 1e-9

    @given(terrain())
    @SLOW
    def test_lowering_the_lethal_threshold_never_opens_a_route(self, drawn):
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        permissive = astar.search(build(hazard, elevation, max_hazard=1.0), start, goal)
        strict = astar.search(build(hazard, elevation, max_hazard=0.4), start, goal)

        if strict.found:
            assert permissive.found


class TestConstraints:
    @given(terrain())
    @SLOW
    def test_every_returned_route_is_actually_drivable(self, drawn):
        """Every step legal under the rover's own limits, checked independently."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        grid = build(hazard, elevation)
        result = astar.search(grid, start, goal)
        if not result.found:
            return

        limit = math.tan(math.radians(ROVER.max_traversable_slope_deg))
        for previous, cell in zip(result.cells, result.cells[1:], strict=False):
            assert abs(previous[0] - cell[0]) <= 1 and abs(previous[1] - cell[1]) <= 1
            assert float(grid.hazard[cell]) < grid.max_hazard
            assert abs(grid.rise_over_run(previous, cell)) <= limit + 1e-9

    @given(terrain())
    @SLOW
    def test_theta_star_segments_are_all_traversable(self, drawn):
        """Any-angle shortcuts are the easiest place to cut a corner by accident."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        grid = build(hazard, elevation)
        result = theta_search(grid, start, goal)
        if not result.found:
            return

        assert result.cells[0] == start
        assert result.cells[-1] == goal
        for previous, cell in zip(result.cells, result.cells[1:], strict=False):
            cost, reason, _ = grid.segment(previous, cell)
            assert cost is not None, f"segment {previous}->{cell} blocked by {reason}"


class TestIncrementalEquivalence:
    @given(terrain(min_size=8, max_size=14), st.integers(0, 2**31 - 1))
    @SLOW
    def test_dstar_lite_always_matches_a_fresh_search(self, drawn, seed):
        """The claim the whole incremental planner rests on, on arbitrary terrain.

        After any sequence of belief updates, the route D* Lite holds must be the
        one A* would compute on the updated map - at the same cost, not merely a
        similar one.
        """
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        grid = build(hazard, elevation)
        planner = DStarLite(grid, start, goal)

        rng = np.random.default_rng(seed)
        updated = hazard.copy()
        rows, cols = hazard.shape
        for _ in range(3):
            row = int(rng.integers(0, rows))
            col = int(rng.integers(0, cols))
            value = float(rng.random())
            updated[row, col] = value
            planner.apply_updates({(row, col): value})

        reference = astar.search(build(updated, elevation), start, goal)
        if reference.found:
            assert planner.reachable
            assert planner.cost_to_goal() == pytest.approx(reference.total_cost, rel=1e-9)
        else:
            assert not planner.reachable

    @given(terrain(min_size=8, max_size=12))
    @SLOW
    def test_an_unchanged_map_produces_no_repair(self, drawn):
        """ "Incremental" has to mean something on arbitrary terrain too."""
        hazard, elevation, start, goal = drawn
        if start == goal:
            return

        grid = build(hazard, elevation)
        planner = DStarLite(grid, start, goal)
        before = planner.stats.replan_count

        rows, cols = hazard.shape
        echo = {(r, c): float(hazard[r, c]) for r in range(rows) for c in range(0, cols, 3)}
        assert planner.apply_updates(echo) is None
        assert planner.stats.replan_count == before
