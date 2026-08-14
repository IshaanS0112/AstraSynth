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


@dataclass(slots=True)
class HazardMap:
    scores: np.ndarray  # float32, [0, 1]
    components: dict[str, np.ndarray]
    calculation_basis: dict

    @property
    def shape(self) -> tuple[int, int]:
        return self.scores.shape  # type: ignore[return-value]


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
    }

    return HazardMap(
        scores=scores,
        components={
            "slope": slope_term,
            "obstacle_proximity": obstacle_term,
            "roughness": roughness_term,
        },
        calculation_basis=basis,
    )


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
