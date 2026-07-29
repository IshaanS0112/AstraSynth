import type {
  Mission,
  RiskReport,
  RoverConfig,
  RoverPath,
  TerrainAnalysis,
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
};
