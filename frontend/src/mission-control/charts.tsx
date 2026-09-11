/**
 * Charts for the trade-off study and the robustness study.
 *
 * Both are deliberately single-series. The Pareto plot shows routes, not four
 * competing categories, so it uses emphasis - one hue for the front, muted ink
 * for the dominated - rather than a categorical palette; fill versus outline
 * carries the same distinction without relying on colour. The Monte Carlo panel
 * leads with a number rather than a chart, because "will this mission finish"
 * has a single answer and a bar chart of one value is not a chart.
 *
 * Status hues (amber, red) appear only where they mean status.
 */

import { useState } from "react";

import type { Experiment, RouteCandidate } from "../api/types";

const PLOT = { width: 420, height: 212, left: 52, right: 16, top: 14, bottom: 36 };

function scale(value: number, low: number, high: number, from: number, to: number): number {
  if (high - low < 1e-12) return (from + to) / 2;
  return from + ((value - low) / (high - low)) * (to - from);
}

export function ParetoChart({
  experiment,
  selected,
  onSelect,
}: {
  experiment: Experiment | null;
  selected: string | null;
  onSelect: (candidate: RouteCandidate | null) => void;
}) {
  const [hovered, setHovered] = useState<RouteCandidate | null>(null);

  if (!experiment) {
    return (
      <p className="text-xs text-slate-500">
        Run a route study to see the trade-off between distance, energy and hazard exposure.
      </p>
    );
  }
  if (experiment.status !== "SUCCESS") {
    return <p className="text-xs text-red-300">{experiment.reason}</p>;
  }

  const front = (experiment.result.front ?? []) as RouteCandidate[];
  const dominated = (experiment.result.dominated ?? []) as RouteCandidate[];
  const all = [...dominated, ...front];
  if (all.length === 0) return <p className="text-xs text-slate-500">No routes were found.</p>;

  const energies = all.map((c) => c.objectives.energy_kwh);
  const hazards = all.map((c) => c.objectives.mean_hazard);
  const pad = (values: number[]) => {
    const low = Math.min(...values);
    const high = Math.max(...values);
    const margin = (high - low || Math.abs(high) || 1) * 0.12;
    return [low - margin, high + margin] as const;
  };
  const [energyLow, energyHigh] = pad(energies);
  const [hazardLow, hazardHigh] = pad(hazards);

  const x = (candidate: RouteCandidate) =>
    scale(candidate.objectives.energy_kwh, energyLow, energyHigh, PLOT.left, PLOT.width - PLOT.right);
  const y = (candidate: RouteCandidate) =>
    scale(candidate.objectives.mean_hazard, hazardLow, hazardHigh, PLOT.height - PLOT.bottom, PLOT.top);

  // Direct-label only the corners of the front - the cheapest route and the
  // safest one. A label on every point is unreadable and goes unread.
  const cheapest = front.reduce<RouteCandidate | null>(
    (best, c) => (!best || c.objectives.energy_kwh < best.objectives.energy_kwh ? c : best),
    null,
  );
  const safest = front.reduce<RouteCandidate | null>(
    (best, c) => (!best || c.objectives.mean_hazard < best.objectives.mean_hazard ? c : best),
    null,
  );

  const ordered = [...front].sort((a, b) => a.objectives.energy_kwh - b.objectives.energy_kwh);
  const frontPath = ordered.map((c) => `${x(c)},${y(c)}`).join(" ");

  return (
    <div className="flex flex-wrap gap-x-6 gap-y-2">
      {/* The plot is capped rather than fluid: a scatter stretched to a 1000px
          panel puts its marks further apart without telling the reader anything
          more, and the aspect ratio stops meaning what the axes imply. */}
      <svg
        viewBox={`0 0 ${PLOT.width} ${PLOT.height}`}
        className="w-full max-w-[420px] shrink-0"
        role="img"
        aria-label="Energy against mean hazard exposure for each planned route"
      >
        {[0, 0.25, 0.5, 0.75, 1].map((t) => {
          const gy = PLOT.top + t * (PLOT.height - PLOT.top - PLOT.bottom);
          const gx = PLOT.left + t * (PLOT.width - PLOT.left - PLOT.right);
          return (
            <g key={t}>
              <line x1={PLOT.left} x2={PLOT.width - PLOT.right} y1={gy} y2={gy} stroke="#1e2740" strokeWidth={1} />
              <line y1={PLOT.top} y2={PLOT.height - PLOT.bottom} x1={gx} x2={gx} stroke="#1e2740" strokeWidth={1} />
            </g>
          );
        })}

        {/* The front itself, as a hairline through the non-dominated set. */}
        {ordered.length > 1 && (
          <polyline points={frontPath} fill="none" stroke="#2dd4bf" strokeWidth={1.5} opacity={0.45} />
        )}

        {dominated.map((candidate) => (
          <circle
            key={candidate.label}
            cx={x(candidate)}
            cy={y(candidate)}
            r={4.5}
            fill="none"
            stroke="#64748b"
            strokeWidth={1.5}
            onMouseEnter={() => setHovered(candidate)}
            onMouseLeave={() => setHovered(null)}
          />
        ))}

        {front.map((candidate) => {
          const isSelected = selected === candidate.label;
          return (
            <g key={candidate.label}>
              {isSelected && (
                <circle cx={x(candidate)} cy={y(candidate)} r={9} fill="none" stroke="#111726" strokeWidth={2} />
              )}
              <circle
                cx={x(candidate)}
                cy={y(candidate)}
                r={isSelected ? 6.5 : 5}
                fill="#2dd4bf"
                stroke={isSelected ? "#5eead4" : "none"}
                strokeWidth={2}
                className="cursor-pointer"
                onMouseEnter={() => setHovered(candidate)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onSelect(isSelected ? null : candidate)}
              />
            </g>
          );
        })}

        {[cheapest, safest]
          .filter((c): c is RouteCandidate => Boolean(c))
          .filter((c, index, list) => list.findIndex((o) => o.label === c.label) === index)
          .map((candidate) => {
            // Flip the label inboard near the right edge. A direct label that
            // runs off the plot is worse than no label - it reads as a clipped
            // value rather than as annotation.
            const cx = x(candidate);
            const flip = cx > PLOT.width - PLOT.right - 70;
            return (
              <text
                key={`label-${candidate.label}`}
                x={flip ? cx - 9 : cx + 9}
                y={y(candidate) - 7}
                textAnchor={flip ? "end" : "start"}
                className="fill-slate-400 text-[9px]"
              >
                {candidate.label === cheapest?.label ? "least energy" : "least hazard"}
              </text>
            );
          })}

        <line x1={PLOT.left} x2={PLOT.width - PLOT.right} y1={PLOT.height - PLOT.bottom} y2={PLOT.height - PLOT.bottom} stroke="#334155" strokeWidth={1} />
        <line x1={PLOT.left} x2={PLOT.left} y1={PLOT.top} y2={PLOT.height - PLOT.bottom} stroke="#334155" strokeWidth={1} />
        <text x={(PLOT.width + PLOT.left) / 2} y={PLOT.height - 8} textAnchor="middle" className="fill-slate-500 text-[10px]">
          energy (kWh) →
        </text>
        <text x={12} y={(PLOT.height - PLOT.bottom + PLOT.top) / 2} transform={`rotate(-90 12 ${(PLOT.height - PLOT.bottom + PLOT.top) / 2})`} textAnchor="middle" className="fill-slate-500 text-[10px]">
          mean hazard →
        </text>
        <text x={PLOT.left} y={PLOT.height - PLOT.bottom + 13} className="fill-slate-600 text-[9px]">
          {energyLow.toFixed(3)}
        </text>
        <text x={PLOT.width - PLOT.right} y={PLOT.height - PLOT.bottom + 13} textAnchor="end" className="fill-slate-600 text-[9px]">
          {energyHigh.toFixed(3)}
        </text>
      </svg>

      <div className="w-full max-w-[420px] space-y-2">
      <div className="flex items-center gap-4 text-[10px] text-slate-500">
        <span className="flex items-center gap-1.5">
          <svg width="10" height="10"><circle cx="5" cy="5" r="4" fill="#2dd4bf" /></svg>
          on the front
        </span>
        <span className="flex items-center gap-1.5">
          <svg width="10" height="10"><circle cx="5" cy="5" r="3.5" fill="none" stroke="#64748b" strokeWidth="1.5" /></svg>
          dominated
        </span>
        <span className="ml-auto">click a route to draw it on the terrain</span>
      </div>

      {(hovered || selected) && (
        <RouteFacts
          candidate={hovered ?? front.find((c) => c.label === selected) ?? null}
        />
      )}

      <p className="text-[10px] leading-snug text-slate-600">
        Weighted-sum scalarisation recovers the convex hull of the true front, not all of
        it. Routes in a concavity are unreachable by any weighting and do not appear.
      </p>
      </div>
    </div>
  );
}

function RouteFacts({ candidate }: { candidate: RouteCandidate | null }) {
  if (!candidate) return null;
  const o = candidate.objectives;
  return (
    <dl className="grid grid-cols-4 gap-x-3 border-t border-edge pt-2 font-mono text-[11px]">
      {[
        ["distance", `${o.distance_m.toFixed(0)} m`],
        ["energy", `${o.energy_kwh.toFixed(4)} kWh`],
        ["mean hazard", o.mean_hazard.toFixed(3)],
        ["peak hazard", o.max_hazard.toFixed(3)],
      ].map(([key, value]) => (
        <div key={key}>
          <dt className="text-[9px] uppercase tracking-wider text-slate-600">{key}</dt>
          <dd className="text-slate-200">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

export function MonteCarloPanel({ experiment }: { experiment: Experiment | null }) {
  if (!experiment) {
    return (
      <p className="text-xs text-slate-500">
        Run a Monte Carlo study to see how often this mission finishes, and what it costs when
        it does.
      </p>
    );
  }
  const result = experiment.result;
  const probability = (result.success_probability ?? 0) as number;
  const energy = result.energy_kwh as Record<string, number> | undefined;
  const failures = (result.failures_by_mode ?? {}) as Record<string, number>;
  const trials = result.trials as number;

  const tone =
    probability >= 0.9 ? "text-emerald-300" : probability >= 0.6 ? "text-amber-300" : "text-red-300";

  return (
    <div className="max-w-[560px] space-y-4">
      <div>
        <div className={`font-mono text-4xl tabular-nums ${tone}`}>
          {(probability * 100).toFixed(1)}%
        </div>
        <p className="text-[11px] text-slate-500">
          of {trials} trials reached the goal · seed {experiment.seed}
        </p>
      </div>

      {energy && Object.keys(energy).length > 0 && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
            Energy over successful trials
          </p>
          <PercentileStrip stats={energy} unit="kWh" />
          <p className="mt-1 text-[10px] text-slate-600">
            P90 is the figure a battery is sized against. The mean is the figure that strands
            missions.
          </p>
        </div>
      )}

      {Object.keys(failures).length > 0 && (
        <div>
          <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">
            How the failures failed
          </p>
          <ul className="space-y-1">
            {Object.entries(failures).map(([mode, count]) => (
              <li key={mode} className="flex items-center gap-2">
                <span aria-hidden className="text-red-400">▲</span>
                <span className="w-40 shrink-0 font-mono text-[11px] text-slate-300">{mode}</span>
                <span className="h-1.5 rounded bg-red-500/70" style={{ width: `${(count / trials) * 100}%`, minWidth: 4 }} />
                <span className="font-mono text-[11px] tabular-nums text-slate-500">{count}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="border-t border-edge pt-2 text-[10px] leading-snug text-slate-600">
        Percentiles cover successful trials only; the success rate covers all of them. Mixing
        the two would let a study look cheap because its expensive runs died early.
      </p>
    </div>
  );
}

function PercentileStrip({ stats, unit }: { stats: Record<string, number>; unit: string }) {
  const { min, max, p10, p50, p90 } = stats;
  const span = max - min || 1;
  const at = (value: number) => ((value - min) / span) * 100;

  return (
    <div className="space-y-1">
      <div className="relative h-6">
        <div className="absolute inset-x-0 top-2.5 h-1 rounded bg-edge" />
        <div
          className="absolute top-2.5 h-1 rounded bg-teal-400/60"
          style={{ left: `${at(p10)}%`, width: `${at(p90) - at(p10)}%` }}
        />
        <div
          className="absolute top-1 h-4 w-0.5 rounded bg-accent"
          style={{ left: `${at(p50)}%` }}
          title={`P50 ${p50.toFixed(4)} ${unit}`}
        />
      </div>
      <div className="flex justify-between font-mono text-[10px] tabular-nums text-slate-500">
        <span>P10 {p10.toFixed(4)}</span>
        <span className="text-slate-300">P50 {p50.toFixed(4)}</span>
        <span>P90 {p90.toFixed(4)}</span>
      </div>
    </div>
  );
}
