"""The shared cost model."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.planning.grid import (
    EdgeBlock,
    PlanningGrid,
    RoverSpec,
    energy_factor,
    heuristic_is_admissible,
)


def make_grid(hazard=None, elevation=None, rover=None, **kwargs) -> PlanningGrid:
    if hazard is None:
        hazard = np.zeros((10, 10)) if elevation is None else np.zeros_like(elevation)
    if elevation is None:
        elevation = np.zeros_like(hazard)
    rover = rover or RoverSpec(6.0, 25.0, 0.003)
    return PlanningGrid(hazard, elevation, kwargs.pop("meters_per_cell", 1.0), rover, **kwargs)


class TestCostModel:
    def test_flat_zero_hazard_edge_costs_exactly_its_length(self):
        grid = make_grid()
        assert grid.edge_cost((0, 0), (0, 1)) == pytest.approx(1.0)
        assert grid.edge_cost((0, 0), (1, 1)) == pytest.approx(math.sqrt(2))

    def test_cost_is_never_below_distance(self):
        """The precondition every heuristic in this package rests on."""
        rng = np.random.default_rng(0)
        grid = make_grid(hazard=rng.random((20, 20)), elevation=rng.random((20, 20)) * 0.3)
        for row in range(19):
            for col in range(19):
                for neighbour in grid.neighbours((row, col)):
                    cost = grid.edge_cost((row, col), neighbour)
                    if cost is not None:
                        assert cost >= grid.distance_m((row, col), neighbour) - 1e-12

    def test_hazard_weight_scales_only_the_hazard_term(self):
        hazard = np.full((5, 5), 0.5)
        base = make_grid(hazard=hazard, hazard_weight=1.0)
        doubled = make_grid(hazard=hazard, hazard_weight=2.0)
        assert base.edge_cost((0, 0), (0, 1)) == pytest.approx(1.5)
        assert doubled.edge_cost((0, 0), (0, 1)) == pytest.approx(2.0)

    def test_negative_weights_are_refused(self):
        """A planner rewarded for hazard would break admissibility silently."""
        assert not heuristic_is_admissible(-0.1, 1.0)
        with pytest.raises(ValueError, match="non-negative"):
            make_grid(hazard_weight=-1.0)

    def test_energy_is_independent_of_the_search_weights(self):
        """Energy must stay comparable against a battery whatever the objective."""
        hazard = np.full((5, 5), 0.5)
        base = make_grid(hazard=hazard, hazard_weight=1.0)
        skewed = make_grid(hazard=hazard, hazard_weight=8.0)
        assert base.step_energy_kwh((0, 0), (0, 1)) == skewed.step_energy_kwh((0, 0), (0, 1))

    def test_payload_scales_energy_but_not_search_cost(self):
        laden = RoverSpec(6.0, 25.0, 0.003, mass_kg=200.0, payload_kg=50.0)
        empty = RoverSpec(6.0, 25.0, 0.003, mass_kg=200.0, payload_kg=0.0)
        laden_grid = make_grid(rover=laden)
        empty_grid = make_grid(rover=empty)

        assert laden.payload_factor() == pytest.approx(1.25)
        assert laden_grid.step_energy_kwh((0, 0), (0, 1)) == pytest.approx(
            1.25 * empty_grid.step_energy_kwh((0, 0), (0, 1))
        )
        assert laden_grid.edge_cost((0, 0), (0, 1)) == empty_grid.edge_cost((0, 0), (0, 1))

    def test_default_rover_reproduces_v1_energy_exactly(self):
        """The payload model must be opt-in to the last bit, not merely close."""
        grid = make_grid()
        assert grid.rover.payload_factor() == 1.0
        assert grid.step_energy_kwh((0, 0), (0, 1)) == pytest.approx(0.003, abs=0)

    def test_energy_factor_is_symmetric_and_unity_on_the_flat(self):
        assert energy_factor(0.0, k=0.5) == 1.0
        assert energy_factor(0.4, k=0.5) == pytest.approx(energy_factor(-0.4, k=0.5))


class TestConstraintLayers:
    def test_lethal_hazard_removes_the_edge_with_a_reason(self):
        hazard = np.zeros((5, 5))
        hazard[0, 1] = 0.9
        grid = make_grid(hazard=hazard, max_hazard=0.85)
        cost, reason = grid.evaluate_edge((0, 0), (0, 1))
        assert cost is None
        assert reason is EdgeBlock.LETHAL_HAZARD

    def test_slope_limit_removes_the_edge_with_a_reason(self):
        elevation = np.zeros((5, 5))
        elevation[0, 1] = 500.0
        grid = make_grid(elevation=elevation)
        cost, reason = grid.evaluate_edge((0, 0), (0, 1))
        assert cost is None
        assert reason is EdgeBlock.SLOPE

    def test_slope_limit_is_exactly_the_rover_limit(self):
        """A 25-degree rover must accept 24.9 degrees and refuse 25.1."""
        rover = RoverSpec(6.0, 25.0, 0.003)
        for angle, expected in ((24.9, True), (25.1, False)):
            elevation = np.zeros((5, 5))
            elevation[0, 1] = math.tan(math.radians(angle))
            grid = make_grid(elevation=elevation, rover=rover)
            assert (grid.edge_cost((0, 0), (0, 1)) is not None) is expected


class TestSupercoverAndSegments:
    def test_supercover_includes_the_corner_cells_bresenham_skips(self):
        """The hole a plain Bresenham line-of-sight check would leave open."""
        grid = make_grid()
        cells = set(grid.supercover_cells((0, 0), (2, 2)))
        assert {(0, 0), (1, 1), (2, 2)} <= cells
        assert (0, 1) in cells and (1, 0) in cells

    def test_line_of_sight_is_blocked_by_a_corner_touch(self):
        """Two lethal cells meeting at a corner must not be drivable between."""
        hazard = np.zeros((5, 5))
        hazard[1, 2] = 0.9
        hazard[2, 1] = 0.9
        grid = make_grid(hazard=hazard, max_hazard=0.85)
        assert not grid.line_of_sight((0, 0), (4, 4))

    def test_segment_over_flat_ground_costs_its_length(self):
        grid = make_grid()
        cost, reason, hazard = grid.segment((0, 0), (9, 9))
        assert reason is None
        assert cost == pytest.approx(math.hypot(9, 9), rel=1e-6)
        assert hazard == 0.0

    def test_segment_rejects_a_ravine_with_zero_net_rise(self):
        """End-to-end slope is zero; the ground in between is a cliff both ways."""
        elevation = np.zeros((11, 11))
        elevation[5, :] = -50.0
        grid = make_grid(hazard=np.zeros((11, 11)), elevation=elevation)
        cost, reason, _ = grid.segment((0, 5), (10, 5))
        assert cost is None
        assert reason is EdgeBlock.SLOPE

    def test_elevation_line_of_sight_is_blocked_by_a_ridge(self):
        elevation = np.zeros((11, 11))
        elevation[5, :] = 40.0
        grid = make_grid(elevation=elevation)
        assert not grid.elevation_line_of_sight((0, 5), (10, 5))
        assert grid.elevation_line_of_sight((0, 5), (10, 5), height_a=100.0, height_b=100.0)


class TestMutation:
    def test_update_reports_only_cells_that_actually_moved(self):
        """Re-sensing known ground must not look like news to D* Lite."""
        grid = make_grid(hazard=np.full((5, 5), 0.3))
        assert grid.apply_hazard_update({(1, 1): 0.3}) == []
        assert grid.apply_hazard_update({(1, 1): 0.9}) == [(1, 1)]
        assert grid.hazard[1, 1] == pytest.approx(0.9)

    def test_out_of_bounds_updates_are_dropped_not_wrapped(self):
        grid = make_grid()
        assert grid.apply_hazard_update({(99, 99): 0.9}) == []

    def test_with_hazard_does_not_alias_the_original(self):
        grid = make_grid()
        other = grid.with_hazard(np.full((10, 10), 0.4))
        other.apply_hazard_update({(0, 0): 1.0})
        assert grid.hazard[0, 0] == 0.0
