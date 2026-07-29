#!/usr/bin/env python3
"""Run the full analysis pipeline on one terrain image, with no database.

Useful for three things: sanity-checking the engines after a change, tuning the
classification and hazard constants against a known image, and demonstrating in
an interview that the numbers come out of the algorithms rather than out of the
API layer.

Usage::

    python scripts/run_pipeline_demo.py data/sample_terrain/synthetic_crater_field_512.png
    python scripts/run_pipeline_demo.py <image> --rover survey --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import Settings  # noqa: E402
from app.services import hazard_mapper, report_generator, risk_engine, terrain_analyzer  # noqa: E402
from app.services.path_planner import (  # noqa: E402
    PathNotFoundError,
    RoverSpec,
    plan_path,
    plan_path_dijkstra,
)

# Mirrors the seeded rover configs in app/main.py.
ROVERS = {
    "scout": RoverSpec(2.0, 20.0, 0.0018),
    "survey": RoverSpec(6.0, 25.0, 0.0030),
    "heavy": RoverSpec(9.0, 30.0, 0.0062),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--rover", default="survey", choices=list(ROVERS))
    parser.add_argument("--json", action="store_true", help="Dump the structured context")
    parser.add_argument("--compare-dijkstra", action="store_true")
    args = parser.parse_args()

    settings = Settings()
    rover = ROVERS[args.rover]

    # --- Stage 1: terrain analysis ---
    analysis = terrain_analyzer.analyze_terrain(args.image, settings)
    hazard = hazard_mapper.build_hazard_map(analysis, settings)

    print(f"\n=== TERRAIN ANALYSIS: {args.image.name} ===")
    print(f"  size              {analysis.stats['image_size_px']}")
    print(f"  classification    {analysis.classification.value}")
    print(f"  rule fired        {analysis.stats['classification_evidence']['rule_fired']}")
    print(f"  slope mean/max    {analysis.stats['slope']['mean_deg']} / "
          f"{analysis.stats['slope']['max_deg']} deg")
    print(f"  roughness mean    {analysis.stats['roughness']['mean']}")
    print(f"  obstacles         {len(analysis.obstacles)} "
          f"(density {analysis.stats['classification_evidence']['obstacle_density_per_mpx']}/Mpx)")
    print(f"  hazard mean/max   {hazard.calculation_basis['aggregate']['mean_hazard']} / "
          f"{hazard.calculation_basis['aggregate']['max_hazard']}")
    print(f"  hazard components {hazard.calculation_basis['component_means']}")

    # --- Stage 2: path planning ---
    hazard_grid, scale = hazard_mapper.downsample_for_planning(
        hazard.scores, settings.planning_grid_max_dim
    )
    elevation_grid, _ = hazard_mapper.downsample_for_planning(
        analysis.elevation_m, settings.planning_grid_max_dim
    )
    meters_per_cell = settings.meters_per_pixel * scale

    height, width = analysis.shape
    start = {"x": int(width * 0.05), "y": int(height * 0.05)}
    goal = {"x": int(width * 0.93), "y": int(height * 0.93)}

    print(f"\n=== PATH PLANNING ({args.rover}) ===")
    try:
        path = plan_path(
            hazard_grid, elevation_grid, start, goal, rover,
            meters_per_cell, scale, settings.energy_slope_coefficient,
            settings.lethal_hazard_threshold,
        )
    except PathNotFoundError as exc:
        print(f"  INFEASIBLE - no traversable route: {exc}")
        return 1

    print(f"  grid              {hazard_grid.shape} @ {meters_per_cell:.2f} m/cell")
    print(f"  waypoints         {len(path.waypoints)}")
    print(f"  distance          {path.total_distance_m} m")
    print(f"  energy            {path.total_energy_cost_kwh:.4f} kWh "
          f"of {rover.battery_capacity_kwh} kWh")
    print(f"  nodes expanded    {path.metadata['nodes_expanded']}")
    print(f"  blocked by slope  {path.metadata['moves_blocked_by_slope_limit']} moves")

    if args.compare_dijkstra:
        dijkstra = plan_path_dijkstra(
            hazard_grid, elevation_grid, start, goal, rover,
            meters_per_cell, scale, settings.energy_slope_coefficient,
            settings.lethal_hazard_threshold,
        )
        saved = 1 - path.metadata["nodes_expanded"] / dijkstra.metadata["nodes_expanded"]
        print("\n=== A* vs DIJKSTRA (same cost function) ===")
        print(f"  A*        {path.metadata['nodes_expanded']:>7} nodes, "
              f"cost {path.total_cost}")
        print(f"  Dijkstra  {dijkstra.metadata['nodes_expanded']:>7} nodes, "
              f"cost {dijkstra.total_cost}")
        print(f"  A* expanded {saved:.1%} fewer nodes; "
              f"costs equal: {abs(path.total_cost - dijkstra.total_cost) < 1e-6}")

    # --- Stage 3: risk ---
    risk = risk_engine.assess_mission_risk(path, rover, settings)
    print("\n=== RISK ASSESSMENT ===")
    print(f"  risk score        {risk.risk_score} -> {risk.risk_tier.value}")
    print(f"  feasibility       {risk.feasibility.value}")
    print(f"  energy margin     {risk.energy_margin_kwh} kWh "
          f"({risk.energy_utilisation:.1%} used)")
    print(f"  path hazard       mean {risk.mean_path_hazard}, peak {risk.max_path_hazard}")
    print(f"  max slope         {risk.max_slope_encountered_deg} deg")
    print(f"  worst segments    {[s['segment_id'] for s in risk.high_hazard_segments]}")

    # --- Stage 4: report ---
    context = report_generator.build_structured_context(
        mission_id="demo",
        mission_name=f"Demo traverse - {args.image.stem}",
        terrain_source="synthetic (scripts/generate_terrain.py)",
        terrain_metadata={
            **analysis.stats,
            "hazard_calculation_basis": hazard.calculation_basis,
        },
        obstacle_count=len(analysis.obstacles),
        classification=analysis.classification.value,
        path=path,
        rover=rover,
        rover_name=args.rover,
        risk=risk,
    )
    narrative = report_generator.generate_narrative(context, settings)

    print(f"\n=== MISSION REPORT (source: {narrative['generated_by']}) ===")
    print(f"  {narrative['summary']}")
    for item in narrative["top_risks"]:
        print(f"  - segment {item['segment_id']}: {item['reason']}")
    print(f"  RECOMMENDATION: {narrative['recommendation']}")

    if args.json:
        print("\n=== STRUCTURED CONTEXT (pre-LLM) ===")
        print(json.dumps(context, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
