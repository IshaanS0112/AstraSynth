#!/usr/bin/env python3
"""Measure A* against Dijkstra on identical terrain.

"A* is faster than Dijkstra" is the kind of claim that is easy to assert and
awkward to defend if nobody has measured it. This runs both over the same grid
with the same cost function and reports node expansions, wall time, and whether
the two agree on the optimal cost - the last of which is the empirical check
that the heuristic really is admissible.

Usage::

    python scripts/benchmark_planner.py
    python scripts/benchmark_planner.py --grid 96 128 192 256 --repeats 5
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import Settings  # noqa: E402
from app.services import hazard_mapper, terrain_analyzer  # noqa: E402
from app.services.path_planner import RoverSpec, plan_path, plan_path_dijkstra  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_terrain import generate  # noqa: E402

ROVER = RoverSpec(battery_capacity_kwh=6.0, max_traversable_slope_deg=25.0, energy_per_meter_kwh=0.0030)


def build_grids(size: int, settings: Settings) -> tuple[np.ndarray, np.ndarray, float]:
    """Real hazard and elevation grids from the CV pipeline, not random noise."""
    import cv2
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        cv2.imwrite(handle.name, generate("crater_field", size, seed=42))
        analysis = terrain_analyzer.analyze_terrain(handle.name, settings)

    hazard = hazard_mapper.build_hazard_map(analysis, settings)
    hazard_grid, scale = hazard_mapper.downsample_for_planning(hazard.scores, size)
    elevation_grid, _ = hazard_mapper.downsample_for_planning(analysis.elevation_m, size)
    return hazard_grid, elevation_grid, settings.meters_per_pixel * scale


def timed(fn, repeats: int) -> tuple[float, object]:
    durations, result = [], None
    for _ in range(repeats):
        started = time.perf_counter()
        result = fn()
        durations.append(time.perf_counter() - started)
    return statistics.median(durations), result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=int, nargs="+", default=[64, 128, 192, 256])
    parser.add_argument("--repeats", type=int, default=3)
    args = parser.parse_args()

    settings = Settings(_env_file=None)

    print(f"Cost function: {'distance_m * (1 + hazard) * (1 + k*|rise/run|)'}")
    print(f"Heuristic:     euclidean distance in metres (A*), zero (Dijkstra)")
    print(f"Repeats:       {args.repeats} (median reported)\n")
    header = f"{'grid':>9} {'A* nodes':>10} {'Dij nodes':>10} {'saved':>7} {'A* ms':>8} {'Dij ms':>8} {'costs agree':>12}"
    print(header)
    print("-" * len(header))

    for size in args.grid:
        hazard_grid, elevation_grid, meters_per_cell = build_grids(size, settings)
        rows, cols = hazard_grid.shape
        start = {"x": 2, "y": 2}
        goal = {"x": cols - 3, "y": rows - 3}

        common = dict(
            hazard_grid=hazard_grid,
            elevation_grid=elevation_grid,
            start=start,
            goal=goal,
            rover=ROVER,
            meters_per_cell=meters_per_cell,
            slope_coefficient=settings.energy_slope_coefficient,
            max_hazard=settings.lethal_hazard_threshold,
        )

        astar_ms, astar = timed(lambda: plan_path(**common), args.repeats)
        dijkstra_ms, dijkstra = timed(lambda: plan_path_dijkstra(**common), args.repeats)

        saved = 1 - astar.metadata["nodes_expanded"] / dijkstra.metadata["nodes_expanded"]
        agree = abs(astar.total_cost - dijkstra.total_cost) < 1e-6

        print(
            f"{rows:>4}x{cols:<4} {astar.metadata['nodes_expanded']:>10} "
            f"{dijkstra.metadata['nodes_expanded']:>10} {saved:>6.1%} "
            f"{astar_ms * 1000:>8.1f} {dijkstra_ms * 1000:>8.1f} {str(agree):>12}"
        )

    print(
        "\n'costs agree' must be True at every size: if A* ever returned a cheaper "
        "cost than Dijkstra the heuristic would be inadmissible and the path would "
        "not be optimal."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
