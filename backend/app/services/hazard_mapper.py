"""Hazard scoring.

    hazard(x, y) = w1 * normalised_slope(x, y)
                 + w2 * obstacle_proximity_penalty(x, y)
                 + w3 * roughness(x, y)

Every term is independently normalised to [0, 1] before weighting, so with
weights summing to 1 the hazard score is itself bounded to [0, 1]. That bound
is what makes the downstream risk tiers (0.3 / 0.6) mean anything.

The weights are not hard-coded at the call site: they travel with the report as
``calculation_basis`` so any number in a generated mission report can be traced
back to the exact formula and parameters that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.services.terrain_analyzer import TerrainAnalysis

# A quantity bounded to [0, 1] cannot have a standard deviation above 0.5 - that
# is the Bernoulli maximum, attained only by a variable that is always exactly 0
# or exactly 1. First-order propagation knows nothing about the bound and will
# happily report more than that where a derivative blows up (the obstacle term
# right on an obstacle edge), so every component sigma is clipped here. The clip
# is the honest statement that linear propagation has stopped being valid, not a
# cosmetic cap.
MAX_TERM_SIGMA = 0.5


@dataclass(slots=True)
class HazardMap:
    scores: np.ndarray  # float32, [0, 1]
    components: dict[str, np.ndarray]
    calculation_basis: dict
    # One standard deviation of hazard, propagated from the per-component error
    # sources below. Same shape as ``scores``.
    uncertainty: np.ndarray | None = None
    component_uncertainty: dict[str, np.ndarray] | None = None

    @property
    def shape(self) -> tuple[int, int]:
        return self.scores.shape  # type: ignore[return-value]

    def planning_scores(self, uncertainty_weight: float = 0.0) -> np.ndarray:
        """``hazard + k * sigma``, clipped to [0, 1].

        The surface a planner should search when it is meant to prefer known
        ground over merely-average ground. ``k = 0`` returns the scores
        unchanged, which is the V1 behaviour.
        """
        if uncertainty_weight == 0.0 or self.uncertainty is None:
            return self.scores
        if uncertainty_weight < 0:
            raise ValueError("uncertainty_weight must be non-negative")
        return np.clip(self.scores + uncertainty_weight * self.uncertainty, 0.0, 1.0).astype(
            np.float32
        )


def normalise_slope(slope_deg: np.ndarray, reference_deg: float) -> np.ndarray:
    """Linear ramp saturating at ``reference_deg``.

    Saturating rather than dividing by 90 matters: real traversability
    collapses well before vertical, so a 30-degree and a 60-degree slope should
    both read as "maximally hazardous" to the scorer, and the hard traversability
    cut-off is enforced separately by the planner.
    """
    if reference_deg <= 0:
        raise ValueError("slope_reference_deg must be positive")
    return np.clip(slope_deg / reference_deg, 0.0, 1.0).astype(np.float32)


def obstacle_proximity_penalty(distance_m: np.ndarray) -> np.ndarray:
    """``1 / (1 + d)`` - 1.0 on an obstacle, decaying with metres of clearance.

    Chosen over a hard binary mask so the planner is nudged into leaving
    clearance around obstacles instead of hugging their edges.
    """
    return (1.0 / (1.0 + np.clip(distance_m, 0.0, None))).astype(np.float32)


def slope_term_sigma(
    gradient_magnitude: np.ndarray,
    roughness: np.ndarray,
    settings: Settings,
) -> np.ndarray:
    """Uncertainty in the normalised slope term, from DEM quantisation.

    The DEM is an 8-bit image, so elevation is known only to
    ``elevation_range_m / levels`` metres. A central-difference gradient over a
    ``meters_per_pixel`` baseline therefore carries a gradient error of about
    ``q / meters_per_pixel``; propagating that through ``slope = atan(g)`` gives

        sigma_slope_rad = (1 / (1 + g^2)) * sigma_g

    which is then normalised by ``slope_reference_deg`` to match the term it
    describes. The result is scaled by ``(1 + roughness)`` because a 3x3 Sobel
    kernel is a plane fit, and a plane is a worse description of rough ground
    than of smooth ground - the estimator degrades exactly where the surface
    stops being locally planar.
    """
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
    """Uncertainty in the obstacle-proximity term, from edge localisation error.

    Canny locates an edge to within about a pixel, so the distance transform
    carries roughly ``obstacle_position_sigma_px * meters_per_pixel`` of error.
    The penalty is ``1 / (1 + d)``, whose derivative is ``-1 / (1 + d)^2``, so
    the same positional error matters enormously next to an obstacle and not at
    all far from one - which is the correct behaviour and the reason for
    propagating rather than assigning a flat uncertainty.
    """
    sigma_distance_m = settings.obstacle_position_sigma_px * settings.meters_per_pixel
    clamped = np.clip(distance_m, 0.0, None)
    return np.clip(sigma_distance_m / (1.0 + clamped) ** 2, 0.0, MAX_TERM_SIGMA).astype(np.float32)


def roughness_term_sigma(roughness: np.ndarray, window: int) -> np.ndarray:
    """Sampling error of a standard deviation estimated from ``window^2`` pixels.

    For a sample standard deviation over ``n`` points, ``sigma_s ~ s / sqrt(2(n-1))``.
    A 9x9 window is 81 samples, so the estimate is good to about 8% of itself -
    small, but not zero, and it is the component that is genuinely small rather
    than the component nobody measured.
    """
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

    # Uncertainty propagation. hazard is a weighted sum of three terms whose
    # error sources are independent (DEM quantisation, edge localisation, window
    # sampling), so the variances add in quadrature:
    #     sigma_hazard = sqrt( sum_i (w_i * sigma_i)^2 )
    # This is a first-order propagation and assumes independence. The three
    # sources really are different measurements, but slope and roughness are
    # both computed from the same pixels, so the assumption is an approximation
    # that mildly under-states the total. Named here rather than buried.
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
    hazard: HazardMap, output_path: str | Path, saturate_at: float = 0.25
) -> str:
    """Write a viewable map of where the hazard estimate is least trustworthy.

    Deliberately a separate image from the hazard heatmap. Blending confidence
    into the hazard colour makes "dangerous" and "unknown" look alike, and they
    call for opposite responses: drive around the first, go and look at the
    second.
    """
    if hazard.uncertainty is None:
        raise ValueError("this hazard map carries no uncertainty field")
    normalised = np.clip(hazard.uncertainty / saturate_at, 0.0, 1.0)
    coloured = cv2.applyColorMap((normalised * 255).astype(np.uint8), cv2.COLORMAP_INFERNO)
    cv2.imwrite(str(output_path), coloured)
    return str(output_path)


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

    # COLORMAP_JET runs blue->red; inverting the input gives the green->red
    # reading operators expect (low hazard green, high hazard red).
    heat = cv2.applyColorMap((hazard.scores * 255).astype(np.uint8), cv2.COLORMAP_TURBO)
    blended = cv2.addWeighted(heat, alpha, base_bgr, 1.0 - alpha, 0)
    cv2.imwrite(str(output_path), blended)
    return str(output_path)


def downsample_for_planning(array: np.ndarray, max_dim: int) -> tuple[np.ndarray, float]:
    """Downsample a full-resolution map to the A* planning grid.

    Returns ``(grid, scale)`` where ``scale = original_dim / grid_dim``, i.e. how
    many source pixels one grid cell spans. A* on a 1024x1024 image is ~1M nodes
    with 8M edges; capping the planning grid keeps a plan interactive while the
    hazard map the user sees stays full resolution.

    ``INTER_AREA`` averages the source block rather than point-sampling it, so a
    single-pixel hazard spike is not silently dropped when it lands between
    sample points.
    """
    height, width = array.shape
    longest = max(height, width)
    if longest <= max_dim:
        return array.astype(np.float32), 1.0

    scale = longest / max_dim
    new_size = (max(1, int(round(width / scale))), max(1, int(round(height / scale))))
    resized = cv2.resize(array, new_size, interpolation=cv2.INTER_AREA)
    return resized.astype(np.float32), scale
