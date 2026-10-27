/**
 * The console panels around the 3-D view.
 *
 * Grouped in one module because they are one thing - the chrome of a mission
 * console - and each is small enough that a file apiece would be more imports
 * than code. Anything that grows its own state or fetches gets its own file.
 */

import { useMemo, useState } from "react";

import type {
  MissionEvent,
  TerrainGrid,
  TerrainLayerName,
  TraverseRun,
  TreeNode,
} from "../api/types";
import { layerRamps, rampGradient } from "../three/palette";
import type { SceneLayer } from "../three/TerrainScene";

const LAYERS: { id: SceneLayer; label: string; hint: string }[] = [
  {
    id: "surface",
    label: "Surface",
    hint: "Blender-baked regolith under the Mars sky it was rendered with: a visual, not a data layer",
  },
  { id: "hazard", label: "Hazard", hint: "weighted slope, obstacle proximity and roughness" },
  { id: "uncertainty", label: "Uncertainty", hint: "1σ of the hazard estimate, propagated" },
  { id: "slope_deg", label: "Slope", hint: "degrees, from the rendered surface" },
  { id: "elevation_m", label: "Elevation", hint: "metres above the tile minimum" },
  {
    id: "clearance_cost",
    label: "Clearance",
    hint: "the selected rover's keep-out band: Nav2-style inflation around every obstacle",
  },
];

export function LayerControl({
  layer,
  onLayer,
  showLethal,
  onShowLethal,
  grid,
  exaggeration,
  onExaggeration,
}: {
  layer: SceneLayer;
  onLayer: (layer: SceneLayer) => void;
  showLethal: boolean;
  onShowLethal: (value: boolean) => void;
  grid: TerrainGrid | null;
  exaggeration: number;
  onExaggeration: (value: number) => void;
}) {
  const range = layer === "surface" ? undefined : grid?.ranges[layer as TerrainLayerName];
  const available = LAYERS.filter((entry) => entry.id !== "clearance_cost" || grid?.layers.clearance_cost);
  const ramps = useMemo(() => layerRamps(), []);
  const active = LAYERS.find((entry) => entry.id === layer);
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {available.map((entry) => (
          <button
            key={entry.id}
            title={entry.hint}
            aria-pressed={layer === entry.id}
            onClick={() => onLayer(entry.id)}
            className={`h-10 rounded-md border px-2.5 text-xs transition-colors duration-120 lg:h-7 lg:px-2 ${
              layer === entry.id
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-line bg-surface-2 text-muted hover:border-line-strong hover:text-fg"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </div>
      {range && (
        <div className="space-y-1">
          <div className="flex items-center gap-2 text-2xs text-muted">
            <span className="num">{range[0].toFixed(2)}</span>
            {/* The same ramp the terrain is drawn with, generated, never retyped. */}
            <span className="h-1.5 flex-1 rounded-full" style={{ background: rampGradient(ramps[layer as TerrainLayerName] ?? ramps.hazard) }} /> {/* lint-ok: a data ramp generated from tokens, matching the terrain */}
            <span className="num">{range[1].toFixed(2)}</span>
          </div>
          {active && <p className="text-2xs leading-snug text-muted">{active.hint}</p>}
          {layer === "clearance_cost" && range[1] === 0 && (
            <p className="text-2xs leading-snug text-warning">
              Nothing on this map reaches the lethal threshold ({grid?.lethal_hazard_threshold}), so
              there is no obstacle to keep clear of and the band is empty.
            </p>
          )}
        </div>
      )}
      {layer === "surface" && (
        <div className="space-y-1">
          <p className="flex items-center gap-1.5 text-2xs">
            <span className="rounded border border-line px-1 font-mono uppercase tracking-wider text-muted">visual</span>
            <span className="text-muted">not a data layer</span>
          </p>
          <p className="text-2xs leading-snug text-muted">{LAYERS[0].hint}. Switch to a data layer to read the terrain.</p>
        </div>
      )}
      <label className="flex items-center gap-2 text-xs text-muted">
        <input
          type="checkbox"
          checked={showLethal}
          onChange={(event) => onShowLethal(event.target.checked)}
          className="accent-danger"
        />
        Mark untraversable ground on every layer
      </label>
      <div className="flex items-center gap-2 text-xs text-muted">
        <span className="shrink-0">Relief</span>
        {[1, 2, 4].map((value) => (
          <button
            key={value}
            // Distinct accessible name: the playback control has a "4x" button
            // too, and two buttons that sound identical but do different things
            // are indistinguishable to anyone not looking at the layout.
            aria-label={`Vertical relief ${value}x`}
            aria-pressed={exaggeration === value}
            onClick={() => onExaggeration(value)}
            className={`h-10 min-w-10 rounded-md px-2 font-mono text-2xs transition-colors duration-120 lg:h-6 lg:min-w-0 ${
              exaggeration === value ? "bg-surface-2 text-fg ring-1 ring-line-strong" : "text-muted hover:text-fg"
            }`}
          >
            {value}×
          </button>
        ))}
        {exaggeration !== 1 && <span className="text-2xs text-warning">exaggerated</span>}
      </div>
    </div>
  );
}

function Stat({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1">
      <span className="text-2xs uppercase tracking-wider text-muted">{label}</span>
      <span className={`font-mono text-xs tabular-nums ${tone || "text-fg"}`}>{value}</span>
    </div>
  );
}

export function TelemetryPanel({
  run,
  index,
  batteryCapacity,
}: {
  run: TraverseRun | null;
  index: number;
  batteryCapacity: number | null;
}) {
  if (!run) {
    return <p className="text-xs text-muted">Run a traverse to see telemetry.</p>;
  }

  const steps = Math.max(run.executed_path.length - 1, 1);
  const progress = Math.min(index, steps) / steps;
  // Linear interpolation along the run. The simulator charges energy per step
  // against true terrain; scrubbing a completed run does not re-derive that, so
  // the readout is explicitly "energy spent by this point in the replay".
  const energy = run.energy_kwh * progress;
  const remaining = batteryCapacity ? batteryCapacity - energy : null;
  const position = run.executed_path[Math.min(index, run.executed_path.length - 1)];
  const failed = run.status === "FAILED";

  return (
    <div className="space-y-3">
      <div>
        <div className="flex items-center justify-between">
          <span className="text-2xs uppercase tracking-wider text-muted">Status</span>
          <span
            className={`rounded px-1.5 py-0.5 text-2xs font-medium ${
              failed ? "bg-danger/15 text-danger" : "bg-success/15 text-success"
            }`}
          >
            {failed ? run.failure_mode : "NOMINAL"}
          </span>
        </div>
        {failed && <p className="mt-1 text-xs leading-snug text-danger/80">{run.reason}</p>}
      </div>

      <div className="border-t border-edge pt-2">
        <Stat label="Position" value={`${position?.x ?? 0}, ${position?.y ?? 0} px`} />
        <Stat label="Progress" value={`${(progress * 100).toFixed(0)}%`} />
        <Stat label="Distance" value={`${(run.distance_m * progress).toFixed(0)} m`} />
        <Stat label="Energy spent" value={`${energy.toFixed(4)} kWh`} />
        {remaining !== null && (
          <Stat
            label="Battery left"
            value={`${remaining.toFixed(4)} kWh`}
            tone={remaining < 0 ? "text-danger" : remaining < (batteryCapacity ?? 1) * 0.15 ? "text-warning" : ""}
          />
        )}
        <Stat label="Elapsed" value={`${(run.elapsed_seconds / 3600).toFixed(2)} h`} />
      </div>

      <div className="border-t border-edge pt-2">
        <Stat label="D* Lite repairs" value={String(run.replans)} />
        <Stat
          label="Route changes"
          value={String(run.reroutes)}
          tone={run.reroutes > 0 ? "text-warning" : ""}
        />
        <Stat label="Vertices repaired" value={String(run.repair_expansions)} />
        <Stat
          label="Map observed"
          value={`${((run.belief_summary.observed_fraction ?? 0) * 100).toFixed(1)}%`}
        />
      </div>
      <p className="border-t border-edge pt-2 text-2xs leading-snug text-muted">
        Repairs count every time the belief changed. Route changes count the times it mattered.
      </p>
    </div>
  );
}

const CATEGORY_TONE: Record<string, string> = {
  planner: "text-accent",
  navigation: "text-success",
  energy: "text-warning",
  risk: "text-danger",
  system: "text-muted",
};

export function EventLog({
  events,
  clockSeconds,
  onSelect,
}: {
  events: MissionEvent[];
  clockSeconds: number;
  onSelect: (event: MissionEvent) => void;
}) {
  const [filter, setFilter] = useState<string>("all");
  const [reroutesOnly, setReroutesOnly] = useState(true);

  const visible = useMemo(() => {
    return events
      .filter((event) => event.t_seconds <= clockSeconds + 1e-6)
      .filter((event) => filter === "all" || event.category === filter)
      .filter((event) => !reroutesOnly || event.kind !== "replan" || event.detail.rerouted === true)
      .slice(-200)
      .reverse();
  }, [events, clockSeconds, filter, reroutesOnly]);

  const categories = useMemo(
    () => ["all", ...Array.from(new Set(events.map((e) => e.category)))],
    [events],
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-edge px-3 py-2">
        {categories.map((category) => (
          <button
            key={category}
            onClick={() => setFilter(category)}
            className={`h-10 rounded px-2 py-0.5 text-2xs uppercase tracking-wider transition lg:h-auto ${
              filter === category ? "bg-edge text-fg" : "text-muted hover:text-fg"
            }`}
          >
            {category}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1.5 text-2xs text-muted">
          <input
            type="checkbox"
            checked={reroutesOnly}
            onChange={(event) => setReroutesOnly(event.target.checked)}
            className="accent-accent"
          />
          only repairs that changed the route
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto font-mono text-xs">
        {visible.length === 0 ? (
          <p className="p-3 text-muted">No events at this point in the mission.</p>
        ) : (
          visible.map((event, index) => (
            <button
              key={`${event.t_seconds}-${event.kind}-${index}`}
              onClick={() => onSelect(event)}
              className="flex w-full items-baseline gap-3 border-b border-edge/50 px-3 py-1 text-left hover:bg-edge/40"
            >
              <span className="tabular-nums text-muted">
                {new Date(event.t_seconds * 1000).toISOString().slice(11, 19)}
              </span>
              <span className={`w-20 shrink-0 ${CATEGORY_TONE[event.category] ?? "text-muted"}`}>
                {event.category}
              </span>
              <span className="text-fg">{event.kind}</span>
              <span className="truncate text-muted">{summarise(event)}</span>
            </button>
          ))
        )}
      </div>
    </div>
  );
}

function summarise(event: MissionEvent): string {
  const detail = event.detail ?? {};
  if (event.kind === "replan") {
    const delta = detail.cost_delta as number | null;
    return [
      `${detail.trigger_cells} cells`,
      `${detail.vertices_expanded} repaired`,
      detail.rerouted ? "rerouted" : "route held",
      delta != null ? `Δcost ${delta > 0 ? "+" : ""}${delta.toFixed(2)}` : null,
    ]
      .filter(Boolean)
      .join(" · ");
  }
  if (event.kind === "initial_plan") return `${detail.expansions} expansions`;
  if (event.kind === "battery_exhausted") return `${detail.spent_kwh} kWh spent`;
  if (event.kind === "goal_reached") return `${detail.distance_m} m`;
  return "";
}

export function EventDetail({ event }: { event: MissionEvent | null }) {
  if (!event) {
    return (
      <p className="text-xs text-muted">
        Select an event to see what the planner actually did.
      </p>
    );
  }
  const detail = event.detail ?? {};
  const replan = event.kind === "replan";
  return (
    <div className="space-y-2">
      <div className="flex items-baseline justify-between">
        <span className="font-mono text-xs text-accent">{event.kind}</span>
        <span className="font-mono text-2xs text-muted">
          t+{event.t_seconds.toFixed(0)}s
        </span>
      </div>
      {replan && (
        <ol className="space-y-1 border-l border-edge pl-3 text-xs text-muted">
          <li>Sensor observed {String(detail.trigger_cells)} cells that disagreed with the belief</li>
          <li>Belief map updated; hazard field changed</li>
          <li>D* Lite repaired {String(detail.vertices_expanded)} vertices</li>
          <li className={detail.rerouted ? "text-warning" : "text-muted"}>
            {detail.rerouted ? "New route committed" : "Existing route confirmed - no diversion"}
          </li>
        </ol>
      )}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-edge pt-2 font-mono text-xs">
        {Object.entries(detail).map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="truncate text-muted">{key}</dt>
            <dd className="truncate text-right text-fg">{String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

export function PlaybackControls({
  index,
  total,
  playing,
  speed,
  onIndex,
  onPlaying,
  onSpeed,
}: {
  index: number;
  total: number;
  playing: boolean;
  speed: number;
  onIndex: (value: number) => void;
  onPlaying: (value: boolean) => void;
  onSpeed: (value: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 lg:flex-nowrap">
      <button
        className="btn-ghost h-10 px-3 py-1 text-xs lg:h-auto"
        onClick={() => onPlaying(!playing)}
        disabled={total === 0}
      >
        {playing ? "Pause" : "Play"}
      </button>
      <button
        className="btn-ghost h-10 px-3 py-1 text-xs lg:h-auto"
        onClick={() => {
          onPlaying(false);
          onIndex(Math.min(index + 1, total));
        }}
        disabled={total === 0}
      >
        Step
      </button>
      <input
        type="range"
        min={0}
        max={Math.max(total, 0)}
        value={Math.min(index, total)}
        onChange={(event) => {
          onPlaying(false);
          onIndex(Number(event.target.value));
        }}
        aria-label="Traverse playback position"
        className="order-last h-1 w-full accent-accent lg:order-none lg:w-auto lg:flex-1"
        disabled={total === 0}
      />
      <div className="flex gap-1">
        {[1, 4, 16].map((value) => (
          <button
            key={value}
            aria-label={`Playback speed ${value}x`}
            aria-pressed={speed === value}
            onClick={() => onSpeed(value)}
            className={`h-10 min-w-10 rounded px-1.5 py-0.5 text-2xs lg:h-auto lg:min-w-0 ${
              speed === value ? "bg-accent/10 text-accent" : "text-muted hover:text-fg"
            }`}
          >
            {value}×
          </button>
        ))}
      </div>
      <span className="w-20 shrink-0 whitespace-nowrap text-right font-mono text-2xs tabular-nums text-muted">
        {Math.min(index, total)} / {total}
      </span>
    </div>
  );
}


const RECOVERY_WHY: Record<string, string> = {
  ReSense: "mast panorama: twice the range, half the noise",
  RelaxUncertainty: "halved the uncertainty penalty and replanned",
  BackUp: "retreated along the driven path for a new vantage",
};

/**
 * The behaviour tree that drove the run, and what it tried. Shaped after Nav2's
 * navigate-with-recovery tree; see backend/app/services/simulation/navigator.py.
 */
export function ExecutivePanel({ run }: { run: TraverseRun | null }) {
  if (!run) {
    return (
      <p className="text-xs leading-relaxed text-muted">
        A behaviour tree drives every traverse. When the rover is blocked it escalates through
        three recoveries before giving up.
      </p>
    );
  }
  if (!run.executive) {
    return <p className="text-xs text-muted">Recorded before the executive existed.</p>;
  }
  const { tree, ticks, recoveries } = run.executive;
  const tried = new Set(recoveries.map((r) => r.recovery));
  return (
    <div className="space-y-3">
      <ul className="space-y-0.5 font-mono text-2xs" aria-label="Behaviour tree">
        <TreeLine node={tree} depth={0} tried={tried} />
      </ul>
      <div className="flex items-baseline justify-between border-t border-line pt-2 text-2xs">
        <span className="uppercase tracking-wider text-muted">ticks</span>
        <span className="num text-xs text-fg">{ticks.toLocaleString("en-IN")}</span>
      </div>
      {recoveries.length === 0 ? (
        <p className="text-xs text-muted">No recovery needed: the drive never failed.</p>
      ) : (
        <ol className="space-y-2">
          {recoveries.map((entry, i) => (
            <li key={i} className="rounded-md border border-line bg-surface-2 p-2">
              <div className="flex items-baseline justify-between gap-2">
                <span className="font-mono text-xs text-fg">
                  {i + 1}. {entry.recovery}
                </span>
                <span className="font-mono text-2xs text-warning">{entry.after_error}</span>
              </div>
              <p className="mt-1 text-2xs leading-snug text-muted">{RECOVERY_WHY[entry.recovery]}</p>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}

function TreeLine({ node, depth, tried }: { node: TreeNode; depth: number; tried: Set<string> }) {
  const isControl = Boolean(node.children?.length);
  const used = tried.has(node.name);
  return (
    <>
      <li className="flex items-center gap-1.5" style={{ paddingLeft: `${depth * 12}px` }}>
        <span className={isControl ? "text-muted" : used ? "text-warning" : "text-fg"}>
          {isControl ? "▾" : "•"}
        </span>
        <span className={isControl ? "text-muted" : used ? "text-fg" : "text-muted"}>{node.name}</span>
        {node.retries !== undefined && <span className="text-muted">×{node.retries}</span>}
        {used && <span className="text-warning">used</span>}
      </li>
      {node.children?.map((child) => (
        <TreeLine key={child.name} node={child} depth={depth + 1} tried={tried} />
      ))}
    </>
  );
}
