import { CircleCheck, CircleX, RefreshCw } from "lucide-react";
import { useMemo } from "react";

import { ApiError, api } from "../api/client";
import type { Readiness } from "../api/types";
import { count, percent } from "../lib/format";
import { parsePrometheus, summarise, type Quantile } from "../lib/prometheus";
import { useAsync } from "../lib/useAsync";
import { useApiStatus } from "../shell/useApiStatus";
import Badge from "../ui/Badge";
import PageHeader, { Section } from "../ui/PageHeader";
import StatTile from "../ui/StatTile";
import { EmptyState, ErrorState, TableSkeleton } from "../ui/States";

function ms(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return "—";
  return seconds < 1 ? `${Math.round(seconds * 1000)} ms` : `${seconds.toFixed(2)} s`;
}

/** Past the last finite bucket all that is known is a floor, so print it as one. */
function quantile(q: Quantile | null): string {
  if (!q) return "—";
  return q.atLeast ? `> ${ms(q.seconds)}` : ms(q.seconds);
}

/** /ready answers 503 with the same checks as its detail; that is a result, not an error. */
async function readiness(): Promise<Readiness> {
  try {
    return await api.ready();
  } catch (exc) {
    if (exc instanceof ApiError && exc.status === 503 && exc.detail && typeof exc.detail === "object") {
      return exc.detail as Readiness;
    }
    throw exc;
  }
}

export default function System() {
  const live = useApiStatus(10000);
  const ready = useAsync(readiness, [], 15000);
  const queue = useAsync(() => api.queueStats(), [], 15000);
  const described = useAsync(() => api.describe(), []);
  const metrics = useAsync(() => api.metrics(), [], 15000);

  const summary = useMemo(() => (metrics.data ? summarise(parsePrometheus(metrics.data)) : null), [metrics.data]);
  const endpoints = described.data
    ? Object.values(described.data.paths).reduce((acc, methods) => acc + Object.keys(methods).length, 0)
    : null;

  const refresh = () => {
    ready.reload();
    queue.reload();
    metrics.reload();
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="System"
        description="Liveness, readiness and the metrics this API process exposes for Prometheus. Counters reset when the process restarts."
        actions={
          <button className="btn-ghost" onClick={refresh}>
            <RefreshCw className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
            Refresh
          </button>
        }
        meta={
          described.data ? (
            <span className="font-mono text-2xs uppercase tracking-wider text-subtle">
              {described.data.info.title} v{described.data.info.version} · {endpoints} operations
            </span>
          ) : undefined
        }
      />

      <div className="mb-8 grid gap-3 md:grid-cols-3">
        <CheckCard
          title="Liveness"
          endpoint="GET /health"
          ok={live.state === "checking" ? null : live.state === "up"}
          detail={
            live.state === "up"
              ? `Answered in ${Math.round(live.latencyMs ?? 0)} ms`
              : live.state === "down"
                ? "No answer"
                : "Checking"
          }
          items={[]}
        />
        <CheckCard
          title="Readiness"
          endpoint="GET /ready"
          ok={ready.data ? ready.data.ready : ready.error ? false : null}
          detail={ready.data ? (ready.data.ready ? "Can serve a request end to end" : "Not ready to serve") : ready.error ? ready.error.message : "Checking"}
          items={
            ready.data
              ? [
                  ["Database", ready.data.database],
                  ["Storage", ready.data.storage],
                ]
              : []
          }
          note={ready.data?.database_error}
        />
        <CheckCard
          title="Job workers"
          endpoint="GET /jobs"
          ok={queue.data ? queue.data.in_process_workers > 0 : queue.error ? false : null}
          detail={
            queue.data
              ? queue.data.in_process_workers > 0
                ? `${queue.data.in_process_workers} in-process ${queue.data.in_process_workers === 1 ? "worker" : "workers"}`
                : "None in process: run python -m app.worker"
              : "Checking"
          }
          items={Object.entries(queue.data?.by_status ?? {}).map(([status, n]) => [`${status.toLowerCase()}`, n ?? 0] as [string, number])}
        />
      </div>

      <Section label="Traffic" title={summary ? `${count(summary.requests)} requests since the process started` : undefined} className="mb-8">
        {metrics.error && !metrics.data ? (
          <ErrorState error={metrics.error} onRetry={metrics.reload} title="Could not read /metrics" />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <StatTile label="Requests" loading={!summary} value={count(summary?.requests ?? 0)} detail="All routes, all methods" />
            <StatTile
              label="Server errors"
              loading={!summary}
              value={count(summary?.serverErrors ?? 0)}
              detail={summary && summary.requests ? `${percent(summary.serverErrors / summary.requests, 2)} of requests · ${summary.clientErrors} client errors` : "None"}
            />
            <StatTile label="Latency p50" loading={!summary} value={quantile(summary?.p50 ?? null)} detail="Estimated from histogram buckets" />
            <StatTile label="Latency p95" loading={!summary} value={quantile(summary?.p95 ?? null)} detail="Same estimate as histogram_quantile" />
          </div>
        )}
      </Section>

      <Section label="Routes" title="By route template, so a mission ID never mints a new series">
        <div className="panel overflow-x-auto">
          {!summary ? (
            <TableSkeleton rows={6} cols={5} />
          ) : summary.routes.length === 0 ? (
            <div className="p-4">
              <EmptyState compact title="No traffic yet" body="Counters start at zero when the API process starts." />
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Route</th>
                  <th>Methods</th>
                  <th className="text-right">Requests</th>
                  <th className="text-right">4xx</th>
                  <th className="text-right">5xx</th>
                  <th className="text-right">Mean</th>
                  <th className="text-right">p95</th>
                </tr>
              </thead>
              <tbody>
                {summary.routes.map((route) => (
                  <tr key={route.route}>
                    <td className="max-w-md truncate font-mono text-xs">
                      {route.route}
                      {route.route === "unmatched" && <span className="ml-2 font-sans text-muted">static files and unknown paths</span>}
                    </td>
                    <td className="font-mono text-2xs text-muted">{route.methods.join(" ")}</td>
                    <td className="num text-right">{count(route.requests)}</td>
                    <td className={`num text-right ${route.clientErrors ? "text-warning" : "text-subtle"}`}>{route.clientErrors}</td>
                    <td className={`num text-right ${route.serverErrors ? "text-danger" : "text-subtle"}`}>{route.serverErrors}</td>
                    <td className="num text-right text-muted">{ms(route.meanSeconds)}</td>
                    <td className="num whitespace-nowrap text-right">{quantile(route.p95)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
        {metrics.data && (
          <details className="mt-3 rounded-md border border-line">
            <summary className="cursor-pointer select-none px-3 py-2 font-mono text-2xs uppercase tracking-wider text-muted hover:text-fg">
              Raw exposition
            </summary>
            <pre className="max-h-80 overflow-auto border-t border-line px-3 py-2 font-mono text-2xs leading-5 text-muted">{metrics.data}</pre>
          </details>
        )}
      </Section>
    </div>
  );
}

function CheckCard({
  title,
  endpoint,
  ok,
  detail,
  items,
  note,
}: {
  title: string;
  endpoint: string;
  ok: boolean | null;
  detail: string;
  items: [string, boolean | number][];
  note?: string;
}) {
  return (
    <div className="panel flex flex-col gap-3 p-4">
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="label-tick">{title}</div>
          <div className="mt-1 font-mono text-2xs text-subtle">{endpoint}</div>
        </div>
        <Badge tone={ok === null ? "neutral" : ok ? "success" : "danger"} pulse={ok === null}>
          {ok === null ? "Checking" : ok ? "Healthy" : "Failing"}
        </Badge>
      </div>
      <p className="text-sm text-fg">{detail}</p>
      {items.length > 0 && (
        <ul className="divide-y divide-line border-t border-line text-sm">
          {items.map(([label, value]) => (
            <li key={label} className="flex h-9 items-center justify-between">
              <span className="capitalize text-muted">{label}</span>
              {typeof value === "boolean" ? (
                value ? (
                  <CircleCheck className="h-4 w-4 text-success" strokeWidth={1.5} aria-label="ok" />
                ) : (
                  <CircleX className="h-4 w-4 text-danger" strokeWidth={1.5} aria-label="failing" />
                )
              ) : (
                <span className="num text-fg">{value}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      {note && <p className="break-all font-mono text-2xs text-danger">{note}</p>}
    </div>
  );
}
