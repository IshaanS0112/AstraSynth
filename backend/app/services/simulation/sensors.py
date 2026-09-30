"""A deliberately simple rover sensor model."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.services.planning.grid import Cell, PlanningGrid


@dataclass(slots=True)
class RoverSensor:
    """Hazard-camera-like local sensing."""

    range_m: float
    noise_sigma: float = 0.05
    sensor_height_m: float = 1.0
    respect_occlusion: bool = True

    def __post_init__(self) -> None:
        if self.range_m <= 0:
            raise ValueError("range_m must be positive")
        if self.noise_sigma <= 0:
            raise ValueError(
                "noise_sigma must be positive; a perfect sensor makes the "
                "Bayesian update degenerate"
            )

    def cells_in_range(self, grid: PlanningGrid, position: Cell) -> list[Cell]:
        radius_cells = int(math.floor(self.range_m / grid.meters_per_cell))
        cells: list[Cell] = []
        for d_row in range(-radius_cells, radius_cells + 1):
            for d_col in range(-radius_cells, radius_cells + 1):
                cell = (position[0] + d_row, position[1] + d_col)
                if not grid.in_bounds(cell):
                    continue
                if math.hypot(d_row, d_col) * grid.meters_per_cell > self.range_m:
                    continue
                cells.append(cell)
        return cells

    def visible(self, grid: PlanningGrid, position: Cell, target: Cell) -> bool:
        """Whether terrain between sensor and target clears the sight line."""
        if not self.respect_occlusion:
            return True
        return grid.elevation_line_of_sight(
            position, target, height_a=self.sensor_height_m, height_b=0.0
        )

    def observe(
        self,
        truth_hazard: np.ndarray,
        grid: PlanningGrid,
        position: Cell,
        rng: np.random.Generator,
    ) -> dict[Cell, float]:
        """Readings of the true hazard field at every visible in-range cell."""
        observations: dict[Cell, float] = {}
        for cell in self.cells_in_range(grid, position):
            if not self.visible(grid, position, cell):
                continue
            reading = float(truth_hazard[cell]) + float(rng.normal(0.0, self.noise_sigma))
            observations[cell] = min(1.0, max(0.0, reading))
        return observations

    def describe(self) -> dict:
        return {
            "range_m": self.range_m,
            "noise_sigma": self.noise_sigma,
            "sensor_height_m": self.sensor_height_m,
            "occlusion_model": (
                "straight-line height profile over the supercover cells"
                if self.respect_occlusion
                else "disabled"
            ),
            "field_of_view": "360 degrees (simplification: no pointing model)",
        }
