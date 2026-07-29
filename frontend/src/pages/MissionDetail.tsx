import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type {
  Mission,
  RiskReport,
  RoverConfig,
  RoverPath,
  TerrainAnalysis,
} from "../api/types";
import RiskReportView from "../components/RiskReportView";
import TerrainViewer, { type Marker } from "../components/TerrainViewer";

type Busy = "analyze" | "plan" | "assess" | "report" | null;

const STAGES = ["PENDING", "ANALYZED", "PATH_PLANNED", "RISK_ASSESSED", "REPORT_GENERATED"];

export default function MissionDetail() {
  const { missionId = "" } = useParams();

  const [mission, setMission] = useState<Mission | null>(null);
  const [analysis, setAnalysis] = useState<TerrainAnalysis | null>(null);
  const [path, setPath] = useState<RoverPath | null>(null);
  const [report, setReport] = useState<RiskReport | null>(null);
  const [rovers, setRovers] = useState<RoverConfig[]>([]);

  const [start, setStart] = useState<Marker>(null);
  const [end, setEnd] = useState<Marker>(null);
  const [pickMode, setPickMode] = useState<"start" | "end" | null>(null);
  const [roverId, setRoverId] = useState("");
  const [busy, setBusy] = useState<Busy>(null);
  const [error, setError] = useState<string | null>(null);

  // Each stage 404s until it has been run; that is expected, not an error.
  const loadAll = useCallback(async () => {
    const [missionData, roverList] = await Promise.all([
      api.getMission(missionId),
      api.listRoverConfigs(),
    ]);
    setMission(missionData);
    setRovers(roverList);
    setRoverId((current) => current || roverList[0]?.id || "");

    const optional = async <T,>(loader: () => Promise<T>): Promise<T | null> => {
      try {
        return await loader();
      } catch (caught) {
        if (caught instanceof ApiError && caught.status === 404) return null;
        throw caught;
      }
    };

    setAnalysis(await optional(() => api.getTerrainAnalysis(missionId)));
    const existingPath = await optional(() => api.getPath(missionId));
    setPath(existingPath);
    if (existingPath) {
      setStart(existingPath.start_point);
      setEnd(existingPath.end_point);
    }
    setReport(await optional(() => api.getRiskReport(missionId)));
  }, [missionId]);

  useEffect(() => {
    loadAll().catch((caught) => setError(String(caught.message ?? caught)));
  }, [loadAll]);

  const run = async (stage: Busy, action: () => Promise<void>) => {
    setBusy(stage);
    setError(null);
    try {
      await action();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(null);
    }
  };

  const handlePick = (point: { x: number; y: number }) => {
    if (pickMode === "start") setStart(point);
    if (pickMode === "end") setEnd(point);
    setPickMode(null);
  };

  if (error && !mission) {
    return <div className="p-6 text-sm text-rose-400">{error}</div>;
  }
  if (!mission) {
    return <div className="p-6 text-sm text-slate-500">Loading mission…</div>;
  }

  const stageIndex = STAGES.indexOf(mission.status);

  return (
    <div className="mx-auto max-w-7xl px-6 py-6">
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link to="/" className="text-xs text-slate-500 hover:text-accent">
            ← all missions
          </Link>
          <h1 className="text-xl font-semibold text-slate-100">{mission.name}</h1>
          <p className="text-xs text-slate-500">
            {mission.terrain_source || "source unspecified"} ·{" "}
            {analysis?.terrain_classification ?? "unclassified"}
          </p>
        </div>
        <div className="flex items-center gap-1.5">
          {STAGES.map((stage, index) => (
            <div
              key={stage}
              title={stage}
              className={`h-1.5 w-10 rounded-full ${
                index <= stageIndex ? "bg-accent" : "bg-edge"
              }`}
            />
          ))}
        </div>
      </div>

      {error && (
        <div className="mb-4 rounded border border-rose-500/40 bg-rose-500/10 px-4 py-2 text-sm text-rose-300">
          {error}
        </div>
      )}

      <div className="grid gap-5 lg:grid-cols-[1fr_380px]">
        <TerrainViewer
          terrainImageUrl={mission.terrain_image_url}
          hazardHeatmapUrl={analysis?.hazard_heatmap_url ?? null}
          slopeMapUrl={analysis?.slope_map_url ?? null}
          obstacles={analysis?.obstacle_contours ?? null}
          path={path}
          start={start}
          end={end}
          onPick={handlePick}
          pickMode={pickMode}
        />

        <div className="space-y-4">
          <div className="panel space-y-3 p-4">
            <h2 className="text-sm font-semibold text-slate-200">1 · Terrain analysis</h2>
            <button
              className="btn-primary w-full"
              disabled={busy !== null}
              onClick={() =>
                run("analyze", async () => {
                  setAnalysis(await api.analyzeTerrain(missionId));
                  setMission(await api.getMission(missionId));
                })
              }
            >
              {busy === "analyze" ? "Analysing…" : analysis ? "Re-run analysis" : "Analyse terrain"}
            </button>

            {analysis?.analysis_metadata && (
              <dl className="grid grid-cols-2 gap-2 text-xs">
                <Metric
                  label="Mean slope"
                  value={`${analysis.analysis_metadata.slope?.mean_deg}°`}
                />
                <Metric label="Max slope" value={`${analysis.analysis_metadata.slope?.max_deg}°`} />
                <Metric label="Obstacles" value={`${analysis.analysis_metadata.obstacle_count}`} />
                <Metric
                  label="Mean hazard"
                  value={`${analysis.analysis_metadata.hazard_calculation_basis?.aggregate?.mean_hazard}`}
                />
                <Metric
                  label="Classified"
                  value={analysis.terrain_classification ?? "-"}
                  wide
                />
                <Metric
                  label="Rule fired"
                  value={analysis.analysis_metadata.classification_evidence?.rule_fired ?? "-"}
                  wide
                />
              </dl>
            )}
          </div>

          <div className="panel space-y-3 p-4">
            <h2 className="text-sm font-semibold text-slate-200">2 · Path planning</h2>

            <div className="grid grid-cols-2 gap-2">
              <button
                className={`btn-ghost ${pickMode === "start" ? "border-accent text-accent" : ""}`}
                onClick={() => setPickMode(pickMode === "start" ? null : "start")}
                disabled={!analysis}
              >
                {start ? `Start ${start.x},${start.y}` : "Set start"}
              </button>
              <button
                className={`btn-ghost ${pickMode === "end" ? "border-accent text-accent" : ""}`}
                onClick={() => setPickMode(pickMode === "end" ? null : "end")}
                disabled={!analysis}
              >
                {end ? `Goal ${end.x},${end.y}` : "Set goal"}
              </button>
            </div>

            <div>
              <label className="label" htmlFor="rover">
                Rover configuration
              </label>
              <select
                id="rover"
                className="field"
                value={roverId}
                onChange={(event) => setRoverId(event.target.value)}
              >
                {rovers.map((rover) => (
                  <option key={rover.id} value={rover.id}>
                    {rover.name} — {rover.battery_capacity_kwh} kWh, ≤
                    {rover.max_traversable_slope_deg}°
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn-primary w-full"
              disabled={!analysis || !start || !end || !roverId || busy !== null}
              onClick={() =>
                run("plan", async () => {
                  setPath(
                    await api.planPath(missionId, {
                      start: start!,
                      end: end!,
                      rover_config_id: roverId,
                    }),
                  );
                  setMission(await api.getMission(missionId));
                })
              }
            >
              {busy === "plan" ? "Planning…" : "Plan path (A*)"}
            </button>

            {path?.planner_metadata && (
              <dl className="grid grid-cols-2 gap-2 text-xs">
                <Metric label="Distance" value={`${path.total_distance_m} m`} />
                <Metric label="Energy" value={`${path.total_energy_cost_kwh?.toFixed(3)} kWh`} />
                <Metric label="Waypoints" value={`${path.waypoints.length}`} />
                <Metric label="Nodes expanded" value={`${path.planner_metadata.nodes_expanded}`} />
                <Metric
                  label="Blocked by slope limit"
                  value={`${path.planner_metadata.moves_blocked_by_slope_limit} moves`}
                  wide
                />
              </dl>
            )}
          </div>

          <div className="panel space-y-3 p-4">
            <h2 className="text-sm font-semibold text-slate-200">3 · Risk assessment</h2>
            <button
              className="btn-primary w-full"
              disabled={!path || busy !== null}
              onClick={() =>
                run("assess", async () => {
                  setReport(await api.assessRisk(missionId));
                  setMission(await api.getMission(missionId));
                })
              }
            >
              {busy === "assess" ? "Assessing…" : "Assess mission risk"}
            </button>
            <p className="text-xs text-slate-500">
              Deterministic: hazard exposure along the planned path plus a battery
              feasibility check. No model is called at this step.
            </p>
          </div>
        </div>
      </div>

      {report && (
        <div className="mt-6">
          <RiskReportView
            report={report}
            generating={busy === "report"}
            onGenerateNarrative={() =>
              run("report", async () => {
                setReport(await api.generateReport(missionId));
                setMission(await api.getMission(missionId));
              })
            }
          />
        </div>
      )}
    </div>
  );
}

function Metric({ label, value, wide }: { label: string; value: string; wide?: boolean }) {
  return (
    <div className={`rounded border border-edge bg-void px-2 py-1.5 ${wide ? "col-span-2" : ""}`}>
      <dt className="text-[10px] uppercase tracking-wider text-slate-500">{label}</dt>
      <dd className="font-mono text-xs text-slate-200">{value}</dd>
    </div>
  );
}
