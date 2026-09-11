"""Belief, observation, and the gap between them and reality.

The property these tests exist to protect is that a planner cannot reach the
truth. Everything else here - Bayesian arithmetic, occlusion geometry, the
uncertainty-averse planning surface - is in service of that separation being
real rather than nominal.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.simulation.belief import BeliefState
from app.services.simulation.sensors import RoverSensor

ROVER = RoverSpec(6.0, 25.0, 0.003)


def board(hazard, elevation=None, meters_per_cell=1.0) -> PlanningGrid:
    elevation = np.zeros_like(hazard) if elevation is None else elevation
    return PlanningGrid(hazard, elevation, meters_per_cell, ROVER)


class TestBayesianUpdate:
    def test_an_observation_tightens_the_belief(self):
        belief = BeliefState.from_prior(np.full((5, 5), 0.3), prior_sigma=0.25)
        before = float(belief.sigma[2, 2])
        belief.update({(2, 2): 0.8}, observation_sigma=0.05)
        assert float(belief.sigma[2, 2]) < before

    def test_repeated_observations_converge_on_the_truth(self):
        belief = BeliefState.from_prior(np.full((5, 5), 0.1), prior_sigma=0.25)
        for _ in range(50):
            belief.update({(2, 2): 0.9}, observation_sigma=0.05)
        assert float(belief.mean[2, 2]) == pytest.approx(0.9, abs=0.01)
        assert float(belief.sigma[2, 2]) < 0.01

    def test_a_precise_sensor_moves_the_mean_further_than_a_vague_one(self):
        precise = BeliefState.from_prior(np.full((3, 3), 0.2), prior_sigma=0.25)
        vague = BeliefState.from_prior(np.full((3, 3), 0.2), prior_sigma=0.25)
        precise.update({(1, 1): 0.9}, observation_sigma=0.02)
        vague.update({(1, 1): 0.9}, observation_sigma=0.4)
        assert float(precise.mean[1, 1]) > float(vague.mean[1, 1])

    def test_a_confirming_observation_reports_no_change(self):
        """What stops a settled belief from triggering endless replans."""
        belief = BeliefState.from_prior(np.full((5, 5), 0.4), prior_sigma=0.25)
        for _ in range(200):
            belief.update({(2, 2): 0.4}, observation_sigma=0.05)
        assert belief.update({(2, 2): 0.4}, observation_sigma=0.05) == {}

    def test_a_zero_noise_sensor_is_refused(self):
        belief = BeliefState.from_prior(np.zeros((3, 3)))
        with pytest.raises(ValueError, match="positive"):
            belief.update({(1, 1): 0.5}, observation_sigma=0.0)


class TestPlanningSurface:
    def test_zero_weight_returns_the_mean_unchanged(self):
        belief = BeliefState.from_prior(np.full((4, 4), 0.3))
        assert np.array_equal(belief.planning_hazard(0.0), belief.mean)

    def test_unsurveyed_ground_costs_more_than_surveyed_ground(self):
        """The behaviour the whole uncertainty model exists to produce."""
        belief = BeliefState.from_prior(np.full((4, 4), 0.4), prior_sigma=0.25)
        for _ in range(30):
            belief.update({(1, 1): 0.4}, observation_sigma=0.05)

        surface = belief.planning_hazard(uncertainty_weight=1.5)
        assert surface[1, 1] < surface[3, 3]
        assert float(belief.mean[1, 1]) == pytest.approx(float(belief.mean[3, 3]), abs=0.01)

    def test_the_surface_stays_inside_the_hazard_scale(self):
        belief = BeliefState.from_prior(np.full((4, 4), 0.95), prior_sigma=0.4)
        surface = belief.planning_hazard(uncertainty_weight=5.0)
        assert surface.min() >= 0.0
        assert surface.max() <= 1.0

    def test_negative_weights_are_refused(self):
        belief = BeliefState.from_prior(np.zeros((3, 3)))
        with pytest.raises(ValueError, match="non-negative"):
            belief.planning_hazard(-1.0)


class TestSensor:
    def test_nothing_beyond_range_is_observed(self):
        grid = board(np.zeros((21, 21)), meters_per_cell=2.0)
        sensor = RoverSensor(range_m=6.0, respect_occlusion=False)
        observed = sensor.observe(np.zeros((21, 21)), grid, (10, 10), np.random.default_rng(0))
        for cell in observed:
            assert grid.distance_m((10, 10), cell) <= 6.0 + 1e-9
        assert (10, 13) in observed  # 6 m away
        assert (10, 14) not in observed  # 8 m away

    def test_a_ridge_hides_the_ground_behind_it(self):
        elevation = np.zeros((21, 21))
        elevation[10, :] = 30.0
        grid = board(np.zeros((21, 21)), elevation)
        sensor = RoverSensor(range_m=30.0)

        assert sensor.visible(grid, (5, 10), (7, 10))
        assert not sensor.visible(grid, (5, 10), (15, 10))

    def test_disabling_occlusion_reveals_the_far_side(self):
        elevation = np.zeros((21, 21))
        elevation[10, :] = 30.0
        grid = board(np.zeros((21, 21)), elevation)
        assert RoverSensor(range_m=30.0, respect_occlusion=False).visible(grid, (5, 10), (15, 10))

    def test_readings_are_noisy_but_stay_on_the_hazard_scale(self):
        truth = np.full((11, 11), 0.5)
        grid = board(np.zeros((11, 11)))
        sensor = RoverSensor(range_m=5.0, noise_sigma=0.2, respect_occlusion=False)
        observed = sensor.observe(truth, grid, (5, 5), np.random.default_rng(1))

        values = list(observed.values())
        assert all(0.0 <= v <= 1.0 for v in values)
        assert len(set(values)) > 1, "a noisy sensor must not return identical readings"
        assert np.mean(values) == pytest.approx(0.5, abs=0.06)

    def test_a_noiseless_sensor_is_refused(self):
        with pytest.raises(ValueError, match="degenerate"):
            RoverSensor(range_m=5.0, noise_sigma=0.0)

    def test_observations_are_reproducible_for_a_given_seed(self):
        truth = np.full((11, 11), 0.5)
        grid = board(np.zeros((11, 11)))
        sensor = RoverSensor(range_m=5.0, respect_occlusion=False)
        first = sensor.observe(truth, grid, (5, 5), np.random.default_rng(3))
        second = sensor.observe(truth, grid, (5, 5), np.random.default_rng(3))
        assert first == second
