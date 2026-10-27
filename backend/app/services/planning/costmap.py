"""Layered costmap: the master grid the planners read, composed from layers.

After Nav2's ``costmap_2d``. An obstacle layer marks cells the rover may never
enter; an inflation layer spreads a decaying cost outward from those cells to the
rover's clearance radius; layers combine by taking the maximum, so a layer can
only ever make ground more expensive, never cheaper.

Three things the hazard field alone could not express:

* **A hazard narrower than a planning cell.** The cost layer is a mean over each
  cell's source pixels, which is right for exposure and wrong for obstacles: a
  one-pixel lethal ridge averages to nothing. Following grid_map, each layer
  takes its own reduction - obstacles arrive max-pooled, so one lethal pixel
  makes its cell an obstacle.
* **The rover has a body.** A point rover can drive one cell from a crater rim.
  Cells within the footprint's inscribed radius of an obstacle become lethal,
  because the body would overlap it wherever the centre stands.
* **Clearance is worth paying for.** Past the footprint, cost decays as Nav2's
  ``InflationLayer::computeCost``: ``peak * exp(-k * (d - r_inscribed))``. A route
  that hugs a hazard boundary is now dearer than one a few cells away.

Distances are between cell centres, as in Nav2.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Nav2 cost values: LETHAL_OBSTACLE = 254, INSCRIBED_INFLATED_OBSTACLE = 253, and
# the inflation band starts at INSCRIBED - 1 = 252. Normalised against the lethal
# threshold, the band's peak sits just below it, so inflation shapes a route but
# never on its own makes a cell undrivable.
NAV2_BAND_PEAK_FRACTION = 252.0 / 254.0

# Nav2's indoor default is k = 3.0/m over a ~0.7 m band. Planetary cells are
# metres wide, so k is expressed per band width instead: the cost falls by e^-3
# (about 95%) across the clearance band at any scale.
DEFAULT_DECAY_OVER_BAND = 3.0


@dataclass(frozen=True, slots=True)
class FootprintParams:
    """How big the rover is, and how much room it wants."""

    inscribed_radius_m: float = 0.0
    inflation_radius_m: float = 0.0
    cost_scaling_factor: float | None = None  # 1/m; None = scale to the band

    def __post_init__(self) -> None:
        if self.inscribed_radius_m < 0 or self.inflation_radius_m < 0:
            raise ValueError("footprint radii must be non-negative")
        if self.inflation_radius_m and self.inflation_radius_m < self.inscribed_radius_m:
            raise ValueError("inflation_radius_m must be at least inscribed_radius_m")
        if self.cost_scaling_factor is not None and self.cost_scaling_factor <= 0:
            raise ValueError("cost_scaling_factor must be positive")

    @property
    def enabled(self) -> bool:
        return self.inscribed_radius_m > 0 or self.inflation_radius_m > 0

    def decay_per_m(self) -> float:
        if self.cost_scaling_factor is not None:
            return self.cost_scaling_factor
        band = self.inflation_radius_m - self.inscribed_radius_m
        return DEFAULT_DECAY_OVER_BAND / band if band > 0 else 0.0


@dataclass(slots=True)
class CostmapLayers:
    """Every layer, kept so the API and the 3-D view can show each one."""

    obstacle: np.ndarray  # bool: never enterable
    distance_m: np.ndarray  # to the nearest obstacle cell centre; inf if none
    inflation: np.ndarray  # [0, peak]: the decaying clearance cost
    master: np.ndarray  # what the planners read
    lethal_threshold: float

    @property
    def lethal(self) -> np.ndarray:
        return self.master >= self.lethal_threshold


def obstacle_mask(
    hazard: np.ndarray,
    max_hazard: float,
    subcell_obstacles: np.ndarray | None = None,
) -> np.ndarray:
    """Cells the rover may not enter: lethal on average, or lethal anywhere inside."""
    mask = hazard >= max_hazard
    if subcell_obstacles is not None:
        mask = mask | subcell_obstacles
    return mask


def distance_to_obstacles_m(obstacle: np.ndarray, meters_per_cell: float) -> np.ndarray:
    """Exact Euclidean distance from each cell centre to the nearest obstacle."""
    if not obstacle.any():
        return np.full(obstacle.shape, np.inf, dtype=np.float64)

    import cv2

    free = np.where(obstacle, 0, 1).astype(np.uint8)
    cells = cv2.distanceTransform(free, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    return cells.astype(np.float64) * meters_per_cell


def inflation_cost(
    distance_m: np.ndarray,
    params: FootprintParams,
    max_hazard: float,
) -> np.ndarray:
    """Nav2's inflation curve, normalised to this cost model's lethal threshold.

    ``d <= r_inscribed`` is returned as the lethal threshold itself; obstacles and
    inscribed cells are made lethal by the caller, so the value only matters for
    display.
    """
    peak = max_hazard * NAV2_BAND_PEAK_FRACTION
    out = np.zeros(distance_m.shape, dtype=np.float64)

    inscribed = distance_m <= params.inscribed_radius_m
    out[inscribed] = max_hazard

    if params.inflation_radius_m > 0:
        band = (~inscribed) & (distance_m <= params.inflation_radius_m)
        k = params.decay_per_m()
        out[band] = peak * np.exp(-k * (distance_m[band] - params.inscribed_radius_m))
    return out


def compose(
    hazard: np.ndarray,
    max_hazard: float,
    meters_per_cell: float,
    params: FootprintParams,
    subcell_obstacles: np.ndarray | None = None,
) -> CostmapLayers:
    """Build every layer and the master costmap from them."""
    obstacle = obstacle_mask(hazard, max_hazard, subcell_obstacles)
    distance = distance_to_obstacles_m(obstacle, meters_per_cell)
    inflation = inflation_cost(distance, params, max_hazard)

    # Combine by maximum, as Nav2's updateWithMax: no layer can lower a cost.
    master = np.maximum(hazard, np.minimum(inflation, max_hazard * NAV2_BAND_PEAK_FRACTION))
    lethal = obstacle | (distance <= params.inscribed_radius_m)
    master[lethal] = np.maximum(master[lethal], max_hazard)

    return CostmapLayers(
        obstacle=obstacle,
        distance_m=distance,
        inflation=inflation,
        master=master,
        lethal_threshold=max_hazard,
    )


def describe(params: FootprintParams, meters_per_cell: float) -> dict:
    """The costmap's parameters, for the audit trail."""
    k = params.decay_per_m()
    return {
        "source": "Nav2 costmap_2d InflationLayer, normalised",
        "inscribed_radius_m": params.inscribed_radius_m,
        "inflation_radius_m": params.inflation_radius_m,
        "cost_scaling_factor_per_m": round(k, 6),
        "band_peak_fraction_of_lethal": round(NAV2_BAND_PEAK_FRACTION, 6),
        "inflation_cells": (
            math.ceil(params.inflation_radius_m / meters_per_cell) if meters_per_cell else 0
        ),
        "combination": "max",
        "formula": "d <= r_inscribed: lethal; else peak * exp(-k * (d - r_inscribed))",
    }
