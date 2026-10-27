import { ArrowRight, Plus } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import type { Mission, TraverseRunSummary } from "../api/types";
import { KIND_SHORT, count, duration, kwh, metres, percent, relativeTime } from "../lib/format";
import { loadActivity } from "../lib/missions";
import { useAsync } from "../lib/useAsync";
import { useApiStatus } from "../shell/useApiStatus";
import Badge from "../ui/Badge";
import { OutcomeBar, StageBar } from "../ui/Bars";
import PageHeader, { Section } from "../ui/PageHeader";
import ReliefImage from "../ui/ReliefImage";
import StatTile from "../ui/StatTile";
import { EmptyState, ErrorState, TableSkeleton } from "../ui/States";
import { MISSION_STATUS, humanize, outcomeTone } from "../ui/status";

interface RunRow extends TraverseRunSummary {
  mission: Mission;
}

async function loadOverview() {
  const [missions, rovers, queue] = await Promise.all([api.listMissions(), api.listRoverConfigs(), api.queueStats()]);
  const activity = await loadActivity(missions, { traverses: true, experiments: true });
  const runs: RunRow[] = activity
    .flatMap(({ mission, traverses }) => traverses.map((run) => ({ ...run, mission })))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  const experiments = activity.flatMap(({ experiments: list }) => list);
  return { missions, rovers, queue, runs, experiments };
}

export default function Overview() {
  const navigate = useNavigate();
  const { data, error, loading, reload } = useAsync(loadOverview, [], 20000);
  const apiStatus = useApiStatus(20000);
  const ready = useAsync(() => api.ready(), [], 30000);

  const missions = data?.missions ?? [];
  const runs = data?.runs ?? [];
  const reached = runs.filter((run) => run.status === "SUCCESS").length;
  const stopped = runs.length - reached;
  const analysed = missions.filter((mission) => mission.status !== "PENDING");
  const latest = analysed[0];
  const byKind = (data?.experiments ?? []).reduce<Record<string, number>>((acc, exp) => {
    acc[exp.kind] = (acc[exp.kind] ?? 0) + 1;
    return acc;
  }, {});
  const active = (data?.queue.by_status.QUEUED ?? 0) + (data?.queue.by_status.RUNNING ?? 0);
  const recent = runs.slice(0, 8);

  const claim =
    runs.length > 0
      ? `${reached} of ${runs.length} simulated traverses reached the goal`
      : missions.length > 0
        ? "No traverses simulated yet"
        : "Start with a terrain tile";

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="Overview"
        description="Every mission, simulated traverse, study and queued job, in one place."
        actions={
          <Link to="/missions/new" className="btn-primary">
            <Plus className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
            New mission
          </Link>
        }
      />

      {error && !data && (
        <div className="mb-6">
          <ErrorState error={error} onRetry={reload} title="Could not load the overview" />
        </div>
      )}

      {/* Hero: the claim on the left, the rendered rover on the right. */}
      <section className="panel mb-6 grid overflow-hidden lg:grid-cols-12">
        <div className="order-2 flex flex-col justify-center gap-4 p-6 lg:order-1 lg:col-span-5 lg:p-8">
          <div className="label-tick">Mission operations</div>
          {loading && !data ? (
            <div className="space-y-2">
              <div className="skeleton h-8 w-4/5" />
              <div className="skeleton h-8 w-3/5" />
            </div>
          ) : (
            <h2 className="text-2xl font-semibold leading-tight tracking-tight text-fg lg:text-3xl">{claim}</h2>
          )}
          <p className="text-sm leading-6 text-muted">
            {count(missions.length)} {missions.length === 1 ? "mission" : "missions"} and {count(data?.rovers.length ?? 0)} rover
            configurations. D* Lite repairs the plan when the rover meets ground it could not see from orbit. Every
            figure is computed; the language model only narrates.
          </p>
          <div className="flex flex-wrap gap-2">
            {latest ? (
              <Link to={`/missions/${latest.id}/control`} className="btn-ghost">
                Open {latest.name}
                <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
              </Link>
            ) : (
              <Link to="/missions/new" className="btn-ghost">
                Upload terrain
                <ArrowRight className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
              </Link>
            )}
            <Link to="/missions" className="btn-ghost">
              All missions
            </Link>
          </div>
        </div>
        <figure className="relative order-1 aspect-video border-b border-line lg:order-2 lg:col-span-7 lg:aspect-auto lg:min-h-80 lg:border-b-0 lg:border-l">
          <img
            src="/3d/hero.jpg"
            alt="The AstraSynth rover on Martian regolith, rendered in Blender"
            className="absolute inset-0 h-full w-full object-cover"
          />
          <figcaption className="absolute bottom-3 left-3 rounded border border-line bg-canvas/80 px-2 py-1 font-mono text-2xs uppercase tracking-wider text-muted">
            Blender · Cycles render
          </figcaption>
        </figure>
      </section>

      {/* KPIs: four, each against what it is measured. */}
      <div className="mb-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Missions"
          loading={loading && !data}
          value={count(missions.length)}
          detail={`${analysed.length} analysed · ${missions.length - analysed.length} awaiting analysis`}
        />
        <StatTile
          label="Traverses simulated"
          loading={loading && !data}
          value={count(runs.length)}
          detail={runs.length ? `${percent(reached / runs.length)} reached the goal` : "None yet"}
        >
          <OutcomeBar success={reached} failed={stopped} />
        </StatTile>
        <StatTile
          label="Studies"
          loading={loading && !data}
          value={count(data?.experiments.length ?? 0)}
          detail={
            Object.keys(byKind).length
              ? Object.entries(byKind)
                  .sort((a, b) => b[1] - a[1])
                  .map(([kind, n]) => `${n} ${KIND_SHORT[kind] ?? kind}`)
                  .join(" · ")
              : "Route, robustness, fleet, science"
          }
        />
        <StatTile
          label="Job queue"
          loading={loading && !data}
          value={count(active)}
          unit="active"
          detail={
            data
              ? `${data.queue.in_process_workers} in-process ${data.queue.in_process_workers === 1 ? "worker" : "workers"}${
                  data.queue.oldest_queued_seconds !== null ? ` · oldest ${duration(data.queue.oldest_queued_seconds)}` : ""
                }`
              : ""
          }
        />
      </div>

      <div className="mb-8 grid gap-6 lg:grid-cols-12">
        <Section
          label="Recent traverses"
          className="min-w-0 lg:col-span-8"
          title={
            recent.length
              ? `Last ${recent.length}: ${recent.filter((r) => r.status === "SUCCESS").length} reached the goal, ${
                  recent.filter((r) => r.status === "FAILED").length
                } stopped short`
              : undefined
          }
          action={
            <Link to="/missions" className="text-xs text-muted transition-colors duration-120 hover:text-fg">
              All missions
            </Link>
          }
        >
          <div className="panel overflow-hidden">
            {loading && !data ? (
              <TableSkeleton rows={6} cols={6} />
            ) : recent.length === 0 ? (
              <div className="p-4">
                <EmptyState
                  compact
                  title="No traverses yet"
                  body="Open a mission in mission control, pick a start and goal, and simulate a traverse. Runs land here."
                  action={
                    latest ? (
                      <Link to={`/missions/${latest.id}/control`} className="btn-primary">
                        Open mission control
                      </Link>
                    ) : undefined
                  }
                />
              </div>
            ) : (
              <>
              {/* Seven columns do not fit a phone: there each run is two lines. */}
              <ul className="divide-y divide-line md:hidden">
                {recent.map((run) => (
                  <li key={run.id}>
                    <Link to={`/missions/${run.mission.id}/control`} className="block space-y-1.5 px-4 py-3">
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm text-fg">{run.mission.name}</span>
                        <Badge tone={outcomeTone(run.status)}>
                          {run.status === "SUCCESS" ? "Reached goal" : humanize(run.failure_mode) || "Failed"}
                        </Badge>
                      </div>
                      <div className="flex items-center justify-between gap-3 text-xs text-muted">
                        <span className="num truncate">
                          {metres(run.distance_m)} · {kwh(run.energy_kwh)} · {run.replans} replans
                        </span>
                        <span className="shrink-0 text-subtle">{relativeTime(run.created_at)}</span>
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
              <div className="hidden overflow-x-auto md:block">
                <table className="tbl">
                  <thead>
                    <tr>
                      <th>Mission</th>
                      <th>Outcome</th>
                      <th className="text-right">Distance</th>
                      <th className="text-right">Energy</th>
                      <th className="text-right">Replans</th>
                      <th className="text-right">Sim time</th>
                      <th className="text-right">When</th>
                    </tr>
                  </thead>
                  <tbody>
                    {recent.map((run) => (
                      <tr
                        key={run.id}
                        className="cursor-pointer"
                        onClick={() => navigate(`/missions/${run.mission.id}/control`)}
                      >
                        <td className="max-w-48">
                          <Link
                            to={`/missions/${run.mission.id}/control`}
                            onClick={(event) => event.stopPropagation()}
                            className="block truncate hover:text-accent"
                          >
                            {run.mission.name}
                          </Link>
                        </td>
                        <td>
                          <Badge tone={outcomeTone(run.status)} title={run.failure_mode ?? undefined}>
                            {run.status === "SUCCESS" ? "Reached goal" : humanize(run.failure_mode) || "Failed"}
                          </Badge>
                        </td>
                        <td className="num text-right">{metres(run.distance_m)}</td>
                        <td className="num text-right">{kwh(run.energy_kwh)}</td>
                        <td className="num text-right">{run.replans}</td>
                        <td className="num text-right">{duration(run.elapsed_seconds)}</td>
                        <td className="whitespace-nowrap text-right text-xs text-muted" title={run.created_at}>
                          {relativeTime(run.created_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              </>
            )}
          </div>
        </Section>

        <div className="flex min-w-0 flex-col gap-6 lg:col-span-4">
          <Section
            label="Fleet"
            action={
              <Link to="/fleet" className="text-xs text-muted transition-colors duration-120 hover:text-fg">
                Manage
              </Link>
            }
          >
            <div className="panel overflow-hidden">
              <div className="dot-grid flex h-32 items-center justify-center border-b border-line">
                <img src="/3d/rover-studio.webp" alt="" className="h-28 w-auto object-contain" />
              </div>
              <ul className="divide-y divide-line">
                {(data?.rovers ?? []).slice(0, 4).map((rover) => (
                  <li key={rover.id} className="flex h-10 items-center justify-between gap-3 px-4 text-sm">
                    <span className="truncate text-fg">{rover.name}</span>
                    <span className="num shrink-0 text-xs text-muted">
                      {rover.battery_capacity_kwh} kWh · ≤{rover.max_traversable_slope_deg}°
                    </span>
                  </li>
                ))}
                {loading && !data && (
                  <li className="px-4 py-3">
                    <div className="skeleton h-4 w-full" />
                  </li>
                )}
              </ul>
            </div>
          </Section>

          <Section
            label="System"
            action={
              <Link to="/system" className="text-xs text-muted transition-colors duration-120 hover:text-fg">
                Details
              </Link>
            }
          >
            <dl className="panel divide-y divide-line text-sm">
              <StatusRow
                label="API"
                ok={apiStatus.state === "up" ? true : apiStatus.state === "down" ? false : null}
                value={apiStatus.latencyMs !== null ? `${Math.round(apiStatus.latencyMs)} ms` : apiStatus.state}
              />
              <StatusRow
                label="Database"
                ok={ready.data ? ready.data.database : ready.error ? false : null}
                value={ready.data?.database ? "ready" : ready.error ? "not ready" : "…"}
              />
              <StatusRow
                label="Workers"
                ok={data ? data.queue.in_process_workers > 0 : null}
                value={data ? `${data.queue.in_process_workers} in process` : "…"}
              />
            </dl>
          </Section>
        </div>
      </div>

      <Section
        label="Missions"
        title={missions.length ? `${analysed.length} of ${missions.length} analysed and ready for mission control` : undefined}
        action={
          <Link to="/missions" className="text-xs text-muted transition-colors duration-120 hover:text-fg">
            View all
          </Link>
        }
      >
        {loading && !data ? (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div key={i} className="panel overflow-hidden">
                <div className="skeleton h-32 rounded-none" />
                <div className="space-y-2 p-4">
                  <div className="skeleton h-4 w-2/3" />
                  <div className="skeleton h-3 w-1/2" />
                </div>
              </div>
            ))}
          </div>
        ) : missions.length === 0 ? (
          <EmptyState
            title="No missions yet"
            body="A mission is one terrain tile: upload a grayscale DEM and AstraSynth analyses slope, hazard and obstacles. Sample tiles are in data/sample_terrain/."
            action={
              <Link to="/missions/new" className="btn-primary">
                New mission
              </Link>
            }
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {missions.slice(0, 6).map((mission) => (
              <MissionCard key={mission.id} mission={mission} />
            ))}
          </div>
        )}
      </Section>
    </div>
  );
}

function StatusRow({ label, ok, value }: { label: string; ok: boolean | null; value: string }) {
  return (
    <div className="flex h-10 items-center justify-between px-4">
      <dt className="flex items-center gap-2 text-muted">
        <span
          className={`h-2 w-2 rounded-full ${ok === null ? "bg-subtle" : ok ? "bg-success" : "bg-danger"}`}
          aria-hidden="true"
        />
        {label}
      </dt>
      <dd className="num text-xs text-fg">{value}</dd>
    </div>
  );
}

export function MissionCard({ mission }: { mission: Mission }) {
  const status = MISSION_STATUS[mission.status];
  const ready = mission.status !== "PENDING";
  return (
    <Link
      to={ready ? `/missions/${mission.id}/control` : `/missions/${mission.id}`}
      className="panel group overflow-hidden transition-colors duration-120 hover:border-line-strong"
    >
      <div className="relative h-32 overflow-hidden border-b border-line bg-surface-2">
        <ReliefImage
          src={mission.terrain_image_url}
          className="h-full w-full opacity-90 transition-opacity duration-180 group-hover:opacity-100"
        />
        <div className="absolute left-3 top-3">
          <Badge tone={status?.tone ?? "neutral"}>{status?.label ?? mission.status}</Badge>
        </div>
      </div>
      <div className="space-y-3 p-4">
        <div>
          <h3 className="truncate text-sm font-medium text-fg group-hover:text-accent">{mission.name}</h3>
          <p className="mt-0.5 truncate text-xs text-muted">
            {mission.terrain_source || "Source unspecified"} · {relativeTime(mission.created_at)}
          </p>
        </div>
        <StageBar status={mission.status} />
      </div>
    </Link>
  );
}
