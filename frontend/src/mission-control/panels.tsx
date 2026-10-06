/**
 * The console panels around the 3-D view.
 *
 * Grouped in one module because they are one thing - the chrome of a mission
 * console - and each is small enough that a file apiece would be more imports
 * than code. Anything that grows its own state or fetches gets its own file.
 */

import { useMemo, useState } from "react";

import type { MissionEvent, TerrainGrid, TerrainLayerName, TraverseRun } from "../api/types";

const LAYERS: { id: TerrainLayerName; label: string; hint: string }[] = [
  { id: "hazard", label: "Hazard", hint: "weighted slope, obstacle proximity and roughness" },
  { id: "uncertainty", label: "Uncertainty", hint: "1σ of the hazard estimate, propagated" },
  { id: "slope_deg", label: "Slope", hint: "degrees, from the rendered surface" },
  { id: "elevation_m", label: "Elevation", hint: "metres above the tile minimum" },
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
  layer: TerrainLayerName;
  onLayer: (layer: TerrainLayerName) => void;
  showLethal: boolean;
  onShowLethal: (value: boolean) => void;
  grid: TerrainGrid | null;
  exaggeration: number;
  onExaggeration: (value: number) => void;
}) {
  const range = grid?.ranges[layer];
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1">
        {LAYERS.map((entry) => (
          <button
            key={entry.id}
            title={entry.hint}
            aria-pressed={layer === entry.id}
            onClick={() => onLayer(entry.id)}
            className={`rounded px-2 py-1 text-[11px] transition ${
              layer === entry.id
                ? "bg-accent text-void"
                : "border border-edge text-slate-400 hover:border-accent hover:text-accent"
            }`}
          >
            {entry.label}
          </button>
        ))}
      </div>
      {range && (
        <div className="flex items-center gap-2 text-[10px] text-slate-500">
          <span className="tabular-nums">{range[0].toFixed(2)}</span>
          <span
            className="h-1.5 flex-1 rounded"
            style={{
              background:
                layer === "uncertainty"
                  ? "linear-gradient(90deg,#171f2e,#2a4c71,#3d99a8,#5eead4)"
                  : layer === "elevation_m"
                    ? "linear-gradient(90deg,#212630,#595c66,#9e9994,#dbd9d4)"
                    : "linear-gradient(90deg,#215c42,#82a840,#d99e1c,#bf362e)",
            }}
          />
          <span className="tabular-nums">{range[1].toFixed(2)}</span>
        </div>
      )}
      <label className="flex items-center gap-2 text-[11px] text-slate-400">
        <input
          type="checkbox"
          checked={showLethal}
          onChange={(event) => onShowLethal(event.target.checked)}
          className="accent-red-500"
        />
        Mark untraversable ground on every layer
      </label>
      <div className="flex items-center gap-2 text-[11px] text-slate-400">
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
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              exaggeration === value ? "bg-accent text-void" : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {value}×
          </button>
        ))}
        {exaggeration !== 1 && (
          <span className="text-[10px] text-amber-300/80">exaggerated</span>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, tone = "" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-[3px]">
      <span className="text-[10px] uppercase tracking-wider text-slate-500">{label}</span>
      <span className={`font-mono text-xs tabular-nums ${tone || "text-slate-200"}`}>{value}</span>
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
    return <p className="text-xs text-slate-500">Run a traverse to see telemetry.</p>;
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
          <span className="text-[10px] uppercase tracking-wider text-slate-500">Status</span>
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
              failed ? "bg-red-500/15 text-red-300" : "bg-emerald-500/15 text-emerald-300"
            }`}
          >
            {failed ? run.failure_mode : "NOMINAL"}
          </span>
        </div>
        {failed && <p className="mt-1 text-[11px] leading-snug text-red-300/80">{run.reason}</p>}
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
            tone={remaining < 0 ? "text-red-300" : remaining < (batteryCapacity ?? 1) * 0.15 ? "text-amber-300" : ""}
          />
        )}
        <Stat label="Elapsed" value={`${(run.elapsed_seconds / 3600).toFixed(2)} h`} />
      </div>

      <div className="border-t border-edge pt-2">
        <Stat label="D* Lite repairs" value={String(run.replans)} />
        <Stat
          label="Route changes"
          value={String(run.reroutes)}
          tone={run.reroutes > 0 ? "text-amber-300" : ""}
        />
        <Stat label="Vertices repaired" value={String(run.repair_expansions)} />
        <Stat
          label="Map observed"
          value={`${((run.belief_summary.observed_fraction ?? 0) * 100).toFixed(1)}%`}
        />
      </div>
      <p className="border-t border-edge pt-2 text-[10px] leading-snug text-slate-600">
        Repairs count every time the belief changed. Route changes count the times it mattered.
      </p>
    </div>
  );
}

const CATEGORY_TONE: Record<string, string> = {
  planner: "text-accent",
  navigation: "text-emerald-300",
  energy: "text-amber-300",
  risk: "text-red-300",
  system: "text-slate-400",
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
            className={`rounded px-2 py-0.5 text-[10px] uppercase tracking-wider transition ${
              filter === category ? "bg-edge text-slate-100" : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {category}
          </button>
        ))}
        <label className="ml-auto flex items-center gap-1.5 text-[10px] text-slate-500">
          <input
            type="checkbox"
            checked={reroutesOnly}
            onChange={(event) => setReroutesOnly(event.target.checked)}
            className="accent-teal-400"
          />
          only repairs that changed the route
        </label>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto font-mono text-[11px]">
        {visible.length === 0 ? (
          <p className="p-3 text-slate-600">No events at this point in the mission.</p>
        ) : (
          visible.map((event, index) => (
            <button
              key={`${event.t_seconds}-${event.kind}-${index}`}
              onClick={() => onSelect(event)}
              className="flex w-full items-baseline gap-3 border-b border-edge/50 px-3 py-1 text-left hover:bg-edge/40"
            >
              <span className="tabular-nums text-slate-600">
                {new Date(event.t_seconds * 1000).toISOString().slice(11, 19)}
              </span>
              <span className={`w-20 shrink-0 ${CATEGORY_TONE[event.category] ?? "text-slate-400"}`}>
                {event.category}
              </span>
              <span className="text-slate-300">{event.kind}</span>
              <span className="truncate text-slate-600">{summarise(event)}</span>
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
      <p className="text-xs text-slate-500">
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
        <span className="font-mono text-[10px] text-slate-500">
          t+{event.t_seconds.toFixed(0)}s
        </span>
      </div>
      {replan && (
        <ol className="space-y-1 border-l border-edge pl-3 text-[11px] text-slate-400">
          <li>Sensor observed {String(detail.trigger_cells)} cells that disagreed with the belief</li>
          <li>Belief map updated; hazard field changed</li>
          <li>D* Lite repaired {String(detail.vertices_expanded)} vertices</li>
          <li className={detail.rerouted ? "text-amber-300" : "text-slate-500"}>
            {detail.rerouted ? "New route committed" : "Existing route confirmed - no diversion"}
          </li>
        </ol>
      )}
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-edge pt-2 font-mono text-[11px]">
        {Object.entries(detail).map(([key, value]) => (
          <div key={key} className="contents">
            <dt className="truncate text-slate-500">{key}</dt>
            <dd className="truncate text-right text-slate-300">{String(value)}</dd>
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
    <div className="flex items-center gap-3">
      <button
        className="btn-ghost px-3 py-1 text-xs"
        onClick={() => onPlaying(!playing)}
        disabled={total === 0}
      >
        {playing ? "Pause" : "Play"}
      </button>
      <button
        className="btn-ghost px-3 py-1 text-xs"
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
        className="h-1 flex-1 accent-teal-400"
        disabled={total === 0}
      />
      <div className="flex gap-1">
        {[1, 4, 16].map((value) => (
          <button
            key={value}
            aria-label={`Playback speed ${value}x`}
            aria-pressed={speed === value}
            onClick={() => onSpeed(value)}
            className={`rounded px-1.5 py-0.5 text-[10px] ${
              speed === value ? "bg-accent text-void" : "text-slate-500 hover:text-slate-300"
            }`}
          >
            {value}×
          </button>
        ))}
      </div>
      <span className="w-20 text-right font-mono text-[10px] tabular-nums text-slate-500">
        {Math.min(index, total)} / {total}
      </span>
    </div>
  );
}
