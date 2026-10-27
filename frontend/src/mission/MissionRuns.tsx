import { Link } from "react-router-dom";

import { api } from "../api/client";
import { KIND_LABEL, dateTime, duration, kwh, metres, relativeTime } from "../lib/format";
import { useAsync } from "../lib/useAsync";
import Badge from "../ui/Badge";
import { Section } from "../ui/PageHeader";
import { EmptyState, ErrorState, TableSkeleton } from "../ui/States";
import { JOB_STATUS, humanize, outcomeTone } from "../ui/status";

/** Everything that has been run against one mission, newest first. */
export default function MissionRuns({ missionId }: { missionId: string }) {
  const { data, error, loading, reload } = useAsync(
    async () => {
      const [traverses, experiments, jobs] = await Promise.all([
        api.listTraverses(missionId),
        api.listExperiments(missionId),
        api.listMissionJobs(missionId),
      ]);
      const newest = <T extends { created_at: string }>(list: T[]) => [...list].sort((a, b) => b.created_at.localeCompare(a.created_at));
      return { traverses: newest(traverses), experiments: newest(experiments), jobs: newest(jobs) };
    },
    [missionId],
    10000,
  );

  if (error && !data) return <ErrorState error={error} onRetry={reload} title="Could not load this mission's runs" />;

  const control = `/missions/${missionId}/control`;

  return (
    <div className="space-y-8">
      <Section
        label="Simulated traverses"
        title={
          data?.traverses.length
            ? `${data.traverses.filter((run) => run.status === "SUCCESS").length} of ${data.traverses.length} reached the goal`
            : undefined
        }
      >
        <div className="panel overflow-x-auto">
          {loading && !data ? (
            <TableSkeleton rows={4} cols={6} />
          ) : !data?.traverses.length ? (
            <div className="p-4">
              <EmptyState
                compact
                title="No traverses yet"
                body="Simulate one in mission control: the rover drives the plan against terrain it can only partly see."
                action={
                  <Link to={control} className="btn-primary">
                    Open mission control
                  </Link>
                }
              />
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Outcome</th>
                  <th className="text-right">Distance</th>
                  <th className="text-right">Energy</th>
                  <th className="text-right">Replans</th>
                  <th className="text-right">Reroutes</th>
                  <th className="text-right">Sim time</th>
                  <th className="text-right">When</th>
                </tr>
              </thead>
              <tbody>
                {data.traverses.map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Badge tone={outcomeTone(run.status)}>
                        {run.status === "SUCCESS" ? "Reached goal" : humanize(run.failure_mode) || "Failed"}
                      </Badge>
                    </td>
                    <td className="num text-right">{metres(run.distance_m)}</td>
                    <td className="num text-right">{kwh(run.energy_kwh)}</td>
                    <td className="num text-right">{run.replans}</td>
                    <td className="num text-right">{run.reroutes}</td>
                    <td className="num text-right">{duration(run.elapsed_seconds)}</td>
                    <td className="whitespace-nowrap text-right text-xs text-muted" title={dateTime(run.created_at)}>
                      {relativeTime(run.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </Section>

      <div className="grid gap-8 xl:grid-cols-2">
        <Section label="Studies">
          <div className="panel overflow-x-auto">
            {loading && !data ? (
              <TableSkeleton rows={4} cols={4} />
            ) : !data?.experiments.length ? (
              <div className="p-4">
                <EmptyState compact title="No studies yet" body="Route, robustness, fleet and science studies run from mission control." />
              </div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Study</th>
                    <th>Outcome</th>
                    <th className="text-right">Seed</th>
                    <th className="text-right">Runtime</th>
                    <th className="text-right">When</th>
                  </tr>
                </thead>
                <tbody>
                  {data.experiments.map((exp) => (
                    <tr key={exp.id}>
                      <td>
                        <Link to={`/studies?experiment=${exp.id}&mission=${missionId}`} className="font-medium hover:text-accent">
                          {KIND_LABEL[exp.kind] ?? exp.kind}
                        </Link>
                      </td>
                      <td>
                        <Badge tone={outcomeTone(exp.status)}>{exp.status === "SUCCESS" ? "Succeeded" : humanize(exp.failure_mode) || "Failed"}</Badge>
                      </td>
                      <td className="num text-right text-muted">{exp.seed ?? "—"}</td>
                      <td className="num text-right">{duration(exp.runtime_seconds)}</td>
                      <td className="whitespace-nowrap text-right text-xs text-muted" title={dateTime(exp.created_at)}>
                        {relativeTime(exp.created_at)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </Section>

        <Section
          label="Queued jobs"
          action={
            <Link to="/jobs" className="text-xs text-muted transition-colors duration-120 hover:text-fg">
              Queue
            </Link>
          }
        >
          <div className="panel overflow-x-auto">
            {loading && !data ? (
              <TableSkeleton rows={3} cols={4} />
            ) : !data?.jobs.length ? (
              <div className="p-4">
                <EmptyState
                  compact
                  title="Nothing queued"
                  body="Large robustness studies go to the background queue."
                  action={
                    <Link to="/jobs?new=1" className="btn-ghost">
                      Queue a study
                    </Link>
                  }
                />
              </div>
            ) : (
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Job</th>
                    <th>Status</th>
                    <th className="text-right">Progress</th>
                    <th className="text-right">Submitted</th>
                  </tr>
                </thead>
                <tbody>
                  {data.jobs.map((job) => {
                    const status = JOB_STATUS[job.status] ?? { label: job.status, tone: "neutral" as const };
                    return (
                      <tr key={job.id}>
                        <td className="font-medium">{KIND_LABEL[job.kind] ?? job.kind}</td>
                        <td>
                          <Badge tone={status.tone} pulse={Boolean(status.live)}>
                            {status.label}
                          </Badge>
                        </td>
                        <td className="num text-right">{Math.round(job.progress * 100)}%</td>
                        <td className="whitespace-nowrap text-right text-xs text-muted" title={dateTime(job.created_at)}>
                          {relativeTime(job.created_at)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </Section>
      </div>
    </div>
  );
}
