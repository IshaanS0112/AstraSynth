#!/usr/bin/env python3
"""ARES VALLEY RECON - the full V3 pipeline on one mission, end to end.

Runs every stage against real generated terrain and prints what each one
decided. Nothing here is mocked and nothing is quoted from elsewhere: every
number below is computed during the run.

    orbital terrain
        -> hazard field + propagated uncertainty
        -> science target selection under an energy budget
        -> communication coverage over the route
        -> traverse under partial observability, D* Lite repairing as the
           rover discovers ground the orbital map did not show
        -> multi-rover deconfliction
        -> Monte Carlo over the whole thing

Usage::

    python scripts/run_mission_demo.py
    python scripts/run_mission_demo.py --size 384 --trials 100
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))

import cv2  # noqa: E402

from app.config import Settings  # noqa: E402
from app.services import hazard_mapper, terrain_analyzer  # noqa: E402
from app.services.mission.comms import (  # noqa: E402
    CommunicationPlan,
    OrbiterPass,
    RelayStation,
)
from app.services.mission.scheduling import Activity, ActivityKind, Timeline  # noqa: E402
from app.services.mission.science import ScienceTarget, plan_science_tour  # noqa: E402
from app.services.planning import cbs, pareto  # noqa: E402
from app.services.planning.grid import PlanningGrid, RoverSpec  # noqa: E402
from app.services.simulation import monte_carlo  # noqa: E402
from app.services.simulation.belief import BeliefState  # noqa: E402
from app.services.simulation.execution import simulate_traverse  # noqa: E402
from app.services.simulation.sensors import RoverSensor  # noqa: E402
from generate_terrain import generate  # noqa: E402

# Three rovers spanning the capability space. Illustrative planning parameters
# of the right order of magnitude for solar planetary rovers - not manufacturer
# specifications, and not claimed to be.
FLEET = {
    "SCOUT-01": RoverSpec(2.0, 20.0, 0.0018, mass_kg=180.0, payload_kg=10.0),
    "SURVEY-02": RoverSpec(6.0, 25.0, 0.0030, mass_kg=250.0, payload_kg=40.0),
    "HEAVY-03": RoverSpec(9.0, 30.0, 0.0062, mass_kg=520.0, payload_kg=140.0),
}
INSTRUMENTS = {
    "SCOUT-01": frozenset({"camera"}),
    "SURVEY-02": frozenset({"camera", "spectrometer"}),
    "HEAVY-03": frozenset({"camera", "spectrometer", "drill"}),
}


def rule(title: str) -> None:
    print(f"\n{'=' * 78}\n{title}\n{'=' * 78}")


def build_terrain(size: int, settings: Settings):
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        cv2.imwrite(handle.name, generate("crater_field", size, seed=17))
        analysis = terrain_analyzer.analyze_terrain(handle.name, settings)
    return analysis, hazard_mapper.build_hazard_map(analysis, settings)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=256)
    parser.add_argument("--trials", type=int, default=40)
    parser.add_argument("--planning-dim", type=int, default=96)
    args = parser.parse_args()

    settings = Settings(_env_file=None)

    # --- 1. Terrain and hazard --------------------------------------------
    rule("1. ORBITAL TERRAIN ANALYSIS")
    analysis, hazard = build_terrain(args.size, settings)
    uncertainty = hazard.calculation_basis["uncertainty"]
    print(f"Terrain classified as   : {analysis.classification.value}")
    print(f"Obstacles detected      : {len(analysis.obstacles)}")
    print(
        f"Mean / p95 slope        : {analysis.stats['slope']['mean_deg']:.2f} deg / "
        f"{analysis.stats['slope']['p95_deg']:.2f} deg"
    )
    print(
        f"Mean / max hazard       : {hazard.calculation_basis['aggregate']['mean_hazard']:.4f} / "
        f"{hazard.calculation_basis['aggregate']['max_hazard']:.4f}"
    )
    print(
        f"Mean / p95 hazard sigma : {uncertainty['mean_sigma']:.4f} / "
        f"{uncertainty['p95_sigma']:.4f}"
    )
    print(f"  from: {', '.join(uncertainty['component_mean_sigma'])}")
    print("  The orbital map is a prior, not the truth. The rover finds out the rest.")

    grid_hazard, scale = hazard_mapper.downsample_for_planning(
        hazard.scores, args.planning_dim
    )
    grid_elevation, _ = hazard_mapper.downsample_for_planning(
        analysis.elevation_m.astype(np.float32), args.planning_dim
    )
    grid_sigma, _ = hazard_mapper.downsample_for_planning(hazard.uncertainty, args.planning_dim)
    meters_per_cell = settings.meters_per_pixel * scale
    rows, cols = grid_hazard.shape
    print(
        f"\nPlanning grid           : {rows}x{cols} cells at "
        f"{meters_per_cell:.1f} m/cell ({rows * meters_per_cell:.0f} m across)"
    )

    survey = PlanningGrid(
        grid_hazard.astype(np.float64),
        grid_elevation.astype(np.float64),
        meters_per_cell,
        FLEET["SURVEY-02"],
        slope_coefficient=settings.energy_slope_coefficient,
        max_hazard=settings.lethal_hazard_threshold,
    )

    def free_cell(preferred, radius=6):
        """Nearest non-lethal cell to a preferred spot, so the demo never starts inside a crater."""
        for r in range(radius):
            for d_row in range(-r, r + 1):
                for d_col in range(-r, r + 1):
                    cell = (preferred[0] + d_row, preferred[1] + d_col)
                    if survey.in_bounds(cell) and not survey.is_lethal(cell):
                        return cell
        raise RuntimeError("no traversable cell near the requested position")

    base = free_cell((3, 3))

    # --- 2. Science targets ------------------------------------------------
    rule("2. SCIENCE TARGET SELECTION - SURVEY-02, 5.0 kWh allocation")
    targets = [
        ScienceTarget("OUTCROP-A", free_cell((int(rows * 0.20), int(cols * 0.75))), 0.91,
                      required_instrument="spectrometer", priority="high"),
        ScienceTarget("DUNE-B", free_cell((int(rows * 0.78), int(cols * 0.20))), 0.65),
        ScienceTarget("RIM-C", free_cell((int(rows * 0.85), int(cols * 0.85))), 0.80,
                      required_instrument="spectrometer"),
        ScienceTarget("PLAIN-D", free_cell((int(rows * 0.50), int(cols * 0.50))), 0.40),
        ScienceTarget("VEIN-E", free_cell((int(rows * 0.10), int(cols * 0.92))), 0.72,
                      required_instrument="drill", priority="high"),
        ScienceTarget("SCARP-F", free_cell((int(rows * 0.60), int(cols * 0.08))), 0.55),
    ]
    outcome = plan_science_tour(
        survey, base, targets,
        energy_budget_kwh=5.0,
        instruments=INSTRUMENTS["SURVEY-02"],
    )
    if not outcome.succeeded:
        print(f"Tour planning failed: {outcome.failure.value} - {outcome.reason}")
        return 1
    tour = outcome.diagnostics["tour"]
    print(f"Visiting {len(tour.visited)} of {len(targets)} targets, in this order:")
    for index, target in enumerate(tour.visited, start=1):
        print(
            f"  {index}. {target.target_id:<12} value {target.value:.2f}  "
            f"priority {target.priority}"
        )
    for entry in tour.skipped:
        print(f"  -- {entry['target_id']:<12} SKIPPED: {entry['reason']} ({entry['detail']})")
    print(
        f"\nScience value collected : {tour.science_value:.2f}"
        f"\nDrive distance          : {tour.distance_m:.0f} m"
        f"\nEnergy                  : {tour.energy_kwh:.4f} / {tour.energy_budget_kwh:.3f} kWh"
        f" ({tour.energy_kwh / tour.energy_budget_kwh:.0%} of budget)"
        f"\n2-opt improvements      : {tour.improvement_passes}"
    )

    # --- 3. Communications -------------------------------------------------
    rule("3. COMMUNICATION COVERAGE OVER THE TOUR")
    comms = CommunicationPlan(
        stations=[RelayStation("LANDER", base, antenna_height_m=3.0, max_range_m=2000.0)],
        orbiters=[OrbiterPass("RELAY-1", period_seconds=6 * 3600.0, duration_seconds=900.0)],
    )
    link = comms.evaluate_route(survey, tour.cells)
    print(f"Ground-relay coverage   : {comms.coverage_mask(survey).mean():.1%} of the map")
    print(f"Route cells with a link : {link['linked_cell_fraction']:.1%}")
    print(f"Longest blackout        : {link['longest_blackout_seconds'] / 60:.1f} min")
    print(f"Traverse duration       : {link['elapsed_seconds'] / 3600:.1f} h")

    # --- 4. Schedule -------------------------------------------------------
    rule("4. MISSION TIMELINE")
    timeline = Timeline()
    clock = 0.0
    for leg, target in zip(tour.legs, tour.visited, strict=True):
        timeline.add(Activity(ActivityKind.TRAVEL, clock, leg.seconds, f"drive to {target.target_id}"))
        clock += leg.seconds
        timeline.add(
            Activity(
                ActivityKind.SCIENCE, clock, target.observation_seconds,
                f"observe {target.target_id}",
                {"value": target.value, "instrument": target.required_instrument},
            )
        )
        clock += target.observation_seconds
        start, end = comms.orbiters[0].next_window(clock)
        if start - clock < 4 * 3600.0:
            timeline.add(Activity(ActivityKind.WAIT, clock, start - clock, "await RELAY-1"))
            timeline.add(Activity(ActivityKind.COMMUNICATE, start, end - start, "RELAY-1 downlink"))
            clock = end
    for activity in timeline.activities:
        sol = timeline.sol_of(activity.start_seconds)
        hours = (activity.start_seconds % timeline.sol_seconds) / 3600.0
        print(
            f"  SOL {sol}  {int(hours):02d}:{int((hours % 1) * 60):02d}  "
            f"{activity.kind.value:<12} {activity.label:<26} "
            f"{activity.duration_seconds / 60:6.1f} min"
        )
    print(f"\nMission span            : {timeline.end_seconds / timeline.sol_seconds:.2f} sols")
    print(f"Time by activity        : {timeline.duration_by_kind()}")

    # --- 5. Traverse under partial observability ---------------------------
    rule("5. EXECUTION - SURVEY-02 DRIVES LEG 1 WITHOUT KNOWING THE TERRAIN")
    first_leg_goal = tour.visited[0].cell
    rng = np.random.default_rng(2024)

    # Reality: the orbital hazard field plus rocks the orbital map never resolved.
    truth = grid_hazard.astype(np.float64).copy()
    planned = tour.legs[0].cells
    for index in rng.choice(np.arange(4, max(5, len(planned) - 3)), size=6, replace=False):
        row, col = planned[int(index)]
        truth[max(0, row - 1) : row + 2, max(0, col - 1) : col + 2] = 0.97

    for label, weight in (("hazard only (V1 behaviour)", 0.0), ("uncertainty-averse", 1.5)):
        belief = BeliefState.from_prior(
            grid_hazard.astype(np.float64), prior_sigma=float(np.mean(grid_sigma)) + 0.15
        )
        result = simulate_traverse(
            truth_hazard=truth,
            start=base,
            goal=first_leg_goal,
            belief=belief,
            sensor=RoverSensor(range_m=6 * meters_per_cell, noise_sigma=0.05),
            grid_template=survey,
            rng=np.random.default_rng(2024),
            uncertainty_weight=weight,
        )
        status = result.outcome.status if result.succeeded else result.outcome.failure.value
        print(
            f"\n  planner surface: {label}"
            f"\n    outcome            : {status}"
            f"\n    distance / energy  : {result.distance_m:.0f} m / {result.energy_kwh:.4f} kWh"
            f"\n    D* Lite repairs    : {result.replans} "
            f"({result.reroutes} of them changed the route)"
            f"\n    vertices repaired  : {result.repair_expansions}"
            f"\n    map observed       : {result.belief_summary['observed_fraction']:.1%} of cells"
        )
    print(
        "\n  Every repair above is incremental. The equivalent from-scratch A* count"
        "\n  is in scripts/benchmark_v3.py, section 2."
    )

    # --- 6. Trade-off surface ---------------------------------------------
    rule("6. ROUTE TRADE-OFFS - THERE IS NO SINGLE BEST ROUTE")
    front = pareto.sweep(
        grid_hazard.astype(np.float64), grid_elevation.astype(np.float64),
        base, first_leg_goal, FLEET["SURVEY-02"], meters_per_cell,
        slope_coefficient=settings.energy_slope_coefficient,
        max_hazard=settings.lethal_hazard_threshold,
    )
    print(
        f"{len(front['weights_swept'])} weightings -> {front['candidates_planned']} distinct "
        f"routes -> {len(front['front'])} non-dominated\n"
    )
    print(f"  {'route':>26} {'dist m':>9} {'kWh':>9} {'mean haz':>9} {'max haz':>8}")
    print(f"  {'-' * 64}")
    for entry in front["front"]:
        objectives = entry["objectives"]
        print(
            f"  {entry['label']:>26} {objectives['distance_m']:>9.1f} "
            f"{objectives['energy_kwh']:>9.4f} {objectives['mean_hazard']:>9.3f} "
            f"{objectives['max_hazard']:>8.3f}"
        )
    if front["extremes"]:
        print(f"\n  Cheapest energy : {front['extremes']['energy_kwh']}")
        print(f"  Lowest hazard   : {front['extremes']['mean_hazard']}")
        print("  Picking between these is a mission decision, not a solver decision.")

    # --- 7. Fleet deconfliction -------------------------------------------
    rule("7. FLEET DECONFLICTION - THREE ROVERS, THREE DIFFERENT GRAPHS")
    # Fleet coordination runs on a COARSER grid than local navigation, and that
    # is a deliberate choice rather than a shortcut. CBS cost grows with the
    # low-level state space, which is cells x ticks, so halving the grid
    # resolution cuts it by roughly eight. It costs nothing that matters here:
    # the coordination question is "who goes through the middle first", not
    # "which rock do I pass on the left", and the second is answered by D* Lite
    # at full resolution once each rover is driving its own leg.
    fleet_dim = max(24, args.planning_dim // 2)
    fleet_hazard, fleet_scale = hazard_mapper.downsample_for_planning(
        grid_hazard, fleet_dim
    )
    fleet_elevation, _ = hazard_mapper.downsample_for_planning(grid_elevation, fleet_dim)
    fleet_mpc = meters_per_cell * fleet_scale
    f_rows, f_cols = fleet_hazard.shape
    print(
        f"Coordination grid       : {f_rows}x{f_cols} at {fleet_mpc:.1f} m/cell "
        f"(navigation runs at {rows}x{cols})"
    )

    def fleet_free(preferred, radius=6):
        for r in range(radius):
            for d_row in range(-r, r + 1):
                for d_col in range(-r, r + 1):
                    cell = (preferred[0] + d_row, preferred[1] + d_col)
                    if (
                        0 <= cell[0] < f_rows
                        and 0 <= cell[1] < f_cols
                        and float(fleet_hazard[cell]) < settings.lethal_hazard_threshold
                    ):
                        return cell
        raise RuntimeError("no traversable cell near the requested fleet position")

    # All three deploy from the lander area and converge on the same far corner,
    # so their routes share a corridor and conflicts are structural rather than
    # incidental. Rovers that never meet do not exercise a deconfliction solver.
    agents = []
    starts = [(2, 2), (2, 5), (5, 2)]
    goals = [
        (f_rows - 3, f_cols - 3),
        (f_rows - 3, f_cols - 6),
        (f_rows - 6, f_cols - 3),
    ]
    for index, (name, spec) in enumerate(FLEET.items()):
        agent_grid = PlanningGrid(
            fleet_hazard.astype(np.float64), fleet_elevation.astype(np.float64),
            fleet_mpc, spec,
            slope_coefficient=settings.energy_slope_coefficient,
            max_hazard=settings.lethal_hazard_threshold,
        )
        agents.append(
            cbs.Agent(name, agent_grid, fleet_free(starts[index]), fleet_free(goals[index]))
        )
    fleet_outcome = cbs.solve(agents, max_high_level_nodes=1500, time_budget_seconds=45.0)
    if fleet_outcome.succeeded:
        solution = fleet_outcome.diagnostics["solution"]
        assert cbs.find_conflicts(solution.paths) == []
        print(f"Deconflicted in {solution.high_level_nodes} constraint-tree nodes "
              f"({solution.low_level_calls} single-agent searches)")
        print(f"Conflicts branched on   : {len(solution.conflicts_branched)}")
        print(f"Sum of costs / makespan : {solution.sum_of_costs:.1f} / {solution.makespan} ticks")
        for agent in agents:
            path = solution.paths[agent.agent_id]
            print(
                f"  {agent.agent_id:<10} {len(path) - 1:>3} steps, cost "
                f"{solution.costs[agent.agent_id]:8.1f}, slope limit "
                f"{agent.grid.rover.max_traversable_slope_deg:.0f} deg"
            )
        print("Verified conflict-free by an independent check, not by the solver's own report.")
    else:
        print(f"CBS returned {fleet_outcome.failure.value}: {fleet_outcome.reason}")

    # --- 8. Monte Carlo ----------------------------------------------------
    rule(f"8. MISSION ROBUSTNESS - {args.trials} TRIALS, SEED 2024")
    template = PlanningGrid(
        grid_hazard.astype(np.float64), grid_elevation.astype(np.float64),
        meters_per_cell, FLEET["SURVEY-02"],
        slope_coefficient=settings.energy_slope_coefficient,
        max_hazard=settings.lethal_hazard_threshold,
    )
    report = monte_carlo.run(
        grid_hazard.astype(np.float64), grid_elevation.astype(np.float64),
        base, first_leg_goal, template,
        RoverSensor(range_m=6 * meters_per_cell), trials=args.trials, seed=2024,
    )
    summary = report.as_dict()
    print(f"Mission success         : {summary['success_probability']:.1%}")
    print(f"Failures by mode        : {summary['failures_by_mode'] or 'none'}")
    for field, unit, divisor in (
        ("energy_kwh", "kWh", 1.0),
        ("elapsed_seconds", "h", 3600.0),
        ("distance_m", "m", 1.0),
    ):
        stats = summary[field]
        if stats:
            print(
                f"{field:<24}: P10 {stats['p10'] / divisor:.3f} / "
                f"P50 {stats['p50'] / divisor:.3f} / P90 {stats['p90'] / divisor:.3f} {unit}"
            )
    print("\nPercentiles are over successful trials; the success rate is over all of them.")
    print("Same seed, same numbers - every figure above is reproducible.")

    rule("SCOPE")
    print(
        "Synthetic terrain, illustrative rover parameters, no flight heritage, no\n"
        "validation against real mission data. What is real is the computation: every\n"
        "number printed above came out of the code in this run."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
