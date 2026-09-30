"""Theta* any-angle planning."""

from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from app.services.planning import astar, theta_star
from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.planning.trace import build_anyangle_path, heading_changes

ROVER = RoverSpec(6.0, 25.0, 0.003)


def grid(hazard, elevation=None, **kwargs) -> PlanningGrid:
    elevation = np.zeros_like(hazard) if elevation is None else elevation
    return PlanningGrid(hazard, elevation, kwargs.pop("meters_per_cell", 1.0), ROVER, **kwargs)


def euclidean_length(cells) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in itertools.pairwise(cells))


class TestAnyAngle:
    def test_open_terrain_produces_the_exact_straight_line(self):
        """The case A* cannot express: an off-diagonal heading over open ground."""
        board = grid(np.zeros((40, 40)))
        result = theta_star.search(board, (2, 2), (37, 20))

        assert result.found
        assert result.cells == [(2, 2), (37, 20)]
        assert euclidean_length(result.cells) == pytest.approx(math.hypot(35, 18))
        assert heading_changes(result.cells) == 0

    def test_beats_astar_on_length_and_turns_over_open_ground(self):
        board = grid(np.zeros((40, 40)))
        grid_route = astar.search(board, (2, 2), (37, 20))
        any_angle = theta_star.search(board, (2, 2), (37, 20))

        assert euclidean_length(any_angle.cells) < euclidean_length(grid_route.cells)
        assert heading_changes(any_angle.cells) < heading_changes(grid_route.cells)

    def test_routes_around_a_lethal_band_rather_than_through_it(self):
        hazard = np.zeros((21, 21))
        hazard[10, :18] = 0.95
        board = grid(hazard, max_hazard=0.85)

        result = theta_star.search(board, (2, 5), (18, 5))
        assert result.found

        # Every segment of the returned route must itself be drivable.
        for first, second in itertools.pairwise(result.cells):
            cost, reason, _ = board.segment(first, second)
            assert cost is not None, f"segment {first}->{second} blocked by {reason}"

    def test_reports_failure_when_the_goal_is_walled_off(self):
        hazard = np.zeros((21, 21))
        hazard[10, :] = 0.95
        board = grid(hazard, max_hazard=0.85)

        result = theta_star.search(board, (2, 5), (18, 5))
        assert not result.found
        assert result.cells == []

    def test_respects_the_slope_limit_along_the_shortcut(self):
        """The shortcut must be tested against the terrain, not just its endpoints."""
        elevation = np.zeros((21, 21))
        elevation[10, :] = 400.0  # ridge; endpoints level, crossing impossible
        board = grid(np.zeros((21, 21)), elevation)

        result = theta_star.search(board, (2, 5), (18, 5))
        assert not result.found

    def test_shortcuts_are_actually_taken_when_terrain_allows(self):
        board = grid(np.zeros((30, 30)))
        result = theta_star.search(board, (1, 1), (28, 15))
        assert result.shortcuts_taken > 0
        assert result.line_of_sight_checks > 0


class TestReporting:
    def test_waypoint_hazard_is_the_segment_mean_not_the_corner(self):
        """A clean corner must not hide a hazardous segment from the risk engine."""
        hazard = np.zeros((21, 21))
        hazard[1:20, 10] = 0.6  # a band the straight route crosses
        board = grid(hazard)

        path = build_anyangle_path(board, [(10, 0), (10, 20)], 1.0, {"algorithm": "theta_star"})
        arriving = path.waypoints[-1]
        assert board.hazard[10, 20] == 0.0
        assert arriving.hazard_score > 0.0

    def test_reported_distance_is_the_true_euclidean_length(self):
        board = grid(np.zeros((30, 30)))
        result = theta_star.search(board, (0, 0), (20, 9))
        path = build_anyangle_path(board, result.cells, 1.0, {"algorithm": "theta_star"})
        assert path.total_distance_m == pytest.approx(euclidean_length(result.cells), abs=1e-3)
