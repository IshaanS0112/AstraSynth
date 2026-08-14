"""Terrain analysis pipeline (OpenCV).

The input is a single-channel terrain image interpreted as a coarse digital
elevation model: intensity 0-255 maps linearly onto ``[0, elevation_range_m]``
metres. That mapping plus ``meters_per_pixel`` is what lets pixel gradients be
converted into real slope angles rather than unitless "steepness" numbers.

Three products come out of this module, all as float arrays the same shape as
the input image:

* ``slope_deg``  - per-pixel terrain slope in degrees (Sobel).
* ``obstacle_mask`` + contour list - discrete obstacle regions (Canny + contours).
* ``roughness``  - normalised local intensity standard deviation.

None of this is decorative. The hazard mapper, the A* cost function and the
risk engine all consume these arrays directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.enums import TerrainClass

# Local intensity std of a [0,1] image is bounded by 0.5 (a half-black /
# half-white window). Dividing by that gives roughness a real [0,1] range.
_MAX_LOCAL_STD = 0.5


@dataclass(slots=True)
class Obstacle:
    """One detected obstacle region (crater rim, rock cluster, ridge)."""

    id: int
    x: int
    y: int
    area_px: float
    radius_px: float
    area_m2: float

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "x": self.x,
            "y": self.y,
            "area_px": round(self.area_px, 2),
            "radius_px": round(self.radius_px, 2),
            "area_m2": round(self.area_m2, 2),
        }


@dataclass(slots=True)
class TerrainAnalysis:
    """Everything the CV stage computes, in raw array form."""

    elevation_m: np.ndarray
    slope_deg: np.ndarray
    gradient_magnitude: np.ndarray  # tan(slope), dimensionless rise/run
    roughness: np.ndarray  # normalised local std, [0, 1]
    obstacle_mask: np.ndarray  # uint8, 255 inside obstacle regions
    distance_to_obstacle_m: np.ndarray
    obstacles: list[Obstacle]
    classification: TerrainClass
    stats: dict = field(default_factory=dict)

    @property
    def shape(self) -> tuple[int, int]:
        return self.slope_deg.shape  # type: ignore[return-value]


def load_terrain_image(path: str | Path) -> np.ndarray:
    """Read a terrain image as single-channel float32 in [0, 255]."""
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Could not read terrain image: {path}")
    return image.astype(np.float32)


def compute_slope(
    gray: np.ndarray, meters_per_pixel: float, elevation_range_m: float
) -> tuple[np.ndarray, np.ndarray]:
    """Sobel slope estimation.

    Returns ``(slope_deg, gradient_magnitude)`` where gradient magnitude is the
    dimensionless rise-over-run, i.e. ``tan(slope)``.

    The ``/ 8`` divisor is the normalisation constant for the 3x3 Sobel kernel;
    without it the "slope" is off by a constant factor and the degrees are
    meaningless.
    """
    if meters_per_pixel <= 0:
        raise ValueError("meters_per_pixel must be positive")

    elevation_m = (gray / 255.0) * elevation_range_m
    dz_dx = cv2.Sobel(elevation_m, cv2.CV_32F, 1, 0, ksize=3) / (8.0 * meters_per_pixel)
    dz_dy = cv2.Sobel(elevation_m, cv2.CV_32F, 0, 1, ksize=3) / (8.0 * meters_per_pixel)

    gradient_magnitude = np.sqrt(dz_dx**2 + dz_dy**2)
    slope_deg = np.degrees(np.arctan(gradient_magnitude))
    return slope_deg.astype(np.float32), gradient_magnitude.astype(np.float32)


def adaptive_canny_thresholds(
    blurred_u8: np.ndarray, percentile: float, low_ratio: float
) -> tuple[float, float]:
    """Derive Canny thresholds from this image's own gradient distribution.

    Fixed thresholds do not survive contact with real terrain: a smooth
    low-relief DEM has gradient magnitudes in the single digits and a fixed
    (60, 160) pair detects literally nothing, while a high-contrast rocky tile
    saturates and detects everything. Taking the high threshold at a percentile
    of the observed gradient magnitude makes the detector scale-invariant with
    respect to image contrast - the percentile fixes roughly *what fraction* of
    the image is treated as edge, which is the property actually wanted.
    """
    gx = cv2.Sobel(blurred_u8, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blurred_u8, cv2.CV_32F, 0, 1, ksize=3)
    magnitude = np.sqrt(gx**2 + gy**2)
    high = max(1.0, float(np.percentile(magnitude, percentile)))
    return high * low_ratio, high


def detect_obstacles(
    gray: np.ndarray,
    canny_percentile: float,
    canny_low_ratio: float,
    morph_kernel: int,
    min_area_px: int,
    meters_per_pixel: float,
) -> tuple[list[Obstacle], np.ndarray, dict]:
    """Canny edge detection -> contour extraction -> obstacle regions.

    A morphological close sits between the two steps: raw Canny output is a set
    of thin, frequently broken edge fragments, and ``findContours`` on that
    yields hundreds of slivers rather than the handful of coherent regions a
    planner cares about. ``L2gradient=True`` so Canny's internal magnitude
    matches the one the thresholds were derived from.
    """
    blurred = cv2.GaussianBlur(gray.astype(np.uint8), (5, 5), 0)
    low, high = adaptive_canny_thresholds(blurred, canny_percentile, canny_low_ratio)
    edges = cv2.Canny(blurred, low, high, L2gradient=True)

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (morph_kernel, morph_kernel))
    closed = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    obstacles: list[Obstacle] = []
    mask = np.zeros(gray.shape, dtype=np.uint8)
    px_area_m2 = meters_per_pixel**2

    for contour in contours:
        area = float(cv2.contourArea(contour))
        if area < min_area_px:
            continue
        (cx, cy), radius = cv2.minEnclosingCircle(contour)
        obstacles.append(
            Obstacle(
                id=len(obstacles),
                x=int(round(cx)),
                y=int(round(cy)),
                area_px=area,
                radius_px=float(radius),
                area_m2=area * px_area_m2,
            )
        )
        cv2.drawContours(mask, [contour], -1, color=255, thickness=cv2.FILLED)

    detector_metadata = {
        "canny_low": round(low, 2),
        "canny_high": round(high, 2),
        "canny_gradient_percentile": canny_percentile,
        "morph_close_kernel": morph_kernel,
        "raw_contours": len(contours),
        "kept_after_area_filter": len(obstacles),
        "min_obstacle_area_px": min_area_px,
    }
    return obstacles, mask, detector_metadata


def compute_roughness(gray: np.ndarray, window: int) -> np.ndarray:
    """Normalised local intensity standard deviation in an NxN window.

    ``E[x^2] - E[x]^2`` via two box filters is O(1) per pixel regardless of
    window size, which matters because this runs over every pixel.
    """
    if window % 2 == 0:
        window += 1  # box filter needs an odd, centred window
    normalised = gray / 255.0
    ksize = (window, window)
    mean = cv2.boxFilter(normalised, cv2.CV_32F, ksize)
    mean_sq = cv2.boxFilter(normalised**2, cv2.CV_32F, ksize)
    variance = np.clip(mean_sq - mean**2, 0.0, None)
    return np.clip(np.sqrt(variance) / _MAX_LOCAL_STD, 0.0, 1.0).astype(np.float32)


def distance_to_nearest_obstacle_m(
    obstacle_mask: np.ndarray, meters_per_pixel: float
) -> np.ndarray:
    """Euclidean distance transform, in metres, from every pixel to an obstacle.

    If nothing was detected the distance is undefined; a large finite value is
    returned so the proximity penalty cleanly collapses to ~0.
    """
    if not obstacle_mask.any():
        return np.full(obstacle_mask.shape, 1e6, dtype=np.float32)
    free_space = np.where(obstacle_mask > 0, 0, 255).astype(np.uint8)
    distance_px = cv2.distanceTransform(free_space, cv2.DIST_L2, maskSize=5)
    return (distance_px * meters_per_pixel).astype(np.float32)


def classify_terrain(
    slope_deg: np.ndarray,
    roughness: np.ndarray,
    obstacles: list[Obstacle],
    obstacle_mask: np.ndarray,
    settings: Settings,
) -> tuple[TerrainClass, dict]:
    """Rule-based terrain classification.

    Deliberately not a neural network. See docs/architecture.md - with no
    labelled planetary terrain set to train on, an explainable threshold rule
    that can be inspected and defended beats an under-trained classifier whose
    outputs cannot be justified.

    The discriminating feature for a crater field is obstacle *area*, not
    obstacle *count*: a rock-strewn highland produces several times more
    contours than a crater field but each is tiny, so counting them ranks the
    two backwards. Craters are few and large.
    """
    height, width = slope_deg.shape
    megapixels = (height * width) / 1e6
    obstacle_density = len(obstacles) / megapixels if megapixels > 0 else 0.0
    area_fraction = float((obstacle_mask > 0).mean())
    mean_slope = float(np.mean(slope_deg))
    mean_roughness = float(np.mean(roughness))
    mean_radius = float(np.mean([o.radius_px for o in obstacles])) if obstacles else 0.0

    evidence = {
        "mean_slope_deg": round(mean_slope, 3),
        "mean_roughness": round(mean_roughness, 4),
        "obstacle_density_per_mpx": round(obstacle_density, 2),
        "obstacle_area_fraction": round(area_fraction, 4),
        "mean_obstacle_radius_px": round(mean_radius, 2),
        "rule_thresholds": {
            "crater_field_area_fraction": settings.crater_field_area_fraction,
            "sandy_plain_max_slope_deg": settings.sandy_plain_max_slope_deg,
            "sandy_plain_max_roughness": settings.sandy_plain_max_roughness,
            "sandy_plain_max_area_fraction": settings.sandy_plain_max_area_fraction,
        },
    }

    if area_fraction >= settings.crater_field_area_fraction:
        classification = TerrainClass.CRATER_FIELD
        evidence["rule_fired"] = (
            f"obstacle_area_fraction {area_fraction:.3f} >= "
            f"{settings.crater_field_area_fraction} (few large closed features)"
        )
    elif (
        mean_slope <= settings.sandy_plain_max_slope_deg
        and mean_roughness <= settings.sandy_plain_max_roughness
        and area_fraction < settings.sandy_plain_max_area_fraction
    ):
        classification = TerrainClass.SANDY_PLAIN
        evidence["rule_fired"] = "low slope AND low roughness AND sparse obstacles"
    else:
        classification = TerrainClass.ROCKY_HIGHLAND
        evidence["rule_fired"] = "default (elevated slope, roughness, or obstacle count)"

    return classification, evidence


def analyze_terrain(image_path: str | Path, settings: Settings) -> TerrainAnalysis:
    """Run the full CV pipeline over one terrain image."""
    gray = load_terrain_image(image_path)

    slope_deg, gradient_magnitude = compute_slope(
        gray, settings.meters_per_pixel, settings.elevation_range_m
    )
    obstacles, obstacle_mask, detector_metadata = detect_obstacles(
        gray,
        settings.canny_gradient_percentile,
        settings.canny_low_ratio,
        settings.morph_close_kernel,
        settings.min_obstacle_area_px,
        settings.meters_per_pixel,
    )
    roughness = compute_roughness(gray, settings.roughness_window)
    distance_m = distance_to_nearest_obstacle_m(obstacle_mask, settings.meters_per_pixel)
    classification, evidence = classify_terrain(
        slope_deg, roughness, obstacles, obstacle_mask, settings
    )

    height, width = gray.shape
    stats = {
        "image_size_px": {"width": int(width), "height": int(height)},
        "coverage_m": {
            "width": round(width * settings.meters_per_pixel, 1),
            "height": round(height * settings.meters_per_pixel, 1),
        },
        "slope": {
            "mean_deg": round(float(np.mean(slope_deg)), 3),
            "max_deg": round(float(np.max(slope_deg)), 3),
            "p95_deg": round(float(np.percentile(slope_deg, 95)), 3),
        },
        "roughness": {
            "mean": round(float(np.mean(roughness)), 4),
            "max": round(float(np.max(roughness)), 4),
        },
        "obstacle_count": len(obstacles),
        "obstacle_area_fraction": round(float((obstacle_mask > 0).mean()), 4),
        "obstacle_detector": detector_metadata,
        "classification_evidence": evidence,
        "parameters": {
            "meters_per_pixel": settings.meters_per_pixel,
            "elevation_range_m": settings.elevation_range_m,
            "canny_gradient_percentile": settings.canny_gradient_percentile,
            "canny_low_ratio": settings.canny_low_ratio,
            "min_obstacle_area_px": settings.min_obstacle_area_px,
            "roughness_window": settings.roughness_window,
        },
    }

    return TerrainAnalysis(
        elevation_m=(gray / 255.0) * settings.elevation_range_m,
        slope_deg=slope_deg,
        gradient_magnitude=gradient_magnitude,
        roughness=roughness,
        obstacle_mask=obstacle_mask,
        distance_to_obstacle_m=distance_m,
        obstacles=obstacles,
        classification=classification,
        stats=stats,
    )


def render_slope_map(slope_deg: np.ndarray, output_path: str | Path) -> str:
    """Write a viewable slope map. Colour scale saturates at 45 degrees."""
    normalised = np.clip(slope_deg / 45.0, 0.0, 1.0)
    coloured = cv2.applyColorMap((normalised * 255).astype(np.uint8), cv2.COLORMAP_VIRIDIS)
    cv2.imwrite(str(output_path), coloured)
    return str(output_path)


def slope_between(
    elevation_m: np.ndarray, a: tuple[int, int], b: tuple[int, int], meters_per_pixel: float
) -> float:
    """Signed slope in degrees travelling from cell ``a`` to cell ``b``.

    Positive is uphill. Used by the planner's energy model and by the
    max-traversable-slope constraint.
    """
    ay, ax = a
    by, bx = b
    run_m = math.hypot(bx - ax, by - ay) * meters_per_pixel
    if run_m == 0:
        return 0.0
    rise_m = float(elevation_m[by, bx] - elevation_m[ay, ax])
    return math.degrees(math.atan(rise_m / run_m))
