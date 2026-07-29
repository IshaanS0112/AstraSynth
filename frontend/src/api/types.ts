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
