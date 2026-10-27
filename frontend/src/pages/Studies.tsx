import { ArrowRight } from "lucide-react";
import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import type { Experiment, ExperimentSummary, Mission } from "../api/types";
import { KIND_LABEL, dateTime, duration, kwh, metres, percent, relativeTime } from "../lib/format";
import { loadActivity } from "../lib/missions";
import { useAsync } from "../lib/useAsync";
import Badge from "../ui/Badge";
import Modal from "../ui/Modal";
import PageHeader from "../ui/PageHeader";
import StatTile from "../ui/StatTile";
import { EmptyState, ErrorState, TableSkeleton } from "../ui/States";
import { humanize, outcomeTone } from "../ui/status";

interface Row extends ExperimentSummary {
  mission: Mission;
}

async function loadStudies(): Promise<Row[]> {
  const missions = await api.listMissions();
  const activity = await loadActivity(missions, { experiments: true });
  return activity
    .flatMap(({ mission, experiments }) => experiments.map((exp) => ({ ...exp, mission })))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
}

function median(values: number[]): number {
  if (values.length === 0) return NaN;
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

export default function Studies() {
  const [params, setParams] = useSearchParams();
  const kind = params.get("kind") ?? "";
  const status = params.get("status") ?? "";
  const openId = params.get("experiment");
  const { data, error, loading, reload } = useAsync(loadStudies, []);

  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    if (key === "experiment" && !value) next.delete("mission");
    setParams(next, { replace: key !== "experiment" });
  };

  const rows = useMemo(
    () => (data ?? []).filter((row) => (!kind || row.kind === kind) && (!status || row.status === status)),
    [data, kind, status],
  );
  const succeeded = (data ?? []).filter((row) => row.status === "SUCCESS").length;
  const open = openId ? (data ?? []).find((row) => row.id === openId) : undefined;
  const openMission = open?.mission.id ?? params.get("mission");

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="Studies"
        description="Route trade-offs, Monte Carlo robustness, multi-rover deconfliction and science tours. Each one records its seed and code revision, so it can be run again."
      />

      <div className="mb-8 grid gap-3 sm:grid-cols-3">
        <StatTile label="Studies run" loading={loading && !data} value={data?.length ?? 0} detail="Across every mission" />
        <StatTile
          label="Succeeded"
          loading={loading && !data}
          value={data?.length ? percent(succeeded / data.length) : "—"}
          detail={data ? `${succeeded} of ${data.length}` : ""}
        />
        <StatTile
          label="Median runtime"
          loading={loading && !data}
          value={data?.length ? duration(median(data.map((row) => row.runtime_seconds))) : "—"}
          detail="Wall clock on the API host"
        />
      </div>

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select className="field w-auto" aria-label="Filter by kind" value={kind} onChange={(event) => set("kind", event.target.value)}>
          <option value="">All kinds</option>
          {Object.entries(KIND_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        <select className="field w-auto" aria-label="Filter by outcome" value={status} onChange={(event) => set("status", event.target.value)}>
          <option value="">All outcomes</option>
          <option value="SUCCESS">Succeeded</option>
          <option value="FAILED">Failed</option>
        </select>
        {data && (
          <span className="ml-auto font-mono text-2xs uppercase tracking-wider text-subtle">
            {rows.length} of {data.length}
          </span>
        )}
      </div>

      {error && !data ? (
        <ErrorState error={error} onRetry={reload} title="Could not load studies" />
      ) : loading && !data ? (
        <div className="panel overflow-hidden">
          <TableSkeleton rows={8} cols={6} />
        </div>
      ) : rows.length === 0 ? (
        <EmptyState
          title={data?.length ? "No studies match" : "No studies yet"}
          body={
            data?.length
              ? "Nothing of that kind or outcome."
              : "Run a route study, robustness study, fleet plan or science tour from mission control, or queue a long one on the Jobs page."
          }
          action={
            data?.length ? (
              <button className="btn-ghost" onClick={() => setParams({}, { replace: true })}>
                Clear filters
              </button>
            ) : (
              <Link to="/missions" className="btn-primary">
                Choose a mission
              </Link>
            )
          }
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>Study</th>
                <th>Mission</th>
                <th>Outcome</th>
                <th className="text-right">Seed</th>
                <th>Revision</th>
                <th className="text-right">Runtime</th>
                <th className="text-right">When</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id} className="cursor-pointer" onClick={() => set("experiment", row.id)}>
                  <td>
                    <button
                      className="text-left font-medium hover:text-accent"
                      onClick={(event) => {
                        event.stopPropagation();
                        set("experiment", row.id);
                      }}
                    >
                      {KIND_LABEL[row.kind] ?? row.kind}
                    </button>
                  </td>
                  <td className="max-w-56 truncate text-muted">{row.mission.name}</td>
                  <td>
                    <Badge tone={outcomeTone(row.status)}>{row.status === "SUCCESS" ? "Succeeded" : humanize(row.failure_mode) || "Failed"}</Badge>
                  </td>
                  <td className="num text-right text-muted">{row.seed ?? "—"}</td>
                  <td className="num text-xs text-muted">{row.code_revision ?? "—"}</td>
                  <td className="num text-right">{duration(row.runtime_seconds)}</td>
                  <td className="whitespace-nowrap text-right text-xs text-muted" title={dateTime(row.created_at)}>
                    {relativeTime(row.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <ExperimentDetail
        missionId={openMission}
        experimentId={openId}
        missionName={open?.mission.name}
        onClose={() => set("experiment", null)}
      />
    </div>
  );
}

function ExperimentDetail({
  missionId,
  experimentId,
  missionName,
  onClose,
}: {
  missionId: string | null | undefined;
  experimentId: string | null;
  missionName?: string;
  onClose: () => void;
}) {
  const detail = useAsync<Experiment | null>(
    () => (missionId && experimentId ? api.getExperiment(missionId, experimentId) : Promise.resolve(null)),
    [missionId, experimentId],
  );
  const exp = detail.data;

  return (
    <Modal
      open={Boolean(experimentId)}
      onClose={onClose}
      width="max-w-2xl"
      title={exp ? KIND_LABEL[exp.kind] ?? exp.kind : "Study"}
      description={missionName ? `${missionName}${exp ? ` · ${dateTime(exp.created_at)}` : ""}` : undefined}
      footer={
        missionId ? (
          <Link to={`/missions/${missionId}/control`} className="btn-ghost" onClick={onClose}>
            Open mission control
            <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
          </Link>
        ) : undefined
      }
    >
      {detail.error ? (
        <ErrorState error={detail.error} onRetry={detail.reload} title="Could not load this study" />
      ) : !exp ? (
        <div className="space-y-3">
          <div className="skeleton h-6 w-2/3" />
          <div className="skeleton h-24 w-full" />
        </div>
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={outcomeTone(exp.status)}>{exp.status === "SUCCESS" ? "Succeeded" : humanize(exp.failure_mode) || "Failed"}</Badge>
            <span className="font-mono text-2xs uppercase tracking-wider text-subtle">
              seed {exp.seed ?? "—"} · rev {exp.code_revision ?? "—"} · {duration(exp.runtime_seconds)}
            </span>
          </div>
          {exp.reason && <p className="text-sm text-muted">{exp.reason}</p>}
          {exp.status === "SUCCESS" && <ResultSummary exp={exp} />}
          <details className="rounded-md border border-line">
            <summary className="cursor-pointer select-none px-3 py-2 font-mono text-2xs uppercase tracking-wider text-muted hover:text-fg">
              Parameters and raw result
            </summary>
            <pre className="max-h-72 overflow-auto border-t border-line px-3 py-2 font-mono text-2xs leading-5 text-muted">
              {JSON.stringify({ parameters: exp.parameters, result: compact(exp.result) }, null, 2)}
            </pre>
          </details>
        </div>
      )}
    </Modal>
  );
}

/** Long arrays (waypoints, routes) collapse to a count; nobody reads 172 coordinates. */
function compact(value: unknown): unknown {
  if (Array.isArray(value)) return value.length > 12 ? `[${value.length} items]` : value.map(compact);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.entries(value as Record<string, unknown>).map(([k, v]) => [k, compact(v)]));
  }
  return value;
}

function Facts({ items }: { items: [string, string][] }) {
  return (
    <dl className="grid grid-cols-2 gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-4">
      {items.map(([label, value]) => (
        <div key={label} className="bg-surface-2 px-3 py-2">
          <dt className="font-mono text-2xs uppercase tracking-wider text-subtle">{label}</dt>
          <dd className="num mt-0.5 text-sm text-fg">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

type Stats = { p10: number; p50: number; p90: number };

function ResultSummary({ exp }: { exp: Experiment }) {
  const r = exp.result;

  if (exp.kind === "monte_carlo") {
    const rows: [string, Stats | undefined, (v: number) => string][] = [
      ["Distance", r.distance_m, metres],
      ["Energy", r.energy_kwh, (v) => kwh(v)],
      ["Replans", r.replans, (v) => v.toFixed(0)],
      ["Sim time", r.elapsed_seconds, duration],
    ];
    return (
      <div className="space-y-3">
        <p className="text-base text-fg">
          {percent(r.success_probability ?? 0)} of {r.trials} perturbed trials reached the goal.
        </p>
        <table className="tbl rounded-md border border-line">
          <thead>
            <tr>
              <th>Over successful trials</th>
              <th className="text-right">P10</th>
              <th className="text-right">Median</th>
              <th className="text-right">P90</th>
            </tr>
          </thead>
          <tbody>
            {rows
              .filter(([, stats]) => stats)
              .map(([label, stats, fmt]) => (
                <tr key={label}>
                  <td className="text-muted">{label}</td>
                  <td className="num text-right">{fmt(stats!.p10)}</td>
                  <td className="num text-right">{fmt(stats!.p50)}</td>
                  <td className="num text-right">{fmt(stats!.p90)}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </div>
    );
  }

  if (exp.kind === "route_study") {
    const front = (r.front ?? []) as { label: string; objectives: Record<string, number> }[];
    return (
      <div className="space-y-3">
        <p className="text-base text-fg">
          {front.length} of {r.candidates_planned} weightings are on the Pareto front.
        </p>
        <div className="overflow-x-auto rounded-md border border-line">
          <table className="tbl">
            <thead>
              <tr>
                <th>Weighting</th>
                <th className="text-right">Distance</th>
                <th className="text-right">Energy</th>
                <th className="text-right">Mean hazard</th>
              </tr>
            </thead>
            <tbody>
              {front.map((candidate) => (
                <tr key={candidate.label}>
                  <td className="num whitespace-nowrap text-xs text-muted">{candidate.label}</td>
                  <td className="num text-right">{metres(candidate.objectives.distance_m)}</td>
                  <td className="num text-right">{kwh(candidate.objectives.energy_kwh)}</td>
                  <td className="num text-right">{candidate.objectives.mean_hazard.toFixed(3)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {r.method && <p className="text-xs text-subtle">{r.method}.</p>}
      </div>
    );
  }

  if (exp.kind === "fleet_plan") {
    const costs = Object.entries((r.per_agent_cost ?? {}) as Record<string, number>);
    return (
      <div className="space-y-3">
        <p className="text-base text-fg">
          {costs.length} rovers, {r.verified_conflict_free ? "verified conflict-free" : "with conflicts left"}, done in{" "}
          {r.makespan} moves.
        </p>
        <Facts
          items={[
            ["Algorithm", "CBS"],
            ["High-level nodes", String(r.high_level_nodes ?? "—")],
            ["Conflicts branched", String((r.conflicts_branched ?? []).length)],
            ["Sum of costs", Number(r.sum_of_costs ?? 0).toFixed(0)],
          ]}
        />
        <ul className="divide-y divide-line rounded-md border border-line">
          {costs.map(([label, cost]) => (
            <li key={label} className="flex h-10 items-center justify-between px-3 text-sm">
              <span className="font-mono text-xs text-muted">{label}</span>
              <span className="num">cost {cost.toFixed(0)}</span>
            </li>
          ))}
        </ul>
      </div>
    );
  }

  if (exp.kind === "science_tour") {
    return (
      <div className="space-y-3">
        <p className="text-base text-fg">
          Visited {r.targets_visited} of {r.targets_offered} targets for {Number(r.science_value_collected ?? 0).toFixed(1)} science value.
        </p>
        <Facts
          items={[
            ["Distance", metres(r.distance_m ?? 0)],
            ["Energy", kwh(r.energy_kwh ?? 0)],
            ["Budget used", percent(r.energy_utilisation ?? 0)],
            ["Skipped", String((r.skipped ?? []).length)],
          ]}
        />
        {r.method && <p className="text-xs text-subtle">{r.method}.</p>}
      </div>
    );
  }

  return null;
}
