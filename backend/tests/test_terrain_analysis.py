"""Terrain analysis and hazard scoring.

The claim under test is that the CV stage computes real quantities: slope in
degrees that matches trigonometry on a known ramp, obstacle detection that
adapts to image contrast, and a hazard score that is bounded and traceable to
its weights.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.enums import TerrainClass
from app.services.hazard_mapper import (
    build_hazard_map,
    downsample_for_planning,
    normalise_slope,
    obstacle_proximity_penalty,
)
from app.services.terrain_analyzer import (
    adaptive_canny_thresholds,
    analyze_terrain,
    compute_roughness,
    compute_slope,
    detect_obstacles,
    distance_to_nearest_obstacle_m,
)


class TestSlope:
    def test_flat_terrain_has_zero_slope(self):
        flat = np.full((32, 32), 128.0, dtype=np.float32)
        slope_deg, gradient = compute_slope(flat, meters_per_pixel=2.0, elevation_range_m=40.0)

        assert np.allclose(slope_deg, 0.0)
        assert np.allclose(gradient, 0.0)

    def test_known_ramp_gives_the_trigonometric_answer(self):
        """A ramp rising 1 m per 1 m of ground must read as 45 degrees.

        This is the test that catches a wrong Sobel normalisation constant: an
        unnormalised 3x3 Sobel is off by a factor of 8, and without a case with
        a known answer that error is invisible.
        """
        # 255 grey levels across 255 columns, over 255 m of elevation range,
        # at 1 m/px => exactly 1 m rise per 1 m run.
        ramp = np.tile(np.arange(256, dtype=np.float32), (32, 1))
        slope_deg, gradient = compute_slope(ramp, meters_per_pixel=1.0, elevation_range_m=255.0)

        interior = slope_deg[4:-4, 4:-4]
        assert interior.mean() == pytest.approx(45.0, abs=0.5)
        assert gradient[4:-4, 4:-4].mean() == pytest.approx(1.0, abs=0.02)

    def test_slope_halves_as_ground_resolution_doubles(self):
        """Same image, coarser pixels => the same rise spread over more ground."""
        ramp = np.tile(np.arange(256, dtype=np.float32), (32, 1))
        _, fine = compute_slope(ramp, meters_per_pixel=1.0, elevation_range_m=255.0)
        _, coarse = compute_slope(ramp, meters_per_pixel=2.0, elevation_range_m=255.0)

        assert coarse[4:-4, 4:-4].mean() == pytest.approx(fine[4:-4, 4:-4].mean() / 2, rel=1e-3)

    def test_rejects_non_positive_ground_resolution(self):
        with pytest.raises(ValueError, match="meters_per_pixel must be positive"):
            compute_slope(np.zeros((8, 8), dtype=np.float32), 0.0, 40.0)


class TestRoughness:
    def test_uniform_image_has_zero_roughness(self):
        uniform = np.full((32, 32), 90.0, dtype=np.float32)
        assert np.allclose(compute_roughness(uniform, window=9), 0.0, atol=1e-6)

    def test_checkerboard_is_rougher_than_a_gradient(self):
        checker = np.indices((64, 64)).sum(axis=0) % 2 * 255.0
        gradient = np.tile(np.linspace(0, 255, 64, dtype=np.float32), (64, 1))

        assert compute_roughness(checker.astype(np.float32), 9).mean() > compute_roughness(
            gradient, 9
        ).mean()

    def test_stays_within_the_unit_range(self):
        extreme = (np.random.default_rng(0).random((64, 64)) * 255).astype(np.float32)
        roughness = compute_roughness(extreme, window=9)

        assert roughness.min() >= 0.0
        assert roughness.max() <= 1.0

    def test_even_window_is_accepted(self):
        """Box filters need an odd window; the caller should not have to know."""
        assert compute_roughness(np.zeros((16, 16), dtype=np.float32), window=8).shape == (16, 16)


class TestObstacleDetection:
    def test_thresholds_scale_with_image_contrast(self):
        """The adaptive-threshold fix, tested directly.

        A low-contrast and a high-contrast version of the same scene must not
        get the same absolute thresholds - that was the original bug, and it
        made the detector find nothing on smooth terrain.
        """
        base = np.tile(np.linspace(0, 60, 64), (64, 1)).astype(np.uint8)
        high_contrast = np.tile(np.linspace(0, 255, 64), (64, 1)).astype(np.uint8)

        low_lo, low_hi = adaptive_canny_thresholds(base, 97.0, 0.5)
        high_lo, high_hi = adaptive_canny_thresholds(high_contrast, 97.0, 0.5)

        assert high_hi > low_hi
        assert low_lo == pytest.approx(low_hi * 0.5)

    def test_finds_a_bright_disc_on_a_dark_field(self):
        import cv2

        image = np.zeros((128, 128), dtype=np.float32)
        cv2.circle(image, (64, 64), 25, 255, thickness=-1)

        obstacles, mask, metadata = detect_obstacles(
            image, canny_percentile=97.0, canny_low_ratio=0.5,
            morph_kernel=5, min_area_px=40, meters_per_pixel=2.0,
        )

        assert len(obstacles) == 1
        assert obstacles[0].x == pytest.approx(64, abs=3)
        assert obstacles[0].y == pytest.approx(64, abs=3)
        assert obstacles[0].radius_px == pytest.approx(25, abs=3)
        assert obstacles[0].area_m2 == pytest.approx(obstacles[0].area_px * 4.0)
        assert mask.any()
        assert metadata["kept_after_area_filter"] == 1

    def test_area_filter_removes_speckle(self):
        import cv2

        image = np.zeros((128, 128), dtype=np.float32)
        cv2.circle(image, (64, 64), 25, 255, thickness=-1)
        for x in range(10, 120, 20):  # 1-pixel noise dots
            image[10, x] = 255

        obstacles, _, metadata = detect_obstacles(
            image, 97.0, 0.5, morph_kernel=5, min_area_px=200, meters_per_pixel=2.0
        )
        assert len(obstacles) == 1
        assert metadata["raw_contours"] >= metadata["kept_after_area_filter"]

    def test_distance_transform_is_zero_on_an_obstacle_and_grows_outward(self):
        mask = np.zeros((64, 64), dtype=np.uint8)
        mask[30:34, 30:34] = 255
        distance = distance_to_nearest_obstacle_m(mask, meters_per_pixel=2.0)

        assert distance[31, 31] == 0.0
        assert distance[0, 0] > distance[20, 20] > 0

    def test_empty_mask_yields_a_large_finite_distance(self):
        """Not inf: the proximity penalty must stay a real number."""
        distance = distance_to_nearest_obstacle_m(np.zeros((16, 16), dtype=np.uint8), 2.0)
        assert np.isfinite(distance).all()
        assert obstacle_proximity_penalty(distance).max() == pytest.approx(0.0, abs=1e-5)


class TestHazardScoring:
    def test_slope_term_saturates_at_the_reference_angle(self):
        slopes = np.array([[0.0, 15.0, 30.0, 60.0, 89.0]], dtype=np.float32)
        normalised = normalise_slope(slopes, reference_deg=30.0)

        assert list(normalised[0]) == pytest.approx([0.0, 0.5, 1.0, 1.0, 1.0])

    def test_proximity_penalty_decays_from_one(self):
        distances = np.array([[0.0, 1.0, 9.0]], dtype=np.float32)
        penalty = obstacle_proximity_penalty(distances)

        assert list(penalty[0]) == pytest.approx([1.0, 0.5, 0.1])

    def test_hazard_is_bounded_to_the_unit_interval(self, settings, synthetic_terrain_path):
        analysis = analyze_terrain(synthetic_terrain_path, settings)
        hazard = build_hazard_map(analysis, settings)

        assert hazard.scores.min() >= 0.0
        assert hazard.scores.max() <= 1.0
        assert hazard.scores.shape == analysis.slope_deg.shape

    def test_weights_are_recorded_for_audit(self, settings, synthetic_terrain_path):
        analysis = analyze_terrain(synthetic_terrain_path, settings)
        basis = build_hazard_map(analysis, settings).calculation_basis

        assert basis["weights"]["slope"] == settings.hazard_w_slope
        assert basis["weights"]["obstacle_proximity"] == settings.hazard_w_obstacle
        assert basis["weights"]["roughness"] == settings.hazard_w_roughness
        assert "formula" in basis

    def test_rejects_weights_that_do_not_sum_to_one(self, settings, synthetic_terrain_path):
        """Weights summing to more than 1 would silently break the [0,1] bound
        that the risk tiers depend on, so it is a hard error."""
        analysis = analyze_terrain(synthetic_terrain_path, settings)
        broken = settings.model_copy(update={"hazard_w_slope": 0.9})

        with pytest.raises(ValueError, match="must sum to 1.0"):
            build_hazard_map(analysis, broken)


class TestDownsampling:
    def test_leaves_small_grids_untouched(self):
        grid = np.ones((50, 50), dtype=np.float32)
        result, scale = downsample_for_planning(grid, max_dim=192)

        assert result.shape == (50, 50)
        assert scale == 1.0

    def test_caps_the_longest_edge(self):
        grid = np.ones((1024, 512), dtype=np.float32)
        result, scale = downsample_for_planning(grid, max_dim=192)

        assert max(result.shape) == 192
        assert scale == pytest.approx(1024 / 192)

    def test_area_averaging_preserves_a_hazard_spike(self):
        """Point-sampling would drop a single-pixel spike between sample points;
        INTER_AREA averages the block instead, so it survives as elevated cost."""
        grid = np.zeros((512, 512), dtype=np.float32)
        grid[256, 256] = 1.0
        result, _ = downsample_for_planning(grid, max_dim=128)

        assert result.max() > 0.0


class TestClassification:
    @pytest.mark.parametrize(
        ("preset", "expected"),
        [
            ("sandy_plain", TerrainClass.SANDY_PLAIN),
            ("rocky_highland", TerrainClass.ROCKY_HIGHLAND),
            ("crater_field", TerrainClass.CRATER_FIELD),
        ],
    )
    def test_generated_presets_classify_as_intended(
        self, settings, tmp_path, preset, expected
    ):
        """End-to-end check of the rule set against terrain built to be that type.

        This is a self-consistency test, not a validation against real labelled
        Mars terrain - which does not exist in this repo and is not claimed.
        """
        import sys

        import cv2

        sys.path.insert(0, str(synthetic_scripts_dir()))
        from generate_terrain import generate

        image_path = tmp_path / f"{preset}.png"
        cv2.imwrite(str(image_path), generate(preset, 512, seed=42 + _preset_index(preset)))

        assert analyze_terrain(image_path, settings).classification == expected

    def test_records_which_rule_fired(self, settings, synthetic_terrain_path):
        evidence = analyze_terrain(synthetic_terrain_path, settings).stats[
            "classification_evidence"
        ]

        assert "rule_fired" in evidence
        assert "rule_thresholds" in evidence
        assert evidence["obstacle_area_fraction"] >= 0.0


def synthetic_scripts_dir():
    from pathlib import Path

    return Path(__file__).resolve().parent.parent.parent / "scripts"


def _preset_index(preset: str) -> int:
    return ["sandy_plain", "rocky_highland", "crater_field"].index(preset)


class TestPipelineIntegration:
    def test_full_analysis_returns_consistent_shapes_and_stats(
        self, settings, synthetic_terrain_path
    ):
        analysis = analyze_terrain(synthetic_terrain_path, settings)

        shape = analysis.slope_deg.shape
        for array in (
            analysis.elevation_m,
            analysis.gradient_magnitude,
            analysis.roughness,
            analysis.obstacle_mask,
            analysis.distance_to_obstacle_m,
        ):
            assert array.shape == shape

        assert analysis.stats["slope"]["max_deg"] >= analysis.stats["slope"]["mean_deg"]
        assert 0.0 <= analysis.stats["obstacle_area_fraction"] <= 1.0
        assert analysis.stats["parameters"]["meters_per_pixel"] == settings.meters_per_pixel

    def test_slope_degrees_are_physically_possible(self, settings, synthetic_terrain_path):
        analysis = analyze_terrain(synthetic_terrain_path, settings)
        assert 0.0 <= analysis.stats["slope"]["max_deg"] < 90.0

    def test_gradient_magnitude_is_the_tangent_of_the_slope(
        self, settings, synthetic_terrain_path
    ):
        analysis = analyze_terrain(synthetic_terrain_path, settings)
        sample_slope = float(analysis.slope_deg[64, 64])
        sample_gradient = float(analysis.gradient_magnitude[64, 64])

        assert math.tan(math.radians(sample_slope)) == pytest.approx(sample_gradient, abs=1e-4)

    def test_missing_image_raises_a_clear_error(self, settings):
        with pytest.raises(FileNotFoundError, match="Could not read terrain image"):
            analyze_terrain("/nonexistent/terrain.png", settings)
