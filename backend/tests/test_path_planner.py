"""A* correctness.

The load-bearing claim in this project's resume line is "A* path planning with
an energy-aware cost function". These tests are what make that claim checkable:
optimality against a hand-computed value, admissibility against Dijkstra on the
same grid, the hard slope constraint, and the energy accounting.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.path_planner import (
    PathNotFoundError,
    energy_factor,
    plan_path,
    plan_path_dijkstra,
)


def _rough_terrain() -> tuple[np.ndarray, np.ndarray]:
    """40x40 random hazard over gentle relief.

    Relief is capped at 0.6 m per cell over 2 m cells (about 17 degrees worst
    case) so the slope limit never removes an edge: these tests are about A*
    versus Dijkstra on an identical graph, and a blocked edge would change the
    graph rather than the search.
    """
    rng = np.random.default_rng(11)
    hazard = rng.random((40, 40)).astype(np.float32)
    elevation = (rng.random((40, 40)) * 0.6).astype(np.float32)
    return hazard, elevation


class TestOptimality:
    def test_flat_terrain_path_is_the_straight_diagonal(self, flat_grid, rover):
        """On zero hazard and zero relief, the optimal route is the diagonal.

        With 8-connectivity and a metric cost, a (0,0) -> (9,9) traverse should
        take exactly 9 diagonal steps: any other route is strictly longer.
        """
        hazard, elevation = flat_grid
        path = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 9, "y": 9}, rover, meters_per_cell=1.0
        )

        assert len(path.waypoints) == 10  # start + 9 steps
        # Stored distances are rounded to millimetres, so compare at that scale.
        assert path.total_distance_m == pytest.approx(9 * math.sqrt(2), abs=1e-3)
        # Every step is diagonal, so x and y advance together.
        for index, waypoint in enumerate(path.waypoints):
            assert waypoint.x == index
            assert waypoint.y == index

    def test_matches_dijkstra_cost_on_random_terrain(self, rover):
        """A* must return the same optimal cost as Dijkstra.

        This is the empirical test of admissibility: an inadmissible heuristic
        produces a cheaper-looking but genuinely worse path, which would show up
        here as a cost mismatch.
        """
        hazard, elevation = _rough_terrain()

        astar = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 39, "y": 39}, rover, meters_per_cell=2.0
        )
        dijkstra = plan_path_dijkstra(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 39, "y": 39}, rover, meters_per_cell=2.0
        )

        assert astar.total_cost == pytest.approx(dijkstra.total_cost, rel=1e-9)

    def test_expands_fewer_nodes_than_dijkstra(self, rover):
        """The entire reason to prefer A* over Dijkstra, measured rather than asserted."""
        hazard, elevation = _rough_terrain()

        astar = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 39, "y": 39}, rover, meters_per_cell=2.0
        )
        dijkstra = plan_path_dijkstra(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 39, "y": 39}, rover, meters_per_cell=2.0
        )

        assert astar.metadata["nodes_expanded"] < dijkstra.metadata["nodes_expanded"]

    def test_cost_layer_alone_does_not_force_a_detour(self, rover):
        """Documents the limit of the soft cost layer.

        ``(1 + hazard)`` tops out at 2x, so a one-cell crossing of a maximally
        hazardous band is still cheaper than a fifteen-cell detour around it.
        This is not a bug in A* - it is why the lethal layer exists, and the
        next test is the same scenario with that layer switched on.
        """
        hazard = np.zeros((21, 21), dtype=np.float32)
        hazard[10, :18] = 0.95
        elevation = np.zeros((21, 21), dtype=np.float32)

        path = plan_path(
            hazard,
            elevation,
            {"x": 5, "y": 2},
            {"x": 5, "y": 18},
            rover,
            meters_per_cell=1.0,
            max_hazard=1.0,
        )

        crossings = [w for w in path.waypoints if w.y == 10]
        assert any(w.x < 18 for w in crossings), "soft cost alone cannot force the detour"

    def test_lethal_layer_forces_the_detour(self, rover):
        """With the lethal threshold engaged the band becomes untraversable."""
        hazard = np.zeros((21, 21), dtype=np.float32)
        hazard[10, :18] = 0.95  # above the 0.85 default, gap on the right
        elevation = np.zeros((21, 21), dtype=np.float32)

        path = plan_path(
            hazard,
            elevation,
            {"x": 5, "y": 2},
            {"x": 5, "y": 18},
            rover,
            meters_per_cell=1.0,
            max_hazard=0.85,
        )

        crossings = [w for w in path.waypoints if w.y == 10]
        assert crossings, "path must cross the band line somewhere"
        assert all(w.x >= 18 for w in crossings), "must cross via the low-hazard gap"
        assert path.metadata["moves_blocked_by_lethal_hazard"] > 0

    def test_lethal_layer_can_make_a_goal_unreachable(self, rover):
        hazard = np.zeros((21, 21), dtype=np.float32)
        hazard[10, :] = 0.95  # full-width lethal band
        elevation = np.zeros((21, 21), dtype=np.float32)

        with pytest.raises(PathNotFoundError, match="lethal hazard threshold"):
            plan_path(
                hazard,
                elevation,
                {"x": 5, "y": 2},
                {"x": 5, "y": 18},
                rover,
                meters_per_cell=1.0,
                max_hazard=0.85,
            )


class TestSlopeConstraint:
    def test_detours_around_impassable_cliff(self, walled_grid, rover):
        hazard, elevation = walled_grid
        path = plan_path(
            hazard, elevation, {"x": 2, "y": 5}, {"x": 17, "y": 5}, rover, meters_per_cell=1.0
        )

        crossings = [w for w in path.waypoints if w.x == 10]
        assert crossings, "path must cross the cliff line somewhere"
        assert all(w.y >= 18 for w in crossings), "must cross through the gap at the bottom"
        assert path.metadata["moves_blocked_by_slope_limit"] > 0

    def test_raises_when_goal_is_walled_off(self, rover):
        """Full-height wall -> genuinely no route -> PathNotFoundError, not a bad path."""
        hazard = np.zeros((20, 20), dtype=np.float32)
        elevation = np.zeros((20, 20), dtype=np.float32)
        elevation[:, 10] = 500.0

        with pytest.raises(PathNotFoundError, match="No traversable path"):
            plan_path(
                hazard, elevation, {"x": 2, "y": 5}, {"x": 17, "y": 5}, rover, meters_per_cell=1.0
            )

    def test_no_step_exceeds_the_rover_slope_limit(self, rover):
        """Relief chosen so the limit genuinely bites but a route still exists."""
        rng = np.random.default_rng(3)
        hazard = np.zeros((40, 40), dtype=np.float32)
        elevation = (rng.random((40, 40)) * 2.0).astype(np.float32)

        path = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 39, "y": 39}, rover, meters_per_cell=3.0
        )
        assert path.metadata["moves_blocked_by_slope_limit"] > 0

        for waypoint in path.waypoints[1:]:
            assert abs(waypoint.slope_deg) <= rover.max_traversable_slope_deg + 1e-6


class TestEnergyModel:
    def test_energy_factor_is_one_on_the_flat(self):
        assert energy_factor(0.0, k=0.5) == 1.0

    def test_energy_factor_grows_with_incline_and_is_symmetric(self):
        assert energy_factor(0.4, k=0.5) == pytest.approx(1.2)
        assert energy_factor(-0.4, k=0.5) == pytest.approx(1.2)

    def test_flat_energy_equals_rate_times_distance(self, flat_grid, rover):
        """With no incline the energy model must collapse to rate x distance."""
        hazard, elevation = flat_grid
        path = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 9, "y": 9}, rover, meters_per_cell=1.0
        )

        expected = rover.energy_per_meter_kwh * path.total_distance_m
        assert path.total_energy_cost_kwh == pytest.approx(expected, rel=1e-6)

    def test_cumulative_energy_is_monotonic_and_matches_the_total(self, rover):
        rng = np.random.default_rng(5)
        hazard = rng.random((25, 25)).astype(np.float32)
        elevation = (rng.random((25, 25)) * 0.6).astype(np.float32)

        path = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 24, "y": 24}, rover, meters_per_cell=2.0
        )

        cumulative = [w.cumulative_energy_kwh for w in path.waypoints]
        assert cumulative == sorted(cumulative)
        assert cumulative[-1] == pytest.approx(path.total_energy_cost_kwh, abs=1e-6)
        assert sum(w.energy_cost_kwh for w in path.waypoints) == pytest.approx(
            path.total_energy_cost_kwh, abs=1e-4
        )

    def test_climbing_costs_more_than_the_same_distance_flat(self, rover):
        """The 'energy-aware' claim, isolated to a single variable."""
        flat = np.zeros((10, 10), dtype=np.float32)
        ramp = np.tile(np.linspace(0, 20, 10, dtype=np.float32), (10, 1))
        hazard = np.zeros((10, 10), dtype=np.float32)

        flat_path = plan_path(
            hazard, flat, {"x": 0, "y": 5}, {"x": 9, "y": 5}, rover, meters_per_cell=5.0
        )
        ramp_path = plan_path(
            hazard, ramp, {"x": 0, "y": 5}, {"x": 9, "y": 5}, rover, meters_per_cell=5.0
        )

        assert ramp_path.total_distance_m == pytest.approx(flat_path.total_distance_m)
        assert ramp_path.total_energy_cost_kwh > flat_path.total_energy_cost_kwh


class TestInputHandling:
    def test_rejects_identical_start_and_goal(self, flat_grid, rover):
        hazard, elevation = flat_grid
        with pytest.raises(ValueError, match="same planning cell"):
            plan_path(
                hazard, elevation, {"x": 5, "y": 5}, {"x": 5, "y": 5}, rover, meters_per_cell=1.0
            )

    def test_rejects_mismatched_grids(self, rover):
        with pytest.raises(ValueError, match="same shape"):
            plan_path(
                np.zeros((10, 10), dtype=np.float32),
                np.zeros((12, 12), dtype=np.float32),
                {"x": 0, "y": 0},
                {"x": 9, "y": 9},
                rover,
                meters_per_cell=1.0,
            )

    def test_out_of_range_points_are_clamped_into_the_grid(self, flat_grid, rover):
        """A click near the image edge must not index out of bounds."""
        hazard, elevation = flat_grid
        path = plan_path(
            hazard, elevation, {"x": 0, "y": 0}, {"x": 9999, "y": 9999}, rover, meters_per_cell=1.0
        )
        assert path.waypoints[-1].x == 19
        assert path.waypoints[-1].y == 19
