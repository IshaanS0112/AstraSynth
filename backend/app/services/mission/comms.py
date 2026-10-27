"""Where the rover can talk from, and when."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from app.services.planning.grid import Cell, PlanningGrid


@dataclass(slots=True)
class RelayStation:
    """A fixed ground station with an antenna at a known height."""

    name: str
    cell: Cell
    antenna_height_m: float = 3.0
    max_range_m: float = 5000.0

    def visibility_mask(self, grid: PlanningGrid, rover_antenna_m: float = 1.5) -> np.ndarray:
        """Boolean grid: cells with line of sight to this station, in range."""
        mask = np.zeros(grid.shape, dtype=bool)
        for row in range(grid.rows):
            for col in range(grid.cols):
                cell = (row, col)
                if grid.distance_m(cell, self.cell) > self.max_range_m:
                    continue
                mask[row, col] = grid.elevation_line_of_sight(
                    cell, self.cell, height_a=rover_antenna_m, height_b=self.antenna_height_m
                )
        return mask

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "cell": list(self.cell),
            "antenna_height_m": self.antenna_height_m,
            "max_range_m": self.max_range_m,
        }


@dataclass(slots=True)
class OrbiterPass:
    """A repeating overhead window."""

    name: str
    period_seconds: float = 6 * 3600.0
    duration_seconds: float = 600.0
    first_pass_at_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.period_seconds <= 0 or self.duration_seconds <= 0:
            raise ValueError("period and duration must be positive")
        if self.duration_seconds > self.period_seconds:
            raise ValueError("a pass cannot last longer than the interval between passes")

    def available_at(self, t_seconds: float) -> bool:
        if t_seconds < self.first_pass_at_seconds:
            return False
        phase = (t_seconds - self.first_pass_at_seconds) % self.period_seconds
        return phase < self.duration_seconds

    def next_window(self, after_seconds: float) -> tuple[float, float]:
        """``(start, end)`` of the first pass beginning at or after ``after_seconds``."""
        if after_seconds <= self.first_pass_at_seconds:
            start = self.first_pass_at_seconds
        else:
            elapsed = after_seconds - self.first_pass_at_seconds
            start = (
                self.first_pass_at_seconds
                + math.ceil(elapsed / self.period_seconds) * self.period_seconds
            )
        return start, start + self.duration_seconds

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "period_seconds": self.period_seconds,
            "duration_seconds": self.duration_seconds,
            "first_pass_at_seconds": self.first_pass_at_seconds,
            "duty_cycle": round(self.duration_seconds / self.period_seconds, 4),
        }


@dataclass(slots=True)
class CommunicationPlan:
    """Ground coverage plus orbiter schedule, evaluated together."""

    stations: list[RelayStation] = field(default_factory=list)
    orbiters: list[OrbiterPass] = field(default_factory=list)
    _coverage: np.ndarray | None = field(default=None, repr=False)

    def coverage_mask(self, grid: PlanningGrid, rover_antenna_m: float = 1.5) -> np.ndarray:
        """Union of every station's visibility mask. Cached per plan."""
        if self._coverage is None:
            mask = np.zeros(grid.shape, dtype=bool)
            for station in self.stations:
                mask |= station.visibility_mask(grid, rover_antenna_m)
            self._coverage = mask
        return self._coverage

    def linked(self, grid: PlanningGrid, cell: Cell, t_seconds: float) -> bool:
        if bool(self.coverage_mask(grid)[cell]):
            return True
        return any(orbiter.available_at(t_seconds) for orbiter in self.orbiters)

    def evaluate_route(
        self, grid: PlanningGrid, cells: list[Cell], start_time_s: float = 0.0
    ) -> dict:
        """Link statistics for a route, driven at the rover's own pace."""
        if not cells:
            raise ValueError("cannot evaluate an empty route")

        coverage = self.coverage_mask(grid)
        clock = start_time_s
        linked_seconds = 0.0
        total_seconds = 0.0
        longest_blackout = 0.0
        current_blackout = 0.0
        linked_cells = 0

        for index, cell in enumerate(cells):
            if index > 0:
                step_seconds = grid.traverse_seconds(cells[index - 1], cell)
                clock += step_seconds
                total_seconds += step_seconds
            else:
                step_seconds = 0.0

            has_link = bool(coverage[cell]) or any(
                orbiter.available_at(clock) for orbiter in self.orbiters
            )
            if has_link:
                linked_cells += 1
                linked_seconds += step_seconds
                current_blackout = 0.0
            else:
                current_blackout += step_seconds
                longest_blackout = max(longest_blackout, current_blackout)

        return {
            "cells_total": len(cells),
            "cells_with_link": linked_cells,
            "linked_cell_fraction": round(linked_cells / len(cells), 4),
            "linked_time_fraction": (
                round(linked_seconds / total_seconds, 4) if total_seconds else 1.0
            ),
            "blackout_fraction": (
                round(1.0 - linked_seconds / total_seconds, 4) if total_seconds else 0.0
            ),
            "longest_blackout_seconds": round(longest_blackout, 1),
            "elapsed_seconds": round(total_seconds, 1),
            "ground_stations": [s.name for s in self.stations],
            "orbiters": [o.name for o in self.orbiters],
            "model": (
                "ground link = geometric line of sight within range; orbiter link = "
                "periodic time window, no per-pass geometry"
            ),
        }

    def as_dict(self) -> dict:
        return {
            "stations": [s.as_dict() for s in self.stations],
            "orbiters": [o.as_dict() for o in self.orbiters],
        }
