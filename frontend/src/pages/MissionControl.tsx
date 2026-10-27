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
  TraverseRun,
} from "../api/types";
import {
  EventDetail,
  EventLog,
  ExecutivePanel,
  LayerControl,
  PlaybackControls,
  TelemetryPanel,
} from "../mission-control/panels";
import { MonteCarloPanel, ParetoChart } from "../mission-control/charts";
import TerrainView3D from "../mission-control/TerrainView3D";
import { css, hex } from "../three/palette";
import type { CameraView, MarkerSpec, Quality, RouteSpec, RoverPose, SceneLayer } from "../three/TerrainScene";

// Graphite: the accent goes to the one route that matters - where the rover
// actually drove. Everything else takes the chart series after it, in order.
const FLEET_COLORS = [hex("chart-2"), hex("chart-3"), hex("chart-4"), hex("chart-5")];
const EXECUTED = hex("accent");
const PLANNED = hex("muted");
const CANDIDATE = hex("chart-2");

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

  // The photographic surface first: it is what the ground looks like. The data
  // layers are one click away and say what it is.
  const [layer, setLayer] = useState<SceneLayer>("surface");
  const [quality, setQuality] = useState<Quality | "auto">(initialQuality);
  const [showLethal, setShowLethal] = useState(true);
  const [camera, setCamera] = useState<{ view: CameraView; nonce: number } | null>(null);
  // 2x by default and always labelled as such: 40 m of relief across a
  // kilometre is nearly flat at true scale. Colours stay true at any setting.
  const [exaggeration, setExaggeration] = useState(2);
  const [contours, setContours] = useState(true);

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
        // The mid-size rover by battery: the light one is sized to run flat on
        // a long traverse, which is worth showing but a poor first impression.
        const bySize = [...configs].sort((a, b) => a.battery_capacity_kwh - b.battery_capacity_kwh);
        const preferred = bySize[Math.floor((bySize.length - 1) / 2)];
        setRoverId((current) => current || preferred?.id || "");
        const science = await api.listScienceTargets(missionId);
        if (cancelled) return;
        setTargets(science);
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

  // The grid is fetched per rover: its footprint decides the clearance layer.
  useEffect(() => {
    if (!roverId) return;
    let cancelled = false;
    api
      .getTerrainGrid(missionId, 128, roverId)
      .then((terrain) => {
        if (cancelled) return;
        setGrid(terrain);
        // Default endpoints at opposite corners, once, so the view is useful
        // before anything is clicked. Whole pixels: the API takes integers.
        const edge = Math.max(2, Math.round(terrain.rows * 0.05));
        const px = (cells: number) => Math.round(cells * terrain.pixel_scale);
        setStart((current) => current ?? { x: px(edge), y: px(edge) });
        setGoal((current) => current ?? { x: px(terrain.cols - 1 - edge), y: px(terrain.rows - 1 - edge) });
        setLayer((current) => (current === "clearance_cost" && !terrain.layers.clearance_cost ? "hazard" : current));
      })
      .catch((exc) => {
        if (cancelled) return;
        setError(
          exc instanceof ApiError && exc.status === 409
            ? "This mission has not been analysed yet. Run terrain analysis first."
            : exc instanceof Error
              ? exc.message
              : "Could not load the terrain.",
        );
      });
    return () => {
      cancelled = true;
    };
  }, [missionId, roverId]);

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
      specs.push({ id: "tour", waypoints: tour.result.waypoints as GridPoint[], color: hex("chart-3") });
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
    if (start) specs.push({ id: "start", point: start, color: hex("fg"), shape: "start", label: "START" });
    if (goal) specs.push({ id: "goal", point: goal, color: hex("fg"), shape: "goal", label: "GOAL" });
    targets.forEach((target) =>
      specs.push({
        id: `target-${target.id}`,
        point: { x: target.x, y: target.y },
        color: hex("chart-3"),
        shape: "target",
        label: target.label,
      }),
    );
    return specs;
  }, [start, goal, targets]);

  const selectedRover = rovers.find((r) => r.id === roverId) ?? null;

  // The rover is on the map while planning too, parked at the start and facing
  // the goal: what is being planned for should be visible before it moves.
  const roverPose = useMemo<RoverPose | null>(() => {
    if (run && run.executed_path.length > 0) {
      const i = Math.min(index, run.executed_path.length - 1);
      const path = run.executed_path;
      return {
        point: path[i],
        from: i > 0 ? path[i - 1] : path.length > 1 ? behind(path[0], path[1]) : null,
        sensorRangeM: (run.parameters?.sensor?.range_m as number | undefined) ?? 0,
      };
    }
    if (start && goal) return { point: start, from: behind(start, goal) };
    return null;
  }, [run, index, start, goal]);

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
  const battery = selectedRover?.battery_capacity_kwh ?? null;

  if (error && !grid) {
    return (
      <div className="mx-auto max-w-3xl p-10 text-center">
        <p className="text-sm text-danger">{error}</p>
        <Link to={`/missions/${missionId}`} className="btn-ghost mt-4 inline-block">
          Back to the mission
        </Link>
      </div>
    );
  }

  return (
    // One column on a phone - terrain first - and three panes from lg up.
    <div className="flex flex-col lg:h-full">
      {/* Header strip: mission identity and clock, the way a console names itself. */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-b border-edge bg-panel/60 px-4 py-2">
        <div>
          <div className="text-sm font-medium text-fg">{mission?.name ?? "Mission"}</div>
          <div className="text-2xs uppercase tracking-wider text-muted">mission control</div>
        </div>
        <div className="ml-auto flex items-center gap-4 whitespace-nowrap font-mono text-xs">
          <span className="text-muted">
            SOL {Math.floor(clockSeconds / 88775)} ·{" "}
            {new Date((clockSeconds % 88775) * 1000).toISOString().slice(11, 19)}
          </span>
          <span
            className={`rounded px-2 py-0.5 text-2xs ${
              run?.status === "FAILED"
                ? "bg-danger/15 text-danger"
                : run
                  ? "bg-success/15 text-success"
                  : "bg-edge text-muted"
            }`}
          >
            {run ? (run.status === "FAILED" ? run.failure_mode : "NOMINAL") : "STANDBY"}
          </span>
          <Link to={`/missions/${missionId}`} className="text-muted hover:text-accent">
            Mission page
          </Link>
        </div>
      </div>

      <div className="flex flex-col lg:min-h-0 lg:flex-1 lg:flex-row">
        {/* Left rail: what to do and what to show. */}
        <aside className="flex w-full shrink-0 flex-col gap-3 border-t border-edge p-3 lg:w-72 lg:overflow-y-auto lg:border-r lg:border-t-0">
          <div className="flex gap-1">
            {(["control", "study", "lab", "fleet"] as Tab[]).map((entry) => (
              <button
                key={entry}
                onClick={() => setTab(entry)}
                className={`h-10 flex-1 rounded px-2 py-1 text-xs capitalize transition lg:h-auto ${
                  tab === entry ? "bg-edge text-fg" : "text-muted hover:text-fg"
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
            <div className="mt-3 grid grid-cols-4 gap-1">
              {(["three-quarter", "top", "orbit", "follow"] as CameraView[]).map((view) => (
                <button
                  key={view}
                  aria-label={`Camera: ${view === "three-quarter" ? "three-quarter" : view} view`}
                  onClick={() => setCamera({ view, nonce: Date.now() })}
                  className="h-10 rounded-md border border-line bg-surface-2 text-2xs text-muted transition-colors duration-120 hover:border-line-strong hover:text-fg lg:h-8"
                >
                  {view === "three-quarter" ? "3/4" : view}
                </button>
              ))}
            </div>
            <label className="mt-2 flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={contours}
                onChange={(event) => setContours(event.target.checked)}
                className="accent-accent"
              />
              Contour lines
            </label>
            <label className="mt-2 flex items-center justify-between gap-2 text-xs text-muted">
              Render quality
              <select
                className="field h-10 w-36 text-xs lg:h-8"
                value={quality}
                onChange={(event) => {
                  const next = event.target.value as Quality | "auto";
                  setQuality(next);
                  try {
                    window.localStorage.setItem(QUALITY_KEY, next);
                  } catch {
                    /* private mode: the choice just does not persist */
                  }
                }}
              >
                <option value="auto">Auto</option>
                <option value="cinematic">Cinematic</option>
                <option value="balanced">Balanced</option>
                <option value="performance">Performance</option>
              </select>
            </label>
          </section>

          <section className="panel space-y-2 p-3">
            <h2 className="label">Rover</h2>
            <select
              className="field"
              aria-label="Rover configuration"
              value={roverId}
              onChange={(event) => setRoverId(event.target.value)}
            >
              {rovers.map((rover) => (
                <option key={rover.id} value={rover.id}>
                  {rover.name}
                </option>
              ))}
            </select>
            {selectedRover && (
              <dl className="grid grid-cols-2 gap-x-3 gap-y-1 text-2xs">
                <dt className="text-muted">Battery</dt>
                <dd className="num text-right text-fg">{selectedRover.battery_capacity_kwh} kWh</dd>
                <dt className="text-muted">Max slope</dt>
                <dd className="num text-right text-fg">{selectedRover.max_traversable_slope_deg}°</dd>
                <dt className="text-muted">Footprint radius</dt>
                <dd className="num text-right text-fg">{selectedRover.footprint_radius_m} m</dd>
                <dt className="text-muted">Clearance band</dt>
                <dd className="num text-right text-fg">{selectedRover.clearance_radius_m} m</dd>
              </dl>
            )}

            <h2 className="label pt-1">Endpoints</h2>
            {(["start", "goal"] as const).map((which) => {
              const point = which === "start" ? start : goal;
              return (
                <button
                  key={which}
                  onClick={() => setPickMode(pickMode === which ? null : which)}
                  className={`flex h-10 w-full items-center justify-between rounded border px-2 py-1.5 text-xs transition lg:h-auto ${
                    pickMode === which
                      ? "border-accent text-accent"
                      : "border-edge text-muted hover:border-accent"
                  }`}
                >
                  <span className="capitalize">{which}</span>
                  <span className="font-mono text-2xs text-muted">
                    {point ? `${point.x}, ${point.y}` : "click to set"}
                  </span>
                </button>
              );
            })}
          </section>

          {tab === "control" && (
            <section className="panel space-y-2 p-3">
              <h2 className="label">Traverse</h2>
              <label className="block text-xs text-muted">
                Seed
                <input
                  type="number"
                  className="field mt-1"
                  value={seed}
                  min={0}
                  onChange={(event) => setSeed(Number(event.target.value))}
                />
              </label>
              <label className="block text-xs text-muted">
                Uncertainty weight · {uncertaintyWeight.toFixed(1)}
                <input
                  type="range"
                  min={0}
                  max={4}
                  step={0.5}
                  value={uncertaintyWeight}
                  onChange={(event) => setUncertaintyWeight(Number(event.target.value))}
                  className="mt-1 w-full accent-accent"
                />
              </label>
              <p className="text-2xs leading-snug text-muted">
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
              <p className="text-2xs leading-snug text-muted">
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
              <p className="text-2xs leading-snug text-muted">
                Plans at nine weightings, measures every route on the unweighted model, and keeps
                the ones nothing beats outright.
              </p>
            </section>
          )}

          {tab === "lab" && (
            <section className="panel space-y-2 p-3">
              <h2 className="label">Robustness</h2>
              <label className="block text-xs text-muted">
                Trials · {trials}
                <input
                  type="range"
                  min={4}
                  max={60}
                  step={1}
                  value={trials}
                  onChange={(event) => setTrials(Number(event.target.value))}
                  className="mt-1 w-full accent-accent"
                />
              </label>
              <p className="text-2xs text-muted">
                Each trial drives the whole mission, so this is roughly{" "}
                <span className="font-mono text-fg">{Math.ceil(trials * 1.2)}s</span> of
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
              <p className="text-2xs leading-snug text-muted">
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
                  <li key={target.id} className="flex items-center gap-2 text-xs">
                    <span className="flex-1 truncate text-fg">{target.label}</span>
                    <span className="font-mono text-2xs text-muted">
                      {target.value.toFixed(2)}
                    </span>
                    <button
                      className="text-subtle hover:text-danger"
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
                  <li className="text-xs text-muted">No targets yet.</li>
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
                            x: start!.x + Math.round(i * (grid?.pixel_scale ?? 1) * 3),
                            y: start!.y,
                          },
                          goal: {
                            x: goal!.x,
                            y: goal!.y - Math.round(i * (grid?.pixel_scale ?? 1) * 3),
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

          {error && <p className="panel p-2 text-xs text-danger">{error}</p>}
        </aside>

        {/* Centre: the terrain, dominant. */}
        <main className="order-first flex min-w-0 flex-col lg:order-none lg:flex-1">
          <div className="relative h-96 shrink-0 lg:h-auto lg:min-h-0 lg:flex-1 lg:shrink">
            <TerrainView3D
              grid={grid}
              layer={layer}
              showLethal={showLethal}
              routes={routes}
              markers={markers}
              rover={roverPose}
              contours={contours}
              cameraRequest={camera}
              exaggeration={exaggeration}
              quality={quality}
              onPick={onPick}
              picking={pickMode !== null}
            />
            {pickMode && (
              <div className="pointer-events-none absolute left-1/2 top-4 -translate-x-1/2 rounded bg-panel/90 px-3 py-1.5 text-xs text-accent">
                Click the terrain to place the {pickMode}
              </div>
            )}
            {(run || candidate) && (
              <div className="pointer-events-none absolute bottom-3 left-3 flex flex-wrap gap-3 rounded-md border border-line bg-surface/90 px-3 py-1.5 text-2xs">
                {run && <LegendSwatch color={css("muted")} label="planned" dashed />}
                {run && <LegendSwatch color={css("accent")} label="driven" />}
                {candidate && <LegendSwatch color={css("chart-2")} label="study route" />}
                {run && <LegendSwatch color={css("info")} label="sensor range" />}
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
        <aside className="flex w-full shrink-0 flex-col gap-3 border-t border-edge p-3 lg:w-72 lg:overflow-y-auto lg:border-l lg:border-t-0">
          <section className="panel p-3">
            <h2 className="label">Telemetry</h2>
            <TelemetryPanel run={run} index={index} batteryCapacity={battery} />
          </section>
          <section className="panel p-3">
            <h2 className="label-tick mb-2">Executive</h2>
            <ExecutivePanel run={run} />
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

const QUALITY_KEY = "astrasynth.render-quality";
const QUALITIES = ["auto", "cinematic", "balanced", "performance"] as const;

/** ``?quality=`` wins (for screenshots and demos), then the viewer's last choice, then auto. */
function initialQuality(): Quality | "auto" {
  const fromUrl = new URLSearchParams(window.location.search).get("quality");
  let stored: string | null = null;
  try {
    stored = window.localStorage.getItem(QUALITY_KEY);
  } catch {
    stored = null;
  }
  const pick = (QUALITIES as readonly string[]).find((q) => q === fromUrl) ?? (QUALITIES as readonly string[]).find((q) => q === stored);
  return (pick as Quality | "auto" | undefined) ?? "auto";
}

/** A point behind ``at``, so that a heading from it to ``at`` faces ``towards``. */
function behind(at: GridPoint, towards: GridPoint): GridPoint {
  return { x: 2 * at.x - towards.x, y: 2 * at.y - towards.y };
}

function LegendSwatch({ color, label, dashed }: { color: string; label: string; dashed?: boolean }) {
  return (
    <span className="flex items-center gap-1.5 text-muted">
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
    <div className="space-y-2 border-t border-edge pt-2 text-xs">
      {experiment && (
        experiment.status === "SUCCESS" ? (
          <p className="text-muted">
            Deconflicted in{" "}
            <span className="font-mono text-fg">{experiment.result.high_level_nodes}</span>{" "}
            constraint-tree {experiment.result.high_level_nodes === 1 ? "node" : "nodes"}.{" "}
            {experiment.result.verified_conflict_free ? (
              <span className="text-success">Verified conflict-free.</span>
            ) : (
              <span className="text-danger">Residual conflicts remain.</span>
            )}
          </p>
        ) : (
          <p className="text-warning">
            {experiment.failure_mode}: the fleet was not deconflicted, so no routes are shown.
          </p>
        )
      )}
      {tour && tour.status === "SUCCESS" && (
        <p className="text-muted">
          Tour visits{" "}
          <span className="font-mono text-fg">{tour.result.targets_visited}</span> of{" "}
          <span className="font-mono text-fg">{tour.result.targets_offered}</span> targets,{" "}
          <span className="font-mono text-fg">
            {(tour.result.energy_kwh as number)?.toFixed(3)}
          </span>{" "}
          kWh.
          {(tour.result.skipped as { target_id: string; reason: string }[])?.length > 0 && (
            <span className="mt-1 block text-muted">
              skipped:{" "}
              {(tour.result.skipped as { target_id: string; reason: string }[])
                .map((s) => `${s.target_id} (${s.reason})`)
                .join(", ")}
            </span>
          )}
        </p>
      )}
      {tour && tour.status !== "SUCCESS" && (
        <p className="text-warning">{tour.failure_mode}: {tour.reason}</p>
      )}
    </div>
  );
}
