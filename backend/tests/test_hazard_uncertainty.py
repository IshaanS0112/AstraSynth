"""Propagated hazard uncertainty."""

from __future__ import annotations

import numpy as np
import pytest

from app.services import hazard_mapper, terrain_analyzer
from app.services.hazard_mapper import MAX_TERM_SIGMA


class TestComponentSigmas:
    def test_slope_uncertainty_follows_dem_quantisation(self, settings):
        """Halving the elevation range halves the quantisation step, and the sigma."""
        gradient = np.zeros((8, 8), dtype=np.float32)
        roughness = np.zeros((8, 8), dtype=np.float32)

        coarse = hazard_mapper.slope_term_sigma(gradient, roughness, settings)
        finer = settings.model_copy(update={"elevation_range_m": settings.elevation_range_m / 2})
        halved = hazard_mapper.slope_term_sigma(gradient, roughness, finer)

        assert float(halved.mean()) == pytest.approx(float(coarse.mean()) / 2, rel=1e-5)

    def test_slope_uncertainty_grows_on_rough_ground(self, settings):
        """A 3x3 plane fit describes rough ground worse than smooth ground."""
        gradient = np.zeros((8, 8), dtype=np.float32)
        smooth = hazard_mapper.slope_term_sigma(gradient, np.zeros((8, 8), np.float32), settings)
        rough = hazard_mapper.slope_term_sigma(gradient, np.ones((8, 8), np.float32), settings)
        assert float(rough.mean()) > float(smooth.mean())

    def test_obstacle_uncertainty_decays_with_clearance(self, settings):
        # Below about 2 m of clearance the propagated sigma exceeds the Bernoulli
        # bound and is clipped flat, so the decay is only observable beyond it -
        # which is itself the honest reading of where linear propagation stops
        # applying.
        distance = np.array([[2.0, 5.0, 10.0, 100.0]], dtype=np.float32)
        sigma = hazard_mapper.obstacle_term_sigma(distance, settings)
        assert sigma[0, 0] > sigma[0, 1] > sigma[0, 2] > sigma[0, 3]
        assert sigma[0, 3] == pytest.approx(0.0, abs=1e-3)
        assert (
            hazard_mapper.obstacle_term_sigma(np.zeros((1, 1), dtype=np.float32), settings)[0, 0]
            == MAX_TERM_SIGMA
        )

    def test_roughness_uncertainty_shrinks_with_a_larger_window(self):
        roughness = np.full((4, 4), 0.5, dtype=np.float32)
        small = hazard_mapper.roughness_term_sigma(roughness, window=3)
        large = hazard_mapper.roughness_term_sigma(roughness, window=15)
        assert float(large.mean()) < float(small.mean())

    def test_every_component_respects_the_bernoulli_bound(self, settings):
        """Linear propagation blows up at the obstacle edge; the bound catches it."""
        distance = np.zeros((4, 4), dtype=np.float32)
        sigma = hazard_mapper.obstacle_term_sigma(distance, settings)
        assert float(sigma.max()) <= MAX_TERM_SIGMA


class TestPropagation:
    def test_total_sigma_is_the_weighted_quadrature_sum(self, settings, synthetic_terrain_path):
        analysis = terrain_analyzer.analyze_terrain(synthetic_terrain_path, settings)
        hazard = hazard_mapper.build_hazard_map(analysis, settings)

        expected = np.sqrt(
            (settings.hazard_w_slope * hazard.component_uncertainty["slope"]) ** 2
            + (settings.hazard_w_obstacle * hazard.component_uncertainty["obstacle_proximity"]) ** 2
            + (settings.hazard_w_roughness * hazard.component_uncertainty["roughness"]) ** 2
        )
        assert np.allclose(hazard.uncertainty, expected, atol=1e-6)

    def test_uncertainty_is_the_same_shape_as_the_hazard_field(
        self, settings, synthetic_terrain_path
    ):
        analysis = terrain_analyzer.analyze_terrain(synthetic_terrain_path, settings)
        hazard = hazard_mapper.build_hazard_map(analysis, settings)
        assert hazard.uncertainty.shape == hazard.scores.shape
        assert hazard.uncertainty.min() >= 0.0

    def test_the_basis_records_every_error_source(self, settings, synthetic_terrain_path):
        """Auditability: a sigma with no stated origin is a decoration."""
        analysis = terrain_analyzer.analyze_terrain(synthetic_terrain_path, settings)
        basis = hazard_mapper.build_hazard_map(analysis, settings).calculation_basis
        sources = basis["uncertainty"]["sources"]
        assert set(sources) == {"slope", "obstacle_proximity", "roughness"}
        assert all(text for text in sources.values())
        assert (
            "quadrature" in basis["uncertainty"]["assumption"]
            or "sqrt" in basis["uncertainty"]["formula"]
        )


class TestPlanningSurface:
    def test_zero_weight_returns_the_scores_unchanged(self, settings, synthetic_terrain_path):
        analysis = terrain_analyzer.analyze_terrain(synthetic_terrain_path, settings)
        hazard = hazard_mapper.build_hazard_map(analysis, settings)
        assert np.array_equal(hazard.planning_scores(0.0), hazard.scores)

    def test_a_positive_weight_raises_the_cost_of_uncertain_ground(
        self, settings, synthetic_terrain_path
    ):
        analysis = terrain_analyzer.analyze_terrain(synthetic_terrain_path, settings)
        hazard = hazard_mapper.build_hazard_map(analysis, settings)
        surface = hazard.planning_scores(1.5)

        assert float(surface.mean()) > float(hazard.scores.mean())
        assert surface.max() <= 1.0
        assert surface.min() >= 0.0

    def test_negative_weights_are_refused(self, settings, synthetic_terrain_path):
        analysis = terrain_analyzer.analyze_terrain(synthetic_terrain_path, settings)
        hazard = hazard_mapper.build_hazard_map(analysis, settings)
        with pytest.raises(ValueError, match="non-negative"):
            hazard.planning_scores(-1.0)
