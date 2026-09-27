"""The traversable graph, materialised once, vectorised.

Why this exists
---------------
Profiling A* on a 192x192 grid put 33% of the runtime inside
``PlanningGrid.evaluate_edge`` across 91,000 calls. None of that is search: it
is the same eight numpy scalar reads per cell, redone every time a cell is
touched, and redone again by the next planner over the same terrain. numpy
scalar indexing costs a few hundred nanoseconds apiece, which is invisible once
and ruinous a million times.

So the graph is built **once**, with numpy doing all eight neighbour directions
as whole-array slice arithmetic, and handed to the planners as flat Python
lists. After that an edge lookup is a list index - a few nanoseconds - and the
search loop touches no numpy at all.

Two properties this has to preserve, and does:

* **Identical results.** The same edges, the same costs, and the same
  *attribution* of why a blocked edge was blocked. ``evaluate_edge`` checks
  lethal hazard before slope, so a cell failing both is reported as a hazard
  rejection; the masks below are composed in that order for the same reason.
  ``test_compiled_graph`` asserts agreement edge-by-edge over random terrain.
* **Cheap invalidation.** D* Lite changes a handful of cells per sensor
  reading. Rebuilding 300,000 edges for eight changed ones would make the
  incremental planner the slowest thing in the system, so
  :meth:`recompute_cells` patches only the edges a changed cell participates
  in - the eight into it, and the eight out of it.

Flat indexing
-------------
Cells are integers, ``index = row * cols + col``, not ``(row, col)`` tuples.
That is not cosmetic either: a tuple key costs an allocation and a hash on every
dictionary touch, and the search does millions of them.
"""

from __future__ import annotations

import math

import numpy as np

from app.services.planning.grid import NEIGHBOUR_OFFSETS, PlanningGrid

# Above this many cells the compiled graph costs more memory than it saves
# time - eight Python floats per cell is roughly 250 bytes, so a 1000x1000 grid
# would be 250 MB. The planning grid is capped well below this by
# ``planning_grid_max_dim``; the guard is here so that a caller who raises that
# cap gets a clear refusal instead of an out-of-memory kill.
MAX_COMPILED_CELLS = 500_000


class GraphTooLargeError(RuntimeError):
    """The grid is too large to compile. Lower ``planning_grid_max_dim``."""


class CompiledGraph:
    """Eight outgoing edges per cell, as flat lists of destinations and costs.

    ``neighbour[k][i]`` is the flat index of cell ``i``'s neighbour in direction
    ``k``, or ``-1`` if that direction leaves the grid. ``cost[k][i]`` is the
    edge cost, or ``inf`` if the edge is not traversable. The two are kept
    separate because "off the grid" and "blocked" are different facts: the first
    never changes, the second does every time the rover learns something.
    """

    __slots__ = (
        "blocked_hazard",
        "blocked_slope",
        "cols",
        "cost",
        "meters_per_cell",
        "neighbour",
        "rows",
        "size",
    )

    def __init__(self, grid: PlanningGrid) -> None:
        rows, cols = grid.shape
        size = rows * cols
        if size > MAX_COMPILED_CELLS:
            raise GraphTooLargeError(
                f"{rows}x{cols} = {size} cells exceeds the {MAX_COMPILED_CELLS}-cell "
                "compile limit; lower planning_grid_max_dim"
            )

        self.rows = rows
        self.cols = cols
        self.size = size
        self.meters_per_cell = grid.meters_per_cell

        neighbour_arrays: list[np.ndarray] = []
        cost_arrays: list[np.ndarray] = []
        hazard_blocked: list[np.ndarray] = []
        slope_blocked: list[np.ndarray] = []

        flat = np.arange(size, dtype=np.int64).reshape(rows, cols)
        max_slope_tan = math.tan(math.radians(grid.rover.max_traversable_slope_deg))

        for d_row, d_col in NEIGHBOUR_OFFSETS:
            destination = np.full((rows, cols), -1, dtype=np.int64)
            cost = np.full((rows, cols), np.inf, dtype=np.float64)
            by_hazard = np.zeros((rows, cols), dtype=bool)
            by_slope = np.zeros((rows, cols), dtype=bool)

            # The sub-rectangle of source cells whose neighbour in this
            # direction is still inside the grid.
            r0, r1 = max(0, -d_row), rows - max(0, d_row)
            c0, c1 = max(0, -d_col), cols - max(0, d_col)
            if r0 >= r1 or c0 >= c1:
                neighbour_arrays.append(destination)
                cost_arrays.append(cost)
                hazard_blocked.append(by_hazard)
                slope_blocked.append(by_slope)
                continue

            src = (slice(r0, r1), slice(c0, c1))
            dst = (slice(r0 + d_row, r1 + d_row), slice(c0 + d_col, c1 + d_col))

            destination[src] = flat[dst]

            destination_hazard = grid.hazard[dst]
            step_m = math.hypot(d_row, d_col) * grid.meters_per_cell
            gradient = (grid.elevation[dst] - grid.elevation[src]) / step_m

            lethal = destination_hazard >= grid.max_hazard
            if d_row != 0 and d_col != 0:
                # No corner cutting: a diagonal clips the two orthogonally
                # adjacent cells, so a lethal one on either side blocks it.
                corner_a = grid.hazard[(slice(r0, r1), slice(c0 + d_col, c1 + d_col))]
                corner_b = grid.hazard[(slice(r0 + d_row, r1 + d_row), slice(c0, c1))]
                lethal = lethal | (corner_a >= grid.max_hazard) | (corner_b >= grid.max_hazard)

            # Order matters: evaluate_edge tests hazard before slope, so a cell
            # failing both is attributed to hazard. The diagnostics downstream
            # pick a failure mode from whichever counter dominates, so getting
            # this backwards would change what a failed mission is blamed on.
            steep = (~lethal) & (np.abs(gradient) > max_slope_tan)

            by_hazard[src] = lethal
            by_slope[src] = steep

            traversable = ~(lethal | steep)
            edge_cost = (
                step_m
                * (1.0 + grid.hazard_weight * destination_hazard)
                * (1.0 + grid.energy_weight * grid.slope_coefficient * np.abs(gradient))
            )
            cost_block = np.full(edge_cost.shape, np.inf, dtype=np.float64)
            np.copyto(cost_block, edge_cost, where=traversable)
            cost[src] = cost_block

            neighbour_arrays.append(destination)
            cost_arrays.append(cost)
            hazard_blocked.append(by_hazard)
            slope_blocked.append(by_slope)

        # `.tolist()` once, here, is the whole point: the search loop then does
        # native list indexing instead of numpy scalar reads.
        self.neighbour = [a.reshape(-1).tolist() for a in neighbour_arrays]
        self.cost = [a.reshape(-1).tolist() for a in cost_arrays]
        self.blocked_hazard = [a.reshape(-1).tolist() for a in hazard_blocked]
        self.blocked_slope = [a.reshape(-1).tolist() for a in slope_blocked]

    # --- frame conversion -----------------------------------------------------

    def index(self, cell: tuple[int, int]) -> int:
        return cell[0] * self.cols + cell[1]

    def cell(self, index: int) -> tuple[int, int]:
        return divmod(index, self.cols)

    def heuristics_to(self, goal: int) -> list[float]:
        """Straight-line distance from every cell to ``goal``, in metres.

        Computed for the whole grid in one numpy call because the goal is fixed
        for the duration of a search: 36,000 cells at once beats 36,000
        ``math.hypot`` calls interleaved with the heap.
        """
        goal_row, goal_col = divmod(goal, self.cols)
        rows = np.arange(self.rows, dtype=np.float64)[:, None] - goal_row
        cols = np.arange(self.cols, dtype=np.float64)[None, :] - goal_col
        return (np.hypot(rows, cols) * self.meters_per_cell).reshape(-1).tolist()

    # --- incremental maintenance ---------------------------------------------

    def recompute_cells(self, grid: PlanningGrid, cells: list[tuple[int, int]]) -> None:
        """Patch the edges a changed cell participates in.

        A cell's hazard appears in the cost of the eight edges *into* it, and its
        elevation in the eight edges out of it as well, so both directions are
        rebuilt. Diagonal corner-cutting means a change can also block a diagonal
        that merely clips the cell, so the eight neighbours' own edges are
        refreshed too.
        """
        touched: set[tuple[int, int]] = set()
        for cell in cells:
            touched.add(cell)
            for d_row, d_col in NEIGHBOUR_OFFSETS:
                neighbour = (cell[0] + d_row, cell[1] + d_col)
                if 0 <= neighbour[0] < self.rows and 0 <= neighbour[1] < self.cols:
                    touched.add(neighbour)

        for cell in touched:
            source = self.index(cell)
            for k, (d_row, d_col) in enumerate(NEIGHBOUR_OFFSETS):
                target = (cell[0] + d_row, cell[1] + d_col)
                if not (0 <= target[0] < self.rows and 0 <= target[1] < self.cols):
                    continue
                cost, reason = grid.evaluate_edge(cell, target)
                from app.services.planning.grid import EdgeBlock

                self.cost[k][source] = math.inf if cost is None else cost
                self.blocked_hazard[k][source] = reason is EdgeBlock.LETHAL_HAZARD
                self.blocked_slope[k][source] = reason is EdgeBlock.SLOPE
