"""The one cost model every planner in this package shares.

A* / Theta* / D* Lite / CBS all call ``PlanningGrid.evaluate_edge``, so a
benchmark that reports different node counts for the same optimal cost is
reporting a real difference in search rather than a different graph.

For a move from cell ``a`` to an 8-connected neighbour ``b``::

    cost(a, b) = distance_m(a, b) * (1 + w_hazard * hazard(b))
                                  * (1 + w_energy * k * |rise / run|)

Two constraint layers: a cost layer ``(1 + hazard)``, bounded by 2, which shapes
the route inside drivable ground, and a lethal layer (``max_hazard`` and the
rover's slope limit) which removes edges from the graph. The cost layer is a
preference, never a prohibition - doubling one cell cannot outweigh a
fifteen-cell detour - which is why the lethal layer is not optional.

Since ``hazard >= 0`` and ``energy_factor >= 1``, every edge satisfies
``cost >= distance_m``, so straight-line distance is an admissible and consistent
heuristic for all four planners. ``heuristic_is_admissible`` asserts it in code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# 8-connected. Diagonals cost sqrt(2) cells, handled naturally by measuring
# distance in metres.
NEIGHBOUR_OFFSETS: tuple[tuple[int, int], ...] = (
    (-1, 0),
    (1, 0),
    (0, -1),
    (0, 1),
    (-1, -1),
    (-1, 1),
    (1, -1),
    (1, 1),
)

Cell = tuple[int, int]  # (row, col) in planning-grid coordinates


class PathNotFoundError(RuntimeError):
    """No traversable route exists between start and goal for this rover."""


class EdgeBlock(str, Enum):
    """Why an edge is absent from the graph."""

    SLOPE = "slope_limit"
    LETHAL_HAZARD = "lethal_hazard"
    OUT_OF_BOUNDS = "out_of_bounds"


@dataclass(slots=True)
class RoverSpec:
    """Planner-facing view of a rover configuration."""

    battery_capacity_kwh: float
    max_traversable_slope_deg: float
    energy_per_meter_kwh: float
    mass_kg: float = 250.0
    payload_kg: float = 0.0
    # Speed retained on maximally rough ground; 1.0 = roughness does not slow it.
    roughness_speed_penalty: float = 0.0
    nominal_speed_ms: float = 0.05

    def payload_factor(self) -> float:
        """Energy multiplier from hauling ``payload_kg`` on a ``mass_kg`` chassis."""
        if self.mass_kg <= 0:
            raise ValueError("mass_kg must be positive")
        return (self.mass_kg + self.payload_kg) / self.mass_kg


def energy_factor(rise_over_run: float, k: float) -> float:
    """Multiplier on per-metre energy draw for a given incline."""
    return 1.0 + k * abs(rise_over_run)


def heuristic_is_admissible(hazard_weight: float, energy_weight: float) -> bool:
    """Whether straight-line distance still lower-bounds cost at these weights."""
    return hazard_weight >= 0.0 and energy_weight >= 0.0


@dataclass(slots=True)
class Waypoint:
    segment_id: int
    x: int  # original image pixel coordinates
    y: int
    hazard_score: float
    slope_deg: float
    step_distance_m: float
    energy_cost_kwh: float  # energy for the step into this waypoint
    cumulative_energy_kwh: float


@dataclass(slots=True)
class PlannedPath:
    waypoints: list[Waypoint]
    total_distance_m: float
    total_energy_cost_kwh: float
    total_cost: float
    metadata: dict = field(default_factory=dict)

    def hazard_series(self) -> list[float]:
        return [w.hazard_score for w in self.waypoints]


class PlanningGrid:
    """A rover, a terrain, and the graph the two of them imply."""

    __slots__ = (
        "_compiled",
        "_max_slope_tan",
        "_payload_factor",
        "cols",
        "elevation",
        "energy_weight",
        "hazard",
        "hazard_weight",
        "max_hazard",
        "meters_per_cell",
        "rover",
        "rows",
        "slope_coefficient",
    )

    def __init__(
        self,
        hazard: np.ndarray,
        elevation: np.ndarray,
        meters_per_cell: float,
        rover: RoverSpec,
        slope_coefficient: float = 0.5,
        max_hazard: float = 1.0,
        hazard_weight: float = 1.0,
        energy_weight: float = 1.0,
    ) -> None:
        if hazard.shape != elevation.shape:
            raise ValueError("hazard and elevation grids must have the same shape")
        if hazard.ndim != 2:
            raise ValueError("planning grids must be 2-D")
        if meters_per_cell <= 0:
            raise ValueError("meters_per_cell must be positive")
        if not heuristic_is_admissible(hazard_weight, energy_weight):
            raise ValueError(
                "hazard_weight and energy_weight must be non-negative: a negative "
                "weight breaks cost >= distance_m and with it the admissibility of "
                "every heuristic in this package"
            )

        self.hazard = np.asarray(hazard, dtype=np.float64)
        self.elevation = np.asarray(elevation, dtype=np.float64)
        self.meters_per_cell = float(meters_per_cell)
        self.rover = rover
        self.slope_coefficient = float(slope_coefficient)
        self.max_hazard = float(max_hazard)
        self.hazard_weight = float(hazard_weight)
        self.energy_weight = float(energy_weight)
        self.rows, self.cols = self.hazard.shape
        self._max_slope_tan = math.tan(math.radians(rover.max_traversable_slope_deg))
        self._payload_factor = rover.payload_factor()
        self._compiled = None

    def compiled(self):
        """The graph as flat lists, built once and cached on first search."""
        if self._compiled is None:
            from app.services.planning.compiled import CompiledGraph

            self._compiled = CompiledGraph(self)
        return self._compiled

    # --- geometry -----------------------------------------------------------

    @property
    def shape(self) -> tuple[int, int]:
        return self.rows, self.cols

    def in_bounds(self, cell: Cell) -> bool:
        return 0 <= cell[0] < self.rows and 0 <= cell[1] < self.cols

    def clamp(self, cell: Cell) -> Cell:
        row = max(0, min(self.rows - 1, cell[0]))
        col = max(0, min(self.cols - 1, cell[1]))
        return row, col

    def to_cell(self, point: dict, scale: float) -> Cell:
        """Image pixel ``{"x": ..., "y": ...}`` -> clamped grid ``(row, col)``."""
        return self.clamp((int(round(point["y"] / scale)), int(round(point["x"] / scale))))

    def distance_m(self, a: Cell, b: Cell) -> float:
        return math.hypot(b[0] - a[0], b[1] - a[1]) * self.meters_per_cell

    def heuristic(self, a: Cell, b: Cell) -> float:
        """Straight-line distance in metres. Admissible and consistent."""
        return self.distance_m(a, b)

    def neighbours(self, cell: Cell):
        row, col = cell
        for d_row, d_col in NEIGHBOUR_OFFSETS:
            n_row, n_col = row + d_row, col + d_col
            if 0 <= n_row < self.rows and 0 <= n_col < self.cols:
                yield (n_row, n_col)

    # --- the graph ----------------------------------------------------------

    def is_lethal(self, cell: Cell) -> bool:
        return float(self.hazard[cell]) >= self.max_hazard

    def rise_over_run(self, a: Cell, b: Cell) -> float:
        run_m = self.distance_m(a, b)
        if run_m == 0.0:
            return 0.0
        return float(self.elevation[b] - self.elevation[a]) / run_m

    def evaluate_edge(self, a: Cell, b: Cell) -> tuple[float | None, EdgeBlock | None]:
        """``(cost, None)`` if the move is legal, ``(None, reason)`` if it is not."""
        if not self.in_bounds(b):
            return None, EdgeBlock.OUT_OF_BOUNDS

        # Lethal-hazard layer: too dangerous to enter at any cost.
        if float(self.hazard[b]) >= self.max_hazard:
            return None, EdgeBlock.LETHAL_HAZARD

        # No corner cutting: a diagonal clips both orthogonal neighbours, so a
        # lethal cell on either side blocks it.
        if (
            a[0] != b[0]
            and a[1] != b[1]
            and (
                float(self.hazard[a[0], b[1]]) >= self.max_hazard
                or float(self.hazard[b[0], a[1]]) >= self.max_hazard
            )
        ):
            return None, EdgeBlock.LETHAL_HAZARD

        step_m = self.distance_m(a, b)
        gradient = (float(self.elevation[b] - self.elevation[a]) / step_m) if step_m else 0.0

        # Hard constraint: makes a mission INFEASIBLE, not merely expensive.
        if abs(gradient) > self._max_slope_tan:
            return None, EdgeBlock.SLOPE

        cost = (
            step_m
            * (1.0 + self.hazard_weight * float(self.hazard[b]))
            * (1.0 + self.energy_weight * self.slope_coefficient * abs(gradient))
        )
        return cost, None

    def edge_cost(self, a: Cell, b: Cell) -> float | None:
        return self.evaluate_edge(a, b)[0]

    def step_energy_kwh(self, a: Cell, b: Cell) -> float:
        """Energy for one move, in kWh. Independent of the search cost function."""
        step_m = self.distance_m(a, b)
        if step_m == 0.0:
            return 0.0
        gradient = float(self.elevation[b] - self.elevation[a]) / step_m
        return (
            self.rover.energy_per_meter_kwh
            * self._payload_factor
            * step_m
            * energy_factor(gradient, self.slope_coefficient)
        )

    def traverse_seconds(self, a: Cell, b: Cell) -> float:
        """Wall-clock duration of one move at the rover's roughness-derated speed."""
        if self.rover.nominal_speed_ms <= 0:
            raise ValueError("nominal_speed_ms must be positive")
        derate = 1.0 - self.rover.roughness_speed_penalty * float(self.hazard[b])
        derate = max(derate, 0.05)  # never divide by zero, never stop completely
        return self.distance_m(a, b) / (self.rover.nominal_speed_ms * derate)

    # --- any-angle support --------------------------------------------------

    def supercover_cells(self, a: Cell, b: Cell) -> list[Cell]:
        """Every cell a straight segment from ``a`` to ``b`` passes through."""
        (row0, col0), (row1, col1) = a, b
        d_row, d_col = abs(row1 - row0), abs(col1 - col0)
        step_row = 1 if row1 > row0 else -1
        step_col = 1 if col1 > col0 else -1

        cells: list[Cell] = [(row0, col0)]
        row, col = row0, col0
        error = d_col - d_row
        # 2x the error so the exact-diagonal case (error == 0) is detectable.
        error *= 2
        d_row2, d_col2 = d_row * 2, d_col * 2

        while (row, col) != (row1, col1):
            if error > 0:
                col += step_col
                error -= d_row2
            elif error < 0:
                row += step_row
                error += d_col2
            else:
                # Exactly through the corner: both neighbours are clipped.
                if (row + step_row, col) != (row1, col1):
                    cells.append((row + step_row, col))
                if (row, col + step_col) != (row1, col1):
                    cells.append((row, col + step_col))
                row += step_row
                col += step_col
                error += d_col2 - d_row2
            cells.append((row, col))

        return cells

    def segment(
        self, a: Cell, b: Cell, samples_per_cell: float = 2.0
    ) -> tuple[float | None, EdgeBlock | None, float]:
        """Cost of driving the straight line ``a -> b``, ignoring the grid graph."""
        if not self.in_bounds(a) or not self.in_bounds(b):
            return None, EdgeBlock.OUT_OF_BOUNDS, 0.0

        # path[1:] skips the origin, matching `evaluate_edge`: lethal ground
        # cannot be *entered*, but a rover standing on it must be able to leave.
        path = self.supercover_cells(a, b)
        for cell in path[1:]:
            if float(self.hazard[cell]) >= self.max_hazard:
                return None, EdgeBlock.LETHAL_HAZARD, 0.0

        length_cells = math.hypot(b[0] - a[0], b[1] - a[1])
        if length_cells == 0.0:
            return 0.0, None, float(self.hazard[a])

        steps = max(2, int(math.ceil(length_cells * samples_per_cell)) + 1)
        rows = np.linspace(a[0], b[0], steps)
        cols = np.linspace(a[1], b[1], steps)
        sample_cells = (np.rint(rows).astype(int), np.rint(cols).astype(int))
        hazards = self.hazard[sample_cells]
        elevations = self.elevation[sample_cells]

        ds_m = (length_cells * self.meters_per_cell) / (steps - 1)

        # Gradients go between consecutive DISTINCT cells over their real
        # separation. Elevation is piecewise constant per cell, so differencing at
        # sub-cell spacing inflates every gradient by the sampling rate. And the
        # supercover list is a set, not a traversal order, so it cannot be walked
        # as a path. See docs/architecture.md bugs 17-18.
        walked = [tuple(int(v) for v in pair) for pair in zip(*sample_cells, strict=False)]
        previous = walked[0]
        for cell in walked[1:]:
            if cell == previous:
                continue
            run_m = self.distance_m(previous, cell)
            rise_m = float(self.elevation[cell] - self.elevation[previous])
            if abs(rise_m / run_m) > self._max_slope_tan:
                return None, EdgeBlock.SLOPE, 0.0
            previous = cell

        gradients = np.abs(np.diff(elevations)) / max(ds_m, self.meters_per_cell)

        # Midpoint rule: each sub-interval is charged the hazard of its far end.
        cost = float(
            np.sum(
                ds_m
                * (1.0 + self.hazard_weight * hazards[1:])
                * (1.0 + self.energy_weight * self.slope_coefficient * gradients)
            )
        )
        return cost, None, float(hazards.mean())

    def segment_energy_kwh(self, a: Cell, b: Cell, samples_per_cell: float = 2.0) -> float:
        """Energy for a straight segment, integrated the same way as ``segment``."""
        length_cells = math.hypot(b[0] - a[0], b[1] - a[1])
        if length_cells == 0.0:
            return 0.0
        steps = max(2, int(math.ceil(length_cells * samples_per_cell)) + 1)
        rows = np.linspace(a[0], b[0], steps)
        cols = np.linspace(a[1], b[1], steps)
        elevations = self.elevation[(np.rint(rows).astype(int), np.rint(cols).astype(int))]
        ds_m = (length_cells * self.meters_per_cell) / (steps - 1)
        # Same one-cell gradient baseline as `segment`; this figure is compared
        # against a battery capacity.
        gradients = np.abs(np.diff(elevations)) / max(ds_m, self.meters_per_cell)
        return float(
            np.sum(
                self.rover.energy_per_meter_kwh
                * self._payload_factor
                * ds_m
                * (1.0 + self.slope_coefficient * gradients)
            )
        )

    def line_of_sight(self, a: Cell, b: Cell) -> bool:
        return self.segment(a, b)[1] is None

    def elevation_line_of_sight(
        self, a: Cell, b: Cell, height_a: float = 0.0, height_b: float = 0.0
    ) -> bool:
        """Whether terrain between ``a`` and ``b`` clears the straight sight line."""
        if a == b:
            return True
        path = self.supercover_cells(a, b)
        if len(path) <= 2:
            return True
        eye = float(self.elevation[a]) + height_a
        target = float(self.elevation[b]) + height_b
        total = float(len(path) - 1)
        for index, cell in enumerate(path[1:-1], start=1):
            line_height = eye + (target - eye) * (index / total)
            if float(self.elevation[cell]) > line_height:
                return False
        return True

    # --- mutation (for belief-driven replanning) ----------------------------

    def apply_hazard_update(self, updates: dict[Cell, float]) -> list[Cell]:
        """Write new hazard values in place; return the cells that actually moved."""
        changed: list[Cell] = []
        for cell, value in updates.items():
            if not self.in_bounds(cell):
                continue
            if float(self.hazard[cell]) != float(value):
                self.hazard[cell] = float(value)
                changed.append(cell)
        if changed and self._compiled is not None:
            # Patch, never rebuild: a sensor reading touches a handful of cells.
            self._compiled.recompute_cells(self, changed)
        return changed

    def with_hazard(self, hazard: np.ndarray) -> PlanningGrid:
        """A copy of this grid over a different hazard field. Terrain is unchanged."""
        return PlanningGrid(
            hazard=hazard,
            elevation=self.elevation,
            meters_per_cell=self.meters_per_cell,
            rover=self.rover,
            slope_coefficient=self.slope_coefficient,
            max_hazard=self.max_hazard,
            hazard_weight=self.hazard_weight,
            energy_weight=self.energy_weight,
        )

    def cost_model_description(self) -> dict:
        """The formula and every constant in it, for the audit trail."""
        return {
            "cost_function": (
                "distance_m * (1 + w_hazard * hazard) * (1 + w_energy * k * |rise/run|)"
            ),
            "energy_model": (
                "energy_kwh = energy_per_meter_kwh * payload_factor * distance_m "
                "* (1 + k * |rise/run|)"
            ),
            "hazard_weight": self.hazard_weight,
            "energy_weight": self.energy_weight,
            "energy_slope_coefficient": self.slope_coefficient,
            "lethal_hazard_threshold": self.max_hazard,
            "max_traversable_slope_deg": self.rover.max_traversable_slope_deg,
            "payload_factor": round(self._payload_factor, 6),
            "meters_per_cell": round(self.meters_per_cell, 4),
            "connectivity": 8,
            "hard_constraints": (
                "edge removed if |slope| > max_traversable_slope_deg "
                "or hazard >= lethal_hazard_threshold"
            ),
            "heuristic": "euclidean_distance_m",
            "heuristic_admissible": True,
            "admissibility_argument": (
                "hazard >= 0 and the energy factor >= 1 at non-negative weights, so "
                "cost >= distance_m on every edge; straight-line distance therefore "
                "never overestimates remaining cost"
            ),
        }
