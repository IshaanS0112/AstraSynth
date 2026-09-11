/**
 * Mission control.
 *
 * The 3-D terrain is the application; the panels are drill-downs from it. That
 * ordering is the whole difference between this page and the V1 dashboard,
 * which showed the same numbers as a list and made the operator reconstruct the
 * geometry in their head.
 *
 * State lives here and flows down. The scene is told what to draw; it never
 * asks. Everything the panels show comes from one of four fetched objects - the
 * terrain grid, a traverse run, a route study, a robustness study - so there is
 * no derived state that can disagree with the server.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type {
  Experiment,
  GridPoint,
  Mission,
  MissionEvent,
  RouteCandidate,
  RoverConfig,
  ScienceTarget,
  TerrainGrid,
  TerrainLayerName,
  TraverseRun,
} from "../api/types";
import { EventDetail, EventLog, LayerControl, PlaybackControls, TelemetryPanel } from "../mission-control/panels";
import { MonteCarloPanel, ParetoChart } from "../mission-control/charts";
import TerrainView3D from "../mission-control/TerrainView3D";
import type { CameraView, MarkerSpec, RouteSpec } from "../three/TerrainScene";

/** Validated for CVD separation and contrast against the panel surface. */
const FLEET_COLORS = [0x0d9488, 0x8b5cf6, 0xd97706, 0xe11d48];
const EXECUTED = 0x5eead4;
const PLANNED = 0x64748b;
const CANDIDATE = 0x8b5cf6;

type PickMode = "start" | "goal" | "target" | null;
type Tab = "control" | "study" | "lab" | "fleet";

export default function MissionControl() {
  const { missionId = "" } = useParams();

  const [mission, setMission] = useState<Mission | null>(null);
  const [grid, setGrid] = useState<TerrainGrid | null>(null);
  const [rovers, setRovers] = useState<RoverConfig[]>([]);
  const [roverId, setRoverId] = useState("");
  const [targets, setTargets] = useState<ScienceTarget[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [layer, setLayer] = useState<TerrainLayerName>("hazard");
  const [showLethal, setShowLethal] = useState(true);
  const [camera, setCamera] = useState<{ view: CameraView; nonce: number } | null>(null);
  const [exaggeration, setExaggeration] = useState(1);

  const [start, setStart] = useState<GridPoint | null>(null);
  const [goal, setGoal] = useState<GridPoint | null>(null);
  const [pickMode, setPickMode] = useState<PickMode>(null);

  const [run, setRun] = useState<TraverseRun | null>(null);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [selectedEvent, setSelectedEvent] = useState<MissionEvent | null>(null);

  const [study, setStudy] = useState<Experiment | null>(null);
  const [candidate, setCandidate] = useState<RouteCandidate | null>(null);
  const [robustness, setRobustness] = useState<Experiment | null>(null);
  const [fleet, setFleet] = useState<Experiment | null>(null);
  const [tour, setTour] = useState<Experiment | null>(null);

  const [tab, setTab] = useState<Tab>("control");
  const [seed, setSeed] = useState(7);
  const [uncertaintyWeight, setUncertaintyWeight] = useState(0);
  const [trials, setTrials] = useState(10);

  // --- loading --------------------------------------------------------------

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [detail, configs] = await Promise.all([
          api.getMission(missionId),
          api.listRoverConfigs(),
        ]);
        if (cancelled) return;
        setMission(detail);
        setRovers(configs);
        setRoverId((current) => current || configs[1]?.id || configs[0]?.id || "");

        const [terrain, science] = await Promise.all([
          api.getTerrainGrid(missionId, 96),
          api.listScienceTargets(missionId),
        ]);
        if (cancelled) return;
        setGrid(terrain);
        setTargets(science);
        // Default endpoints at opposite corners, so the view is useful before
        // anything is clicked.
        const edge = Math.max(2, Math.round(terrain.rows * 0.05));
        setStart({ x: edge * terrain.pixel_scale, y: edge * terrain.pixel_scale });
        setGoal({
          x: Math.round((terrain.cols - 1 - edge) * terrain.pixel_scale),
          y: Math.round((terrain.rows - 1 - edge) * terrain.pixel_scale),
        });
      } catch (exc) {
        if (!cancelled) {
          setError(
            exc instanceof ApiError && exc.status === 409
              ? "This mission has not been analysed yet. Run terrain analysis first."
              : exc instanceof Error
                ? exc.message
                : "Could not load the mission.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [missionId]);

  // --- playback -------------------------------------------------------------

  const total = run ? Math.max(run.executed_path.length - 1, 0) : 0;
  const timer = useRef<number | null>(null);

  useEffect(() => {
    if (!playing || total === 0) return;
    // A fixed 120 ms tick rather than requestAnimationFrame: the simulation
    // clock must not depend on the renderer's frame rate, or the same run plays
    // back at different speeds on different machines.
    timer.current = window.setInterval(() => {
      setIndex((current) => {
        const next = current + speed;
        if (next >= total) {
          setPlaying(false);
          return total;
        }
        return next;
      });
    }, 120);
    return () => {
      if (timer.current) window.clearInterval(timer.current);
    };
  }, [playing, speed, total]);

  const clockSeconds = useMemo(() => {
    if (!run || total === 0) return 0;
    return (Math.min(index, total) / total) * run.elapsed_seconds;
  }, [run, index, total]);

  // --- scene inputs ---------------------------------------------------------

  const routes = useMemo<RouteSpec[]>(() => {
    const specs: RouteSpec[] = [];
    const fleetRoutes = fleet?.status === "SUCCESS" ? (fleet.result.routes ?? {}) : {};
    Object.entries(fleetRoutes as Record<string, GridPoint[]>).forEach(([label, waypoints], i) => {
      specs.push({ id: `fleet-${label}`, waypoints, color: FLEET_COLORS[i % FLEET_COLORS.length] });
    });
    if (tour?.status === "SUCCESS" && tour.result.waypoints) {
      specs.push({ id: "tour", waypoints: tour.result.waypoints as GridPoint[], color: 0xd97706 });
    }
    if (candidate) {
      specs.push({ id: "candidate", waypoints: candidate.waypoints, color: CANDIDATE, emphasis: true });
    }
    if (run) {
      specs.push({ id: "planned", waypoints: run.initial_plan, color: PLANNED, dashed: true });
      specs.push({
        id: "executed",
        waypoints: run.executed_path.slice(0, Math.min(index, total) + 1),
        color: EXECUTED,
        emphasis: true,
      });
    }
    return specs;
  }, [run, index, total, candidate, fleet, tour]);

  const markers = useMemo<MarkerSpec[]>(() => {
    const specs: MarkerSpec[] = [];
    if (start) specs.push({ id: "start", point: start, color: 0x5eead4, shape: "start" });
    if (goal) specs.push({ id: "goal", point: goal, color: 0xf8fafc, shape: "goal" });
    targets.forEach((target) =>
      specs.push({
        id: `target-${target.id}`,
        point: { x: target.x, y: target.y },
        color: 0xd97706,
        shape: "target",
        label: target.label,
      }),
    );
    if (run) {
      const position = run.executed_path[Math.min(index, run.executed_path.length - 1)];
      if (position) specs.push({ id: "rover", point: position, color: 0x5eead4, shape: "rover" });
    }
    return specs;
  }, [start, goal, targets, run, index]);

  // --- actions --------------------------------------------------------------

  const guard = useCallback(
    async (name: string, action: () => Promise<void>) => {
      setBusy(name);
      setError(null);
      try {
        await action();
      } catch (exc) {
        setError(exc instanceof Error ? exc.message : "Request failed.");
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const onPick = useCallback(
    (point: GridPoint) => {
      if (pickMode === "start") setStart(point);
      else if (pickMode === "goal") setGoal(point);
      else if (pickMode === "target") {
        void guard("target", async () => {
          const created = await api.createScienceTarget(missionId, {
            label: `TARGET-${targets.length + 1}`,
            x: point.x,
            y: point.y,
            value: 0.7,
          });
          setTargets((current) => [...current, created]);
        });
      }
      setPickMode(null);
    },
    [pickMode, missionId, targets.length, guard],
  );

  const ready = Boolean(grid && start && goal && roverId);
  const battery = rovers.find((r) => r.id === roverId)?.battery_capacity_kwh ?? null;

  if (error && !grid) {
    return (
      <div className="mx-auto max-w-3xl p-10 text-center">
        <p className="text-sm text-red-300">{error}</p>
        <Link to={`/missions/${missionId}`} className="btn-ghost mt-4 inline-block">
          Back to the mission
        </Link>
      </div>
    );
  }

  return (
    <div className="flex h-[calc(100vh-3.25rem)] flex-col">
      {/* Header strip: mission identity and clock, the way a console names itself. */}
      <div className="flex shrink-0 items-center gap-4 border-b border-edge bg-panel/60 px-4 py-2">
        <div>
          <div className="text-sm font-medium text-slate-100">{mission?.name ?? "Mission"}</div>
          <div className="text-[10px] uppercase tracking-[0.2em] text-slate-500">
            planetary autonomy · mission control
          </div>
        </div>
        <div className="ml-auto flex items-center gap-4 font-mono text-xs">
          <span className="text-slate-500">
            SOL {Math.floor(clockSeconds / 88775)} ·{" "}
            {new Date((clockSeconds % 88775) * 1000).toISOString().slice(11, 19)}
          </span>
          <span
            className={`rounded px-2 py-0.5 text-[10px] ${
              run?.status === "FAILED"
                ? "bg-red-500/15 text-red-300"
                : run
                  ? "bg-emerald-500/15 text-emerald-300"
                  : "bg-edge text-slate-400"
            }`}
          >
            {run ? (run.status === "FAILED" ? run.failure_mode : "NOMINAL") : "STANDBY"}
          </span>
          <Link to={`/missions/${missionId}`} className="text-slate-500 hover:text-accent">
            classic view
          </Link>
        </div>
      </div>

      <div className="flex min-h-0 flex-1">
        {/* Left rail: what to do and what to show. */}
        <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-r border-edge p-3">
          <div className="flex gap-1">
            {(["control", "study", "lab", "fleet"] as Tab[]).map((entry) => (
              <button
                key={entry}
                onClick={() => setTab(entry)}
                className={`flex-1 rounded px-2 py-1 text-[11px] capitalize transition ${
                  tab === entry ? "bg-edge text-slate-100" : "text-slate-500 hover:text-slate-300"
                }`}
              >
                {entry}
              </button>
            ))}
          </div>

          <section className="panel p-3">
            <h2 className="label">Map layers</h2>
            <LayerControl
              layer={layer}
              onLayer={setLayer}
              showLethal={showLethal}
              onShowLethal={setShowLethal}
              grid={grid}
              exaggeration={exaggeration}
              onExaggeration={setExaggeration}
            />
            <div className="mt-3 flex gap-1">
              {(["three-quarter", "top", "orbit"] as CameraView[]).map((view) => (
                <button
                  key={view}
                  aria-label={`Camera: ${view === "three-quarter" ? "three-quarter" : view} view`}
                  onClick={() => setCamera({ view, nonce: Date.now() })}
                  className="flex-1 rounded border border-edge px-1.5 py-1 text-[10px] text-slate-400 hover:border-accent hover:text-accent"
                >
                  {view === "three-quarter" ? "3/4" : view}
                </button>
              ))}
            </div>
          </section>

          <section className="panel space-y-2 p-3">
            <h2 className="label">Rover</h2>
            <select
              className="field"
              value={roverId}
              onChange={(event) => setRoverId(event.target.value)}
            >
              {rovers.map((rover) => (
                <option key={rover.id} value={rover.id}>
                  {rover.name} · {rover.battery_capacity_kwh} kWh · {rover.max_traversable_slope_deg}°
                </option>
              ))}
            </select>

            <h2 className="label pt-1">Endpoints</h2>
            {(["start", "goal"] as const).map((which) => {
              const point = which === "start" ? start : goal;
              return (
                <button
                  key={which}
                  onClick={() => setPickMode(pickMode === which ? null : which)}
                  className={`flex w-full items-center justify-between rounded border px-2 py-1.5 text-[11px] transition ${
                    pickMode === which
                      ? "border-accent text-accent"
                      : "border-edge text-slate-400 hover:border-accent"
                  }`}
                >
                  <span className="capitalize">{which}</span>
                  <span className="font-mono text-[10px] text-slate-500">
                    {point ? `${point.x}, ${point.y}` : "click to set"}
                  </span>
                </button>
              );
            })}
          </section>

          {tab === "control" && (
            <section className="panel space-y-2 p-3">
              <h2 className="label">Traverse</h2>
              <label className="block text-[11px] text-slate-400">
                Seed
                <input
                  type="number"
                  className="field mt-1"
                  value={seed}
                  min={0}
                  onChange={(event) => setSeed(Number(event.target.value))}
                />
              </label>
              <label className="block text-[11px] text-slate-400">
                Uncertainty weight · {uncertaintyWeight.toFixed(1)}
                <input
                  type="range"
                  min={0}
                  max={4}
                  step={0.5}
                  value={uncertaintyWeight}
                  onChange={(event) => setUncertaintyWeight(Number(event.target.value))}
                  className="mt-1 w-full accent-teal-400"
                />
              </label>
              <p className="text-[10px] leading-snug text-slate-600">
                0 searches the hazard mean and ignores how well it is known — the V1 behaviour.
                Above 0, the planner is charged for uncertainty and prefers surveyed ground.
              </p>
              <button
                className="btn-primary w-full"
                disabled={!ready || busy !== null}
                onClick={() =>
                  guard("traverse", async () => {
                    const result = await api.simulateTraverse(missionId, {
                      start: start!,
                      goal: goal!,
                      rover_config_id: roverId,
                      seed,
                      uncertainty_weight: uncertaintyWeight,
                    });
                    setRun(result);
                    setIndex(0);
                    setSelectedEvent(null);
                    setPlaying(true);
                  })
                }
              >
                {busy === "traverse" ? "Driving…" : "Simulate traverse"}
              </button>
              <p className="text-[10px] leading-snug text-slate-600">
                Ground truth is synthesised from the seed: the orbital map plus hazards it never
                resolved. A real mission has no truth array.
              </p>
            </section>
          )}

          {tab === "study" && (
            <section className="panel space-y-2 p-3">
              <h2 className="label">Route study</h2>
              <button
                className="btn-primary w-full"
                disabled={!ready || busy !== null}
                onClick={() =>
                  guard("study", async () => {
                    setStudy(
                      await api.routeStudy(missionId, {
                        start: start!,
                        goal: goal!,
                        rover_config_id: roverId,
                      }),
                    );
                    setCandidate(null);
                  })
                }
              >
                {busy === "study" ? "Sweeping…" : "Sweep objective weights"}
              </button>
              <p className="text-[10px] leading-snug text-slate-600">
                Plans at nine weightings, measures every route on the unweighted model, and keeps
                the ones nothing beats outright.
              </p>
            </section>
          )}

          {tab === "lab" && (
            <section className="panel space-y-2 p-3">
              <h2 className="label">Robustness</h2>
              <label className="block text-[11px] text-slate-400">
                Trials · {trials}
                <input
                  type="range"
                  min={4}
                  max={60}
                  step={1}
                  value={trials}
                  onChange={(event) => setTrials(Number(event.target.value))}
                  className="mt-1 w-full accent-teal-400"
                />
              </label>
              <p className="text-[10px] text-slate-500">
                Each trial drives the whole mission, so this is roughly{" "}
                <span className="font-mono text-slate-300">{Math.ceil(trials * 1.2)}s</span> of
                work on this grid.
              </p>
              <button
                className="btn-primary w-full"
                disabled={!ready || busy !== null}
                onClick={() =>
                  guard("mc", async () =>
                    setRobustness(
                      await api.monteCarlo(missionId, {
                        start: start!,
                        goal: goal!,
                        rover_config_id: roverId,
                        trials,
                        seed,
                        uncertainty_weight: uncertaintyWeight,
                      }),
                    ),
                  )
                }
              >
                {busy === "mc" ? `Running ${trials} trials…` : "Run study"}
              </button>
              <p className="text-[10px] leading-snug text-slate-600">
                Runs synchronously on the request thread, which is why the count is capped
                here. A thousand-trial study belongs behind a job queue. Same seed, same
                numbers.
              </p>
            </section>
          )}

          {tab === "fleet" && (
            <section className="panel space-y-2 p-3">
              <h2 className="label">Fleet &amp; science</h2>
              <button
                className="btn-ghost w-full"
                onClick={() => setPickMode(pickMode === "target" ? null : "target")}
              >
                {pickMode === "target" ? "Click the terrain…" : "Add science target"}
              </button>
              <ul className="max-h-32 space-y-1 overflow-y-auto">
                {targets.map((target) => (
                  <li key={target.id} className="flex items-center gap-2 text-[11px]">
                    <span className="flex-1 truncate text-slate-300">{target.label}</span>
                    <span className="font-mono text-[10px] text-slate-500">
                      {target.value.toFixed(2)}
                    </span>
                    <button
                      className="text-slate-600 hover:text-red-300"
                      onClick={() =>
                        guard("del", async () => {
                          await api.deleteScienceTarget(missionId, target.id);
                          setTargets((current) => current.filter((t) => t.id !== target.id));
                        })
                      }
                    >
                      ×
                    </button>
                  </li>
                ))}
                {targets.length === 0 && (
                  <li className="text-[11px] text-slate-600">No targets yet.</li>
                )}
              </ul>
              <button
                className="btn-primary w-full"
                disabled={!ready || targets.length === 0 || busy !== null}
                onClick={() =>
                  guard("tour", async () =>
                    setTour(
                      await api.scienceTour(missionId, {
                        start: start!,
                        rover_config_id: roverId,
                        instruments: ["camera", "spectrometer"],
                      }),
                    ),
                  )
                }
              >
                {busy === "tour" ? "Planning…" : "Plan science tour"}
              </button>
              <button
                className="btn-ghost w-full"
                disabled={!ready || rovers.length < 2 || busy !== null}
                onClick={() =>
                  guard("fleet", async () =>
                    setFleet(
                      await api.fleetPlan(missionId, {
                        assignments: rovers.slice(0, 3).map((rover, i) => ({
                          label: rover.name.split(/[ -]/)[0].toUpperCase(),
                          rover_config_id: rover.id,
                          start: {
                            x: start!.x + i * (grid?.pixel_scale ?? 1) * 3,
                            y: start!.y,
                          },
                          goal: {
                            x: goal!.x,
                            y: goal!.y - i * (grid?.pixel_scale ?? 1) * 3,
                          },
                        })),
                        time_budget_seconds: 25,
                      }),
                    ),
                  )
                }
              >
                {busy === "fleet" ? "Deconflicting…" : "Deconflict 3 rovers"}
              </button>
              <FleetSummary experiment={fleet} tour={tour} />
            </section>
          )}

          {error && <p className="panel p-2 text-[11px] text-red-300">{error}</p>}
        </aside>

        {/* Centre: the terrain, dominant. */}
        <main className="flex min-w-0 flex-1 flex-col">
          <div className="relative min-h-0 flex-1">
            <TerrainView3D
              grid={grid}
              layer={layer}
              showLethal={showLethal}
              routes={routes}
              markers={markers}
              cameraRequest={camera}
              exaggeration={exaggeration}
              onPick={onPick}
              picking={pickMode !== null}
            />
            {pickMode && (
              <div className="pointer-events-none absolute left-1/2 top-4 -translate-x-1/2 rounded bg-panel/90 px-3 py-1.5 text-[11px] text-accent">
                Click the terrain to place the {pickMode}
              </div>
            )}
            {run && (
              <div className="pointer-events-none absolute bottom-3 left-3 flex gap-3 rounded bg-panel/80 px-3 py-1.5 text-[10px]">
                <LegendSwatch color="#64748b" label="planned" dashed />
                <LegendSwatch color="#5eead4" label="executed" />
                {candidate && <LegendSwatch color="#8b5cf6" label="study route" />}
              </div>
            )}
          </div>

          <div className="shrink-0 border-t border-edge px-4 py-2">
            <PlaybackControls
              index={index}
              total={total}
              playing={playing}
              speed={speed}
              onIndex={setIndex}
              onPlaying={setPlaying}
              onSpeed={setSpeed}
            />
          </div>

          <div className="h-60 shrink-0 border-t border-edge">
            {tab === "study" ? (
              <div className="h-full overflow-y-auto p-3">
                <ParetoChart experiment={study} selected={candidate?.label ?? null} onSelect={setCandidate} />
              </div>
            ) : tab === "lab" ? (
              <div className="h-full overflow-y-auto p-3">
                <MonteCarloPanel experiment={robustness} />
              </div>
            ) : (
              <EventLog
                events={run?.events ?? []}
                clockSeconds={clockSeconds}
                onSelect={setSelectedEvent}
              />
            )}
          </div>
        </main>

        {/* Right rail: what the rover is doing, and why. */}
        <aside className="flex w-72 shrink-0 flex-col gap-3 overflow-y-auto border-l border-edge p-3">
          <section className="panel p-3">
            <h2 className="label">Telemetry</h2>
            <TelemetryPanel run={run} index={index} batteryCapacity={battery} />
          </section>
          <section className="panel p-3">
            <h2 className="label">Event detail</h2>
            <EventDetail event={selectedEvent} />
          </section>
        </aside>
      </div>
    </div>
  );
}

function LegendSwatch({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-slate-400">
      <svg width="16" height="6">
        <line
          x1="0"
          y1="3"
          x2="16"
          y2="3"
          stroke={color}
          strokeWidth="2"
          strokeDasharray={dashed ? "3 2" : undefined}
        />
      </svg>
      {label}
    </span>
  );
}

function FleetSummary({ experiment, tour }: { experiment: Experiment | null; tour: Experiment | null }) {
  return (
    <div className="space-y-2 border-t border-edge pt-2 text-[11px]">
      {experiment && (
        experiment.status === "SUCCESS" ? (
          <p className="text-slate-400">
            Deconflicted in{" "}
            <span className="font-mono text-slate-200">{experiment.result.high_level_nodes}</span>{" "}
            constraint-tree nodes.{" "}
            {experiment.result.verified_conflict_free ? (
              <span className="text-emerald-300">Verified conflict-free.</span>
            ) : (
              <span className="text-red-300">Residual conflicts remain.</span>
            )}
          </p>
        ) : (
          <p className="text-amber-300">
            {experiment.failure_mode}: the fleet was not deconflicted, so no routes are shown.
          </p>
        )
      )}
      {tour && tour.status === "SUCCESS" && (
        <p className="text-slate-400">
          Tour visits{" "}
          <span className="font-mono text-slate-200">{tour.result.targets_visited}</span> of{" "}
          <span className="font-mono text-slate-200">{tour.result.targets_offered}</span> targets,{" "}
          <span className="font-mono text-slate-200">
            {(tour.result.energy_kwh as number)?.toFixed(3)}
          </span>{" "}
          kWh.
          {(tour.result.skipped as { target_id: string; reason: string }[])?.length > 0 && (
            <span className="mt-1 block text-slate-600">
              skipped:{" "}
              {(tour.result.skipped as { target_id: string; reason: string }[])
                .map((s) => `${s.target_id} (${s.reason})`)
                .join(", ")}
            </span>
          )}
        </p>
      )}
      {tour && tour.status !== "SUCCESS" && (
        <p className="text-amber-300">{tour.failure_mode}: {tour.reason}</p>
      )}
    </div>
  );
}
