export type MissionStatus =
  | "PENDING"
  | "ANALYZED"
  | "PATH_PLANNED"
  | "RISK_ASSESSED"
  | "REPORT_GENERATED";

export type RiskTier = "LOW" | "MEDIUM" | "HIGH";
export type Feasibility = "FEASIBLE" | "FEASIBLE_WITH_MARGIN" | "INFEASIBLE";

export interface Mission {
  id: string;
  name: string;
  terrain_source: string | null;
  status: MissionStatus;
  created_at: string;
  terrain_image_url: string | null;
}

export interface Obstacle {
  id: number;
  x: number;
  y: number;
  area_px: number;
  radius_px: number;
  area_m2: number;
}

export interface TerrainAnalysis {
  id: string;
  mission_id: string;
  terrain_classification: string | null;
  obstacle_contours: Obstacle[] | null;
  analysis_metadata: Record<string, any> | null;
  analyzed_at: string;
  slope_map_url: string | null;
  hazard_heatmap_url: string | null;
}

export interface Waypoint {
  segment_id: number;
  x: number;
  y: number;
  hazard_score: number;
  slope_deg: number;
  step_distance_m: number;
  energy_cost_kwh: number;
  cumulative_energy_kwh: number;
}

export interface RoverPath {
  id: string;
  mission_id: string;
  rover_config_id: string;
  start_point: { x: number; y: number };
  end_point: { x: number; y: number };
  waypoints: Waypoint[];
  total_distance_m: number | null;
  total_energy_cost_kwh: number | null;
  algorithm_used: string;
  planner_metadata: Record<string, any> | null;
  planned_at: string;
}

export interface RoverConfig {
  id: string;
  name: string;
  battery_capacity_kwh: number;
  max_traversable_slope_deg: number;
  energy_per_meter_kwh: number;
}

export interface HighHazardSegment {
  segment_id: number;
  hazard_score: number;
  slope_deg: number;
  x: number;
  y: number;
  cumulative_energy_kwh: number;
}

export interface StructuredContext {
  mission_id: string;
  mission_name: string;
  terrain_source: string;
  terrain_summary: {
    avg_hazard_score: number;
    max_hazard_score: number;
    obstacle_count: number;
    terrain_classification: string | null;
    mean_slope_deg: number;
    max_slope_deg: number;
    area_covered_m: { width: number; height: number };
  };
  path_summary: {
    total_distance_m: number;
    total_energy_cost_kwh: number;
    num_waypoints: number;
    mean_path_hazard: number;
    max_path_hazard: number;
    max_slope_encountered_deg: number;
    high_hazard_segments: HighHazardSegment[];
    algorithm: string;
    nodes_expanded: number;
  };
  rover_constraints: {
    name: string;
    battery_capacity_kwh: number;
    energy_margin_kwh: number;
    energy_utilisation: number;
    max_traversable_slope_deg: number;
    energy_per_meter_kwh: number;
  };
  risk_score: RiskTier;
  risk_score_numeric: number;
  feasibility: Feasibility;
  calculation_basis: Record<string, any>;
}

export interface AiNarrative {
  summary: string;
  top_risks: { segment_id: number; reason: string }[];
  recommendation: string;
  generated_by: "llm" | "template_fallback";
  fallback_reason?: string;
  dropped_citations?: number;
}

export interface RiskReport {
  id: string;
  mission_id: string;
  rover_path_id: string;
  risk_score: RiskTier | null;
  feasibility: Feasibility | null;
  structured_context: StructuredContext;
  ai_narrative: AiNarrative | null;
  narrative_source: string | null;
  generated_at: string;
}

// --- V3: mission autonomy ---------------------------------------------------

export interface TerrainGrid {
  rows: number;
  cols: number;
  /** Image pixels per rendered cell. Every waypoint the API returns is in pixels. */
  pixel_scale: number;
  meters_per_cell: number;
  elevation_range_m: number;
  lethal_hazard_threshold: number;
  layers: {
    elevation_m: number[][];
    hazard: number[][];
    uncertainty: number[][];
    slope_deg: number[][];
    lethal: number[][];
  };
  ranges: Record<string, [number, number]>;
}

export type TerrainLayerName = "elevation_m" | "hazard" | "uncertainty" | "slope_deg";

export interface ScienceTarget {
  id: string;
  mission_id: string;
  label: string;
  x: number;
  y: number;
  value: number;
  priority: "low" | "medium" | "high";
  observation_seconds: number;
  required_instrument: string | null;
  created_at: string;
}

/**
 * A point in original-image pixel coordinates.
 *
 * Named separately from the V1 `Waypoint`, which is a planner output carrying
 * hazard, slope and cumulative energy. Declaring both as `Waypoint` in one
 * module does not collide - TypeScript merges same-named interfaces - it
 * silently produces a type requiring every field of both.
 */
export interface GridPoint {
  x: number;
  y: number;
}

export interface MissionEvent {
  t_seconds: number;
  category: "navigation" | "planner" | "energy" | "risk" | "system";
  kind: string;
  detail: Record<string, unknown>;
}

export interface TraverseRun {
  id: string;
  mission_id: string;
  rover_config_id: string;
  status: "SUCCESS" | "FAILED";
  failure_mode: string | null;
  reason: string | null;
  start_point: GridPoint;
  goal_point: GridPoint;
  parameters: Record<string, any>;
  distance_m: number;
  energy_kwh: number;
  elapsed_seconds: number;
  replans: number;
  reroutes: number;
  repair_expansions: number;
  initial_plan: GridPoint[];
  executed_path: GridPoint[];
  events: MissionEvent[];
  belief_summary: Record<string, number | null>;
  created_at: string;
}

export interface TraverseRunSummary {
  id: string;
  status: "SUCCESS" | "FAILED";
  failure_mode: string | null;
  distance_m: number;
  energy_kwh: number;
  elapsed_seconds: number;
  replans: number;
  reroutes: number;
  repair_expansions: number;
  created_at: string;
}

export interface RouteCandidate {
  label: string;
  search_weights: { hazard: number; energy: number };
  objectives: {
    distance_m: number;
    energy_kwh: number;
    mean_hazard: number;
    max_hazard: number;
  };
  nodes_expanded: number;
  heading_changes: number;
  waypoint_count: number;
  waypoints: GridPoint[];
}

export interface Experiment {
  id: string;
  mission_id: string;
  kind: "route_study" | "monte_carlo" | "fleet_plan" | "science_tour";
  status: "SUCCESS" | "FAILED";
  failure_mode: string | null;
  reason: string | null;
  parameters: Record<string, any>;
  seed: number | null;
  code_revision: string | null;
  result: Record<string, any>;
  runtime_seconds: number;
  created_at: string;
}

export interface ExperimentSummary {
  id: string;
  kind: Experiment["kind"];
  status: "SUCCESS" | "FAILED";
  failure_mode: string | null;
  seed: number | null;
  code_revision: string | null;
  runtime_seconds: number;
  created_at: string;
}
