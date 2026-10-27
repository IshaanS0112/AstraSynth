#!/usr/bin/env python3
"""Measure the V3 planners against each other on identical terrain.

Every planner in ``app.services.planning`` searches the same ``PlanningGrid``,
so the differences reported here are differences in search strategy and nothing
else. Four comparisons, each answering the question its planner exists for:

1. **A* vs Theta*** - route shape. Theta* is not optimal and is not compared on
   cost; it is compared on Euclidean length and heading changes, which is what
   any-angle planning is actually for.
2. **D* Lite vs from-scratch A*** - repair cost under a sensor that reveals the
   map a little at a time. This is the case D* Lite is designed for. On a single
   large map change it has no advantage, which the output says.
3. **CBS** - how the constraint tree grows with fleet size.
4. **Monte Carlo** - trials per second, so a study can be sized before it is run.

Usage::

    python scripts/benchmark_v3.py
    python scripts/benchmark_v3.py --grid 64 96 --trials 50
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.services.mission.comms import (  # noqa: E402
    CommunicationPlan,
    OrbiterPass,
    RelayStation,
)
from app.services.planning import astar, cbs, pareto, theta_star  # noqa: E402
from app.services.planning.dstar_lite import DStarLite  # noqa: E402
from app.services.planning.grid import PlanningGrid, RoverSpec  # noqa: E402
from app.services.planning.trace import heading_changes  # noqa: E402
from app.services.simulation import monte_carlo  # noqa: E402
from app.services.simulation.sensors import RoverSensor  # noqa: E402

SURVEY = RoverSpec(6.0, 25.0, 0.0030)
SCOUT = RoverSpec(2.0, 20.0, 0.0018)
HEAVY = RoverSpec(9.0, 30.0, 0.0062)


def terrain(size: int, seed: int = 42) -> tuple[np.ndarray, np.ndarray]:
    """Continuous random hazard - the cost-field case."""
    rng = np.random.default_rng(seed)
    hazard = rng.random((size, size)) * 0.55
    elevation = rng.random((size, size)) * 0.6
    return hazard, elevation


def obstacle_field(size: int, seed: int = 42, rocks: int | None = None):
    """Mostly-open ground with discrete impassable rocks."""
    rng = np.random.default_rng(seed)
    hazard = rng.random((size, size)) * 0.05
    elevation = np.zeros((size, size))
    for _ in range(rocks if rocks is not None else max(8, size // 4)):
        row, col = int(rng.integers(3, size - 4)), int(rng.integers(3, size - 4))
        radius = int(rng.integers(1, 4))
        hazard[
            max(0, row - radius) : row + radius + 1, max(0, col - radius) : col + radius + 1
        ] = 0.99
    hazard[:4, :4] = 0.0
    hazard[-4:, -4:] = 0.0
    return hazard, elevation


def board(hazard, elevation, rover=SURVEY) -> PlanningGrid:
    return PlanningGrid(hazard.copy(), elevation, 2.0, rover, max_hazard=0.85)


def euclidean_length(cells, meters_per_cell=2.0) -> float:
    return (
        sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(cells, cells[1:], strict=False))
        * meters_per_cell
    )


def timed(fn, repeats: int):
    durations, result = [], None
    for _ in range(repeats):
        started = time.perf_counter()
        result = fn()
        durations.append(time.perf_counter() - started)
    return statistics.median(durations), result


def bench_any_angle(sizes: list[int], repeats: int) -> None:
    print("\n1. A* vs Theta* - route shape on open ground broken by discrete rocks")
    print("   Theta* is NOT optimal. Cost is not comparable between the two: A* sums")
    print("   grid steps, Theta* integrates along straight segments. Length and turns are.")
    print("   Theta* pays for this in wall time - line-of-sight checks are not free.")
    header = (
        f"{'grid':>9} {'A* len m':>10} {'Th* len m':>10} {'shorter':>8} "
        f"{'A* turns':>9} {'Th* turns':>10} {'A* ms':>8} {'Th* ms':>8}"
    )
    print(f"\n{header}\n{'-' * len(header)}")

    for size in sizes:
        hazard, elevation = obstacle_field(size)
        grid = board(hazard, elevation)
        start, goal = (2, 2), (size - 3, size - 3)

        a_ms, a_res = timed(lambda: astar.search(grid, start, goal), repeats)
        t_ms, t_res = timed(lambda: theta_star.search(grid, start, goal), repeats)
        if not (a_res.found and t_res.found):
            print(f"{size:>4}x{size:<4} no route")
            continue

        a_len = euclidean_length(a_res.cells)
        t_len = euclidean_length(t_res.cells)
        print(
            f"{size:>4}x{size:<4} {a_len:>10.1f} {t_len:>10.1f} {1 - t_len / a_len:>7.1%} "
            f"{heading_changes(a_res.cells):>9} {heading_changes(t_res.cells):>10} "
            f"{a_ms * 1000:>8.1f} {t_ms * 1000:>8.1f}"
        )


def bench_incremental(sizes: list[int]) -> None:
    print("\n2. D* Lite repair vs from-scratch A*, under a sensor revealing 3 cells' radius")
    print("   Both are measured over the SAME traverse: at every step where the belief")
    print("   changed, D* Lite repairs and an A* is also run from scratch for comparison.")
    header = (
        f"{'grid':>9} {'steps':>6} {'repairs':>8} {'D*L exp':>9} "
        f"{'A* exp':>9} {'saved':>8} {'reroutes':>9}"
    )
    print(f"\n{header}\n{'-' * len(header)}")

    for size in sizes:
        hazard, elevation = terrain(size, seed=4)
        goal = (size - 2, size - 2)

        # Put the unmapped obstacles ON the route the rover is about to drive.
        # Scattering them at random mostly misses, and a benchmark where the
        # surprises never intersect the plan measures nothing about replanning.
        planned = astar.search(board(hazard, elevation), (1, 1), goal)
        truth = hazard.copy()
        rng = np.random.default_rng(size)
        if planned.found and len(planned.cells) > 12:
            for index in rng.choice(
                np.arange(6, len(planned.cells) - 4), size=6, replace=False
            ):
                row, col = planned.cells[int(index)]
                truth[
                    max(0, row - 1) : row + 2, max(0, col - 1) : col + 2
                ] = 0.99

        grid = board(hazard, elevation)
        planner = DStarLite(grid, (1, 1), goal)
        if not planner.reachable:
            print(f"{size:>4}x{size:<4} unreachable")
            continue

        repair_expansions = fresh_expansions = repairs = reroutes = steps = 0
        position = (1, 1)
        for _ in range(size * 6):
            if position == goal:
                break
            window = {
                (position[0] + dr, position[1] + dc): float(
                    truth[position[0] + dr, position[1] + dc]
                )
                for dr in range(-3, 4)
                for dc in range(-3, 4)
                if 0 <= position[0] + dr < size and 0 <= position[1] + dc < size
            }
            before = planner.next_step()
            record = planner.apply_updates(window)
            if record is not None:
                repairs += 1
                repair_expansions += record.vertices_expanded
                reroutes += 1 if planner.next_step() != before else 0
                fresh_expansions += astar.search(
                    board(planner.grid.hazard, elevation), position, goal
                ).nodes_expanded

            step = planner.next_step()
            if step is None:
                break
            planner.move_to(step)
            position = step
            steps += 1

        saved = 1 - repair_expansions / fresh_expansions if fresh_expansions else 0.0
        print(
            f"{size:>4}x{size:<4} {steps:>6} {repairs:>8} {repair_expansions:>9} "
            f"{fresh_expansions:>9} {saved:>7.1%} {reroutes:>9}"
        )


def bench_cbs(repeats: int) -> None:
    print("\n3. CBS - constraint-tree growth with fleet size, heterogeneous rovers")
    print("   Worst case on purpose: flat open ground, every rover crossing to the")
    print("   opposite corner, so every pair conflicts. CBS is exponential in the")
    print("   number of conflicts, and this is what that looks like.")
    header = (
        f"{'rovers':>7} {'CT nodes':>9} {'low-level':>10} {'conflicts':>10} "
        f"{'sum cost':>10} {'makespan':>9} {'ms':>8}"
    )
    print(f"\n{header}\n{'-' * len(header)}")

    size = 24
    hazard = np.zeros((size, size))
    hazard[0, :] = hazard[-1, :] = hazard[:, 0] = hazard[:, -1] = 0.99
    elevation = np.zeros((size, size))
    rovers = [SCOUT, HEAVY, SURVEY, SCOUT, HEAVY]

    # Every rover crosses the map to the opposite corner, so their routes all
    # pass through the middle and conflicts are guaranteed rather than incidental.
    for count in (2, 3, 4, 5):
        agents = [
            cbs.Agent(
                f"R{index + 1}",
                board(hazard, elevation, rovers[index]),
                (2 + index * 4, 2),
                (size - 3 - index * 4, size - 3),
            )
            for index in range(count)
        ]
        ms, outcome = timed(lambda a=agents: cbs.solve(a, max_high_level_nodes=2000, time_budget_seconds=120.0), repeats)
        if not outcome.succeeded:
            print(
                f"{count:>7} {outcome.failure.value:>9} - budget of "
                f"{outcome.diagnostics['high_level_nodes']} CT nodes exhausted"
            )
            continue
        solution = outcome.diagnostics["solution"]
        assert cbs.find_conflicts(solution.paths) == [], "CBS returned a colliding solution"
        print(
            f"{count:>7} {solution.high_level_nodes:>9} {solution.low_level_calls:>10} "
            f"{len(solution.conflicts_branched):>10} {solution.sum_of_costs:>10.1f} "
            f"{solution.makespan:>9} {ms * 1000:>8.1f}"
        )


def bench_pareto_and_monte_carlo(trials: int) -> None:
    print("\n4. Multi-objective sweep and Monte Carlo throughput")
    hazard, elevation = terrain(50, seed=9)
    started = time.perf_counter()
    front = pareto.sweep(hazard, elevation, (1, 1), (48, 48), SURVEY, 2.0, max_hazard=0.85)
    sweep_ms = (time.perf_counter() - started) * 1000

    print(
        f"\n   Pareto sweep: {len(front['weights_swept'])} weightings -> "
        f"{front['candidates_planned']} distinct routes -> "
        f"{len(front['front'])} on the front, {sweep_ms:.0f} ms"
    )
    header = f"   {'route':>26} {'dist m':>9} {'kWh':>8} {'mean haz':>9} {'max haz':>8}"
    print(f"{header}\n   {'-' * (len(header) - 3)}")
    for entry in front["front"]:
        objectives = entry["objectives"]
        print(
            f"   {entry['label']:>26} {objectives['distance_m']:>9.1f} "
            f"{objectives['energy_kwh']:>8.4f} {objectives['mean_hazard']:>9.3f} "
            f"{objectives['max_hazard']:>8.3f}"
        )

    orbital = np.random.default_rng(1).random((40, 40)) * 0.35
    mc_elevation = np.random.default_rng(2).random((40, 40)) * 0.4
    template = PlanningGrid(orbital.copy(), mc_elevation, 3.0, RoverSpec(0.6, 25.0, 0.003),
                            max_hazard=0.85)
    started = time.perf_counter()
    report = monte_carlo.run(
        orbital, mc_elevation, (1, 1), (38, 38), template,
        RoverSensor(range_m=10.0), trials=trials, seed=42,
    )
    elapsed = time.perf_counter() - started
    summary = report.as_dict()
    print(
        f"\n   Monte Carlo: {trials} trials in {elapsed:.1f}s "
        f"({trials / elapsed:.1f} trials/s)"
    )
    print(f"   Mission success probability: {summary['success_probability']:.1%}")
    print(f"   Failures by mode: {summary['failures_by_mode'] or 'none'}")
    energy = summary["energy_kwh"]
    if energy:
        print(
            f"   Energy over successful trials: P10 {energy['p10']:.4f} / "
            f"P50 {energy['p50']:.4f} / P90 {energy['p90']:.4f} kWh"
        )
        print("   P90 is the number a battery is sized against; the mean is not.")


def bench_comms() -> None:
    print("\n5. Communication coverage - geometric line of sight over a ridge")
    size = 40
    elevation = np.zeros((size, size))
    for offset, height in enumerate((3.0, 6.0, 6.0, 3.0)):
        elevation[18 + offset, :] = height
    # 4 m cells: the 3 m step onto the ridge is 37 degrees, inside the rover's
    # 45-degree limit, so the ridge blocks line of sight without blocking driving.
    grid = PlanningGrid(
        np.zeros((size, size)), elevation, 4.0, RoverSpec(6.0, 45.0, 0.003), max_hazard=0.85
    )

    plan = CommunicationPlan(
        stations=[RelayStation("LANDER", (2, 20), antenna_height_m=2.0)],
        orbiters=[OrbiterPass("RELAY-1", period_seconds=3600.0, duration_seconds=600.0)],
    )
    coverage = plan.coverage_mask(grid)
    route = astar.search(grid, (2, 2), (38, 38))
    stats = plan.evaluate_route(grid, route.cells)

    print(f"\n   Ground-relay coverage: {coverage.mean():.1%} of the map")
    print(f"   North of the ridge: {coverage[:16].mean():.1%}   south: {coverage[25:].mean():.1%}")
    print(
        f"   Route link: {stats['linked_cell_fraction']:.1%} of cells, "
        f"longest blackout {stats['longest_blackout_seconds'] / 60:.1f} min "
        f"over {stats['elapsed_seconds'] / 3600:.1f} h"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grid", type=int, nargs="+", default=[48, 64, 96])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--trials", type=int, default=30)
    args = parser.parse_args()

    print("AstraSynth V3 planner benchmarks")
    print("Shared cost model: distance_m * (1 + w_h*hazard) * (1 + w_e*k*|rise/run|)")

    bench_any_angle(args.grid, args.repeats)
    bench_incremental(args.grid)
    bench_cbs(args.repeats)
    bench_pareto_and_monte_carlo(args.trials)
    bench_comms()

    print("\nAll figures produced by this script on this machine. Nothing here is quoted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
