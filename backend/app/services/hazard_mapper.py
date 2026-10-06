"""Hazard scoring."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.services.terrain_analyzer import TerrainAnalysis

# Bernoulli maximum: a [0, 1]-bounded quantity cannot have sigma above 0.5.
# First-order propagation does not know about the bound and reports more where a
# derivative blows up, so every component sigma is clipped to this. The clip
# marks where linear propagation stops being valid.
MAX_TERM_SIGMA = 0.5


@dataclass(slots=True)
class HazardMap:
    scores: np.ndarray  # float32, [0, 1]
    components: dict[str, np.ndarray]
    calculation_basis: dict
    # One sigma of hazard, propagated from the component error sources.
    uncertainty: np.ndarray | None = None
    component_uncertainty: dict[str, np.ndarray] | None = None

    @property
    def shape(self) -> tuple[int, int]:
        return self.scores.shape  # type: ignore[return-value]

    def planning_scores(self, uncertainty_weight: float = 0.0) -> np.ndarray:
        """``hazard + k * sigma``, clipped to [0, 1]."""
        if uncertainty_weight == 0.0 or self.uncertainty is None:
            return self.scores
        if uncertainty_weight < 0:
            raise ValueError("uncertainty_weight must be non-negative")
        return np.clip(self.scores + uncertainty_weight * self.uncertainty, 0.0, 1.0).astype(
            np.float32
        )


def normalise_slope(slope_deg: np.ndarray, reference_deg: float) -> np.ndarray:
    """Linear ramp saturating at ``reference_deg``."""
    if reference_deg <= 0:
        raise ValueError("slope_reference_deg must be positive")
    return np.clip(slope_deg / reference_deg, 0.0, 1.0).astype(np.float32)


def obstacle_proximity_penalty(distance_m: np.ndarray) -> np.ndarray:
    """``1 / (1 + d)`` - 1.0 on an obstacle, decaying with metres of clearance."""
    return (1.0 / (1.0 + np.clip(distance_m, 0.0, None))).astype(np.float32)


def slope_term_sigma(
    gradient_magnitude: np.ndarray,
    roughness: np.ndarray,
    settings: Settings,
) -> np.ndarray:
    """Uncertainty in the normalised slope term, from DEM quantisation."""
    quantum_m = settings.elevation_range_m / settings.dem_quantisation_levels
    sigma_gradient = quantum_m / settings.meters_per_pixel
    sigma_slope_rad = sigma_gradient / (1.0 + gradient_magnitude**2)
    sigma_slope_deg = np.degrees(sigma_slope_rad)
    return np.clip(
        (sigma_slope_deg / settings.slope_reference_deg) * (1.0 + roughness),
        0.0,
        MAX_TERM_SIGMA,
    ).astype(np.float32)


def obstacle_term_sigma(distance_m: np.ndarray, settings: Settings) -> np.ndarray:
    """Uncertainty in the obstacle-proximity term, from edge localisation error."""
    sigma_distance_m = settings.obstacle_position_sigma_px * settings.meters_per_pixel
    clamped = np.clip(distance_m, 0.0, None)
    return np.clip(sigma_distance_m / (1.0 + clamped) ** 2, 0.0, MAX_TERM_SIGMA).astype(np.float32)


def roughness_term_sigma(roughness: np.ndarray, window: int) -> np.ndarray:
    """Sampling error of a standard deviation estimated from ``window^2`` pixels."""
    n = max(window * window, 2)
    return np.clip(roughness / np.sqrt(2.0 * (n - 1)), 0.0, MAX_TERM_SIGMA).astype(np.float32)


def build_hazard_map(analysis: TerrainAnalysis, settings: Settings) -> HazardMap:
    w1 = settings.hazard_w_slope
    w2 = settings.hazard_w_obstacle
    w3 = settings.hazard_w_roughness
    weight_sum = w1 + w2 + w3
    if abs(weight_sum - 1.0) > 1e-6:
        raise ValueError(f"Hazard weights must sum to 1.0, got {weight_sum:.4f}")

    slope_term = normalise_slope(analysis.slope_deg, settings.slope_reference_deg)
    obstacle_term = obstacle_proximity_penalty(analysis.distance_to_obstacle_m)
    roughness_term = analysis.roughness

    scores = np.clip(w1 * slope_term + w2 * obstacle_term + w3 * roughness_term, 0.0, 1.0).astype(
        np.float32
    )

    # hazard is a weighted sum of three terms with independent error sources, so
    # variances add in quadrature: sigma = sqrt(sum_i (w_i * sigma_i)^2).
    # First-order and assumes independence - slope and roughness share pixels, so
    # this mildly under-states the total. See docs/uncertainty.md.
    sigma_slope = slope_term_sigma(analysis.gradient_magnitude, analysis.roughness, settings)
    sigma_obstacle = obstacle_term_sigma(analysis.distance_to_obstacle_m, settings)
    sigma_roughness = roughness_term_sigma(analysis.roughness, settings.roughness_window)
    uncertainty = np.sqrt(
        (w1 * sigma_slope) ** 2 + (w2 * sigma_obstacle) ** 2 + (w3 * sigma_roughness) ** 2
    ).astype(np.float32)

    basis = {
        "formula": (
            "hazard = w_slope * min(slope_deg / slope_reference_deg, 1) "
            "+ w_obstacle * 1/(1 + distance_to_obstacle_m) "
            "+ w_roughness * normalised_local_std"
        ),
        "weights": {"slope": w1, "obstacle_proximity": w2, "roughness": w3},
        "slope_reference_deg": settings.slope_reference_deg,
        "roughness_window": settings.roughness_window,
        "aggregate": {
            "mean_hazard": round(float(scores.mean()), 4),
            "max_hazard": round(float(scores.max()), 4),
            "p95_hazard": round(float(np.percentile(scores, 95)), 4),
            "fraction_above_0_6": round(float((scores > 0.6).mean()), 4),
        },
        "component_means": {
            "slope_term": round(float(slope_term.mean()), 4),
            "obstacle_term": round(float(obstacle_term.mean()), 4),
            "roughness_term": round(float(roughness_term.mean()), 4),
        },
        "uncertainty": {
            "formula": "sigma_hazard = sqrt(sum_i (w_i * sigma_i)^2)",
            "assumption": (
                "first-order propagation with independent component errors; each "
                f"component sigma clipped to {MAX_TERM_SIGMA} (the maximum standard "
                "deviation a [0,1]-bounded quantity can have)"
            ),
            "sources": {
                "slope": (
                    f"DEM quantisation at {settings.elevation_range_m}m / "
                    f"{settings.dem_quantisation_levels} levels, propagated through "
                    "atan and scaled by (1 + roughness)"
                ),
                "obstacle_proximity": (
                    f"Canny edge localisation at {settings.obstacle_position_sigma_px} px, "
                    "propagated through d/dd of 1/(1+d)"
                ),
                "roughness": "sampling error of a std over the roughness window",
            },
            "mean_sigma": round(float(uncertainty.mean()), 5),
            "max_sigma": round(float(uncertainty.max()), 5),
            "p95_sigma": round(float(np.percentile(uncertainty, 95)), 5),
            "component_mean_sigma": {
                "slope": round(float(sigma_slope.mean()), 5),
                "obstacle_proximity": round(float(sigma_obstacle.mean()), 5),
                "roughness": round(float(sigma_roughness.mean()), 5),
            },
        },
    }

    return HazardMap(
        scores=scores,
        components={
            "slope": slope_term,
            "obstacle_proximity": obstacle_term,
            "roughness": roughness_term,
        },
        calculation_basis=basis,
        uncertainty=uncertainty,
        component_uncertainty={
            "slope": sigma_slope,
            "obstacle_proximity": sigma_obstacle,
            "roughness": sigma_roughness,
        },
    )


def render_uncertainty_map(
    hazard: HazardMap, output_path: str | Path, saturate_at: float | None = None
) -> tuple[str, float]:
    """Write a viewable map of where the hazard estimate is least trustworthy."""
    if hazard.uncertainty is None:
        raise ValueError("this hazard map carries no uncertainty field")
    if saturate_at is None:
        # A uniform field would divide by zero and render as noise.
        saturate_at = max(float(np.percentile(hazard.uncertainty, 99)), 1e-6)
    normalised = np.clip(hazard.uncertainty / saturate_at, 0.0, 1.0)
    coloured = cv2.applyColorMap((normalised * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    cv2.imwrite(str(output_path), coloured)
    return str(output_path), float(saturate_at)


def render_hazard_heatmap(
    hazard: HazardMap,
    terrain_image_path: str | Path,
    output_path: str | Path,
    alpha: float = 0.55,
) -> str:
    """Green-to-red hazard heatmap alpha-blended over the terrain image."""
    base = cv2.imread(str(terrain_image_path), cv2.IMREAD_GRAYSCALE)
    if base is None:
        raise FileNotFoundError(f"Could not read terrain image: {terrain_image_path}")
    base_bgr = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR)

    # JET runs blue->red; inverted gives green (safe) -> red (hazardous).
    heat = cv2.applyColorMap((hazard.scores * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    blended = cv2.addWeighted(heat, alpha, base_bgr, 1.0 - alpha, 0)
    cv2.imwrite(str(output_path), blended)
    return str(output_path)


def downsample_for_planning(array: np.ndarray, max_dim: int) -> tuple[np.ndarray, float]:
    """Downsample a full-resolution map to the A* planning grid."""
    height, width = array.shape
    longest = max(height, width)
    if longest <= max_dim:
        return array.astype(np.float32), 1.0

    scale = longest / max_dim
    new_size = (max(1, int(round(width / scale))), max(1, int(round(height / scale))))
    resized = cv2.resize(array, new_size, interpolation=cv2.INTER_AREA)
    return resized.astype(np.float32), scale
