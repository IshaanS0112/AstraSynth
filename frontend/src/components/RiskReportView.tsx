import { useState } from "react";

import type { RiskReport } from "../api/types";

const RISK_STYLES: Record<string, string> = {
  LOW: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  MEDIUM: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  HIGH: "bg-rose-500/15 text-rose-300 border-rose-500/40",
};

const FEASIBILITY_STYLES: Record<string, string> = {
  FEASIBLE: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
  FEASIBLE_WITH_MARGIN: "bg-amber-500/15 text-amber-300 border-amber-500/40",
  INFEASIBLE: "bg-rose-500/15 text-rose-300 border-rose-500/40",
};

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-md border border-edge bg-void px-3 py-2">
      <div className="text-[10px] uppercase tracking-wider text-slate-500">{label}</div>
      <div className="font-mono text-sm text-slate-100">{value}</div>
      {hint && <div className="text-[10px] text-slate-500">{hint}</div>}
    </div>
  );
}

export default function RiskReportView({
  report,
  onGenerateNarrative,
  generating,
}: {
  report: RiskReport;
  onGenerateNarrative: () => void;
  generating: boolean;
}) {
  const [showContext, setShowContext] = useState(false);
  const context = report.structured_context;
  const narrative = report.ai_narrative;
  const utilisation = context.rover_constraints.energy_utilisation;

  return (
    <div className="space-y-4">
      <div className="panel p-4">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <span
            className={`rounded border px-3 py-1 text-xs font-semibold tracking-wider ${
              RISK_STYLES[report.risk_score ?? ""] ?? "border-edge text-slate-300"
            }`}
          >
            RISK {report.risk_score} · {context.risk_score_numeric}
          </span>
          <span
            className={`rounded border px-3 py-1 text-xs font-semibold tracking-wider ${
              FEASIBILITY_STYLES[report.feasibility ?? ""] ?? "border-edge text-slate-300"
            }`}
          >
            {report.feasibility?.replace(/_/g, " ")}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Stat label="Distance" value={`${context.path_summary.total_distance_m} m`} />
          <Stat
            label="Energy"
            value={`${context.path_summary.total_energy_cost_kwh} kWh`}
            hint={`of ${context.rover_constraints.battery_capacity_kwh} kWh`}
          />
          <Stat
            label="Margin"
            value={`${context.rover_constraints.energy_margin_kwh} kWh`}
            hint={`${(utilisation * 100).toFixed(1)}% used`}
          />
          <Stat
            label="Max slope"
            value={`${context.path_summary.max_slope_encountered_deg}°`}
            hint={`limit ${context.rover_constraints.max_traversable_slope_deg}°`}
          />
          <Stat label="Mean path hazard" value={`${context.path_summary.mean_path_hazard}`} />
          <Stat label="Peak path hazard" value={`${context.path_summary.max_path_hazard}`} />
          <Stat label="Waypoints" value={`${context.path_summary.num_waypoints}`} />
          <Stat
            label="Nodes expanded"
            value={`${context.path_summary.nodes_expanded}`}
            hint={context.path_summary.algorithm}
          />
        </div>

        <div className="mt-4">
          <div className="mb-1 flex justify-between text-[10px] uppercase tracking-wider text-slate-500">
            <span>Battery utilisation</span>
            <span>{(utilisation * 100).toFixed(1)}%</span>
          </div>
          <div className="h-2 w-full overflow-hidden rounded-full bg-void">
            <div
              className={`h-full ${
                utilisation > 1
                  ? "bg-rose-500"
                  : utilisation > 0.85
                    ? "bg-amber-400"
                    : "bg-emerald-400"
              }`}
              style={{ width: `${Math.min(utilisation, 1) * 100}%` }}
            />
          </div>
        </div>
      </div>

      <div className="panel p-4">
        <div className="mb-3 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-slate-200">Mission narrative</h3>
          <button className="btn-ghost" onClick={onGenerateNarrative} disabled={generating}>
            {generating ? "Generating…" : narrative ? "Regenerate" : "Generate report"}
          </button>
        </div>

        {!narrative && (
          <p className="text-sm text-slate-500">
            Every figure above is already computed. Generating the narrative only turns
            those numbers into prose — it does not change any of them.
          </p>
        )}

        {narrative && (
          <div className="space-y-3">
            <div
              className={`inline-block rounded border px-2 py-0.5 text-[10px] uppercase tracking-wider ${
                narrative.generated_by === "llm"
                  ? "border-teal-500/40 bg-teal-500/10 text-teal-300"
                  : "border-slate-600 bg-slate-500/10 text-slate-400"
              }`}
            >
              {narrative.generated_by === "llm" ? "LLM narrative" : "Deterministic fallback"}
            </div>
            {narrative.fallback_reason && (
              <p className="text-xs text-slate-500">Fallback reason: {narrative.fallback_reason}</p>
            )}

            <p className="text-sm leading-relaxed text-slate-300">{narrative.summary}</p>

            {narrative.top_risks.length > 0 && (
              <ul className="space-y-1.5">
                {narrative.top_risks.map((risk) => (
                  <li key={risk.segment_id} className="rounded border border-edge bg-void p-2 text-xs">
                    <span className="font-mono text-accent">segment {risk.segment_id}</span>
                    <span className="text-slate-400"> — {risk.reason}</span>
                  </li>
                ))}
              </ul>
            )}

            <div className="rounded border border-edge bg-void p-3 text-sm text-slate-300">
              <span className="text-[10px] uppercase tracking-wider text-slate-500">
                Recommendation
              </span>
              <p className="mt-1">{narrative.recommendation}</p>
            </div>

            {!!narrative.dropped_citations && (
              <p className="text-xs text-amber-400">
                {narrative.dropped_citations} cited segment ID(s) were not present in the
                structured context and were discarded.
              </p>
            )}
          </div>
        )}
      </div>

      <div className="panel p-4">
        <button
          className="flex w-full items-center justify-between text-left"
          onClick={() => setShowContext((value) => !value)}
        >
          <span className="text-sm font-semibold text-slate-200">
            Structured context (pre-LLM, auditable)
          </span>
          <span className="text-xs text-slate-500">{showContext ? "hide" : "show"}</span>
        </button>
        {showContext && (
          <pre className="mt-3 max-h-96 overflow-auto rounded border border-edge bg-void p-3 font-mono text-[11px] leading-relaxed text-slate-400">
            {JSON.stringify(context, null, 2)}
          </pre>
        )}
      </div>
    </div>
  );
}
