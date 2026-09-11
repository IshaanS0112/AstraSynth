import type { GridPoint } from "./types";
import type {
  Experiment,
  ExperimentSummary,
  Mission,
  MissionEvent,
  RiskReport,
  RoverConfig,
  RoverPath,
  ScienceTarget,
  TerrainAnalysis,
  TerrainGrid,
  TraverseRun,
  TraverseRunSummary,
} from "./types";

// In dev, Vite proxies /api -> :8000. In the Docker image, nginx does the same.
// Either way the browser only ever talks to its own origin.
const BASE = import.meta.env.VITE_API_BASE_URL ?? "/api";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly detail?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...init?.headers,
    },
  });

  if (!response.ok) {
    let detail: unknown;
    try {
      detail = (await response.json()).detail;
    } catch {
      detail = await response.text();
    }
    // FastAPI's detail is sometimes a string and sometimes an object; the
    // planner's "no traversable path" error uses the object form.
    const message =
      typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail !== null && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, detail);
  }

  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

/** Static assets (hazard heatmaps, slope maps) are served off the API origin. */
export function assetUrl(url: string | null): string | undefined {
  if (!url) return undefined;
  return BASE === "/api" ? url : `${BASE}${url}`;
}

export const api = {
  listMissions: () => request<Mission[]>("/missions"),
  getMission: (id: string) => request<Mission>(`/missions/${id}`),

  createMission: (form: FormData) =>
    request<Mission>("/missions", { method: "POST", body: form }),

  analyzeTerrain: (id: string) =>
    request<TerrainAnalysis>(`/missions/${id}/analyze-terrain`, { method: "POST" }),
  getTerrainAnalysis: (id: string) =>
    request<TerrainAnalysis>(`/missions/${id}/terrain-analysis`),

  planPath: (
    id: string,
    body: { start: { x: number; y: number }; end: { x: number; y: number }; rover_config_id: string },
  ) => request<RoverPath>(`/missions/${id}/plan-path`, { method: "POST", body: JSON.stringify(body) }),
  getPath: (id: string) => request<RoverPath>(`/missions/${id}/path`),

  assessRisk: (id: string) =>
    request<RiskReport>(`/missions/${id}/assess-risk`, { method: "POST", body: JSON.stringify({}) }),
  getRiskReport: (id: string) => request<RiskReport>(`/missions/${id}/risk-report`),

  generateReport: (id: string) =>
    request<RiskReport>(`/missions/${id}/generate-report`, { method: "POST" }),

  listRoverConfigs: () => request<RoverConfig[]>("/rover-configs"),

  // --- V3: mission autonomy -------------------------------------------------

  /** Every per-cell layer in one call - they are rendered and toggled together. */
  getTerrainGrid: (id: string, maxDim = 96) =>
    request<TerrainGrid>(`/missions/${id}/terrain-grid?max_dim=${maxDim}`),

  listScienceTargets: (id: string) =>
    request<ScienceTarget[]>(`/missions/${id}/science-targets`),
  createScienceTarget: (
    id: string,
    body: {
      label: string;
      x: number;
      y: number;
      value?: number;
      priority?: string;
      required_instrument?: string | null;
    },
  ) =>
    request<ScienceTarget>(`/missions/${id}/science-targets`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteScienceTarget: (id: string, targetId: string) =>
    request<void>(`/missions/${id}/science-targets/${targetId}`, { method: "DELETE" }),

  simulateTraverse: (
    id: string,
    body: {
      start: GridPoint;
      goal: GridPoint;
      rover_config_id: string;
      seed?: number;
      uncertainty_weight?: number;
      unmapped_obstacles?: number;
      energy_budget_kwh?: number | null;
    },
  ) =>
    request<TraverseRun>(`/missions/${id}/simulate-traverse`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listTraverses: (id: string) => request<TraverseRunSummary[]>(`/missions/${id}/traverses`),
  getTraverse: (id: string, runId: string) =>
    request<TraverseRun>(`/missions/${id}/traverses/${runId}`),
  getTraverseEvents: (id: string, runId: string, category?: string) =>
    request<MissionEvent[]>(
      `/missions/${id}/traverses/${runId}/events${category ? `?category=${category}` : ""}`,
    ),

  routeStudy: (id: string, body: { start: GridPoint; goal: GridPoint; rover_config_id: string }) =>
    request<Experiment>(`/missions/${id}/route-study`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  monteCarlo: (
    id: string,
    body: {
      start: GridPoint;
      goal: GridPoint;
      rover_config_id: string;
      trials: number;
      seed: number;
      uncertainty_weight?: number;
    },
  ) =>
    request<Experiment>(`/missions/${id}/monte-carlo`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  fleetPlan: (
    id: string,
    body: {
      assignments: {
        label: string;
        rover_config_id: string;
        start: GridPoint;
        goal: GridPoint;
      }[];
      time_budget_seconds?: number;
    },
  ) =>
    request<Experiment>(`/missions/${id}/fleet-plan`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  scienceTour: (
    id: string,
    body: {
      start: GridPoint;
      rover_config_id: string;
      instruments: string[];
      energy_budget_kwh?: number | null;
      relay_at?: GridPoint | null;
    },
  ) =>
    request<Experiment>(`/missions/${id}/science-tour`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listExperiments: (id: string, kind?: string) =>
    request<ExperimentSummary[]>(
      `/missions/${id}/experiments${kind ? `?kind=${kind}` : ""}`,
    ),
  getExperiment: (id: string, experimentId: string) =>
    request<Experiment>(`/missions/${id}/experiments/${experimentId}`),
};
