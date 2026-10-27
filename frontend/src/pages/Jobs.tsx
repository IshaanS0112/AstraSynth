import { Plus } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { ApiError, api } from "../api/client";
import type { JobSummary, Mission, RoverConfig } from "../api/types";
import { KIND_LABEL, count, dateTime, duration, relativeTime } from "../lib/format";
import { loadActivity } from "../lib/missions";
import { useAsync } from "../lib/useAsync";
import Badge from "../ui/Badge";
import { ProgressBar } from "../ui/Bars";
import Modal from "../ui/Modal";
import PageHeader from "../ui/PageHeader";
import StatTile from "../ui/StatTile";
import { EmptyState, ErrorState, TableSkeleton } from "../ui/States";
import { JOB_STATUS } from "../ui/status";
import { useToast } from "../ui/Toast";

interface Row extends JobSummary {
  mission: Mission;
}

async function loadJobs() {
  const [missions, queue] = await Promise.all([api.listMissions(), api.queueStats()]);
  const activity = await loadActivity(missions, { jobs: true });
  const rows: Row[] = activity
    .flatMap(({ mission, jobs }) => jobs.map((job) => ({ ...job, mission })))
    .sort((a, b) => b.created_at.localeCompare(a.created_at));
  return { missions, queue, rows };
}

export default function Jobs() {
  const notify = useToast();
  const [params, setParams] = useSearchParams();
  const [cancelling, setCancelling] = useState<string | null>(null);
  const { data, error, loading, reload } = useAsync(loadJobs, [], 3000);
  const queueing = params.get("new") === "1";
  const setQueueing = (open: boolean) => setParams(open ? { new: "1" } : {}, { replace: true });

  const by = data?.queue.by_status ?? {};
  const live = (data?.rows ?? []).filter((row) => JOB_STATUS[row.status]?.live).length;

  const cancel = async (row: Row) => {
    setCancelling(row.id);
    try {
      await api.cancelJob(row.id);
      notify({ title: "Cancellation requested", body: `${KIND_LABEL[row.kind] ?? row.kind} on ${row.mission.name} stops at its next checkpoint.` });
      reload();
    } catch (exc) {
      notify({
        title: exc instanceof ApiError && exc.status === 409 ? "Already finished" : "Could not cancel",
        body: exc instanceof Error ? exc.message : String(exc),
        tone: "danger",
      });
    } finally {
      setCancelling(null);
    }
  };

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 lg:px-8 lg:py-8">
      <PageHeader
        title="Jobs"
        description="Long studies run on the background queue: a Postgres table that in-process or standalone workers claim from. Refreshes every few seconds."
        actions={
          <button className="btn-primary" onClick={() => setQueueing(true)}>
            <Plus className="h-4 w-4" strokeWidth={1.5} aria-hidden="true" />
            Queue robustness study
          </button>
        }
      />

      <div className="mb-8 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Queued"
          loading={loading && !data}
          value={count(by.QUEUED ?? 0)}
          detail={data?.queue.oldest_queued_seconds != null ? `Oldest waiting ${duration(data.queue.oldest_queued_seconds)}` : "Nothing waiting"}
        />
        <StatTile label="Running" loading={loading && !data} value={count((by.RUNNING ?? 0) + (by.CANCELLING ?? 0))} detail={live ? `${live} live on this page` : "Idle"} />
        <StatTile
          label="Finished"
          loading={loading && !data}
          value={count((by.SUCCEEDED ?? 0) + (by.FAILED ?? 0) + (by.CANCELLED ?? 0))}
          detail={`${by.SUCCEEDED ?? 0} succeeded · ${by.FAILED ?? 0} failed · ${by.CANCELLED ?? 0} cancelled`}
        />
        <StatTile
          label="Workers"
          loading={loading && !data}
          value={count(data?.queue.in_process_workers ?? 0)}
          unit="in process"
          detail={data?.queue.in_process_workers ? "Claiming with SKIP LOCKED" : "Run python -m app.worker to consume"}
        />
      </div>

      {error && !data ? (
        <ErrorState error={error} onRetry={reload} title="Could not load the queue" />
      ) : loading && !data ? (
        <div className="panel overflow-hidden">
          <TableSkeleton rows={6} cols={6} />
        </div>
      ) : !data?.rows.length ? (
        <EmptyState
          title="The queue is empty"
          body="Interactive studies in mission control run inline. Queue a larger robustness study here and watch a worker pick it up."
          action={
            <button className="btn-primary" onClick={() => setQueueing(true)}>
              Queue robustness study
            </button>
          }
        />
      ) : (
        <div className="panel overflow-x-auto">
          <table className="tbl">
            <thead>
              <tr>
                <th>Job</th>
                <th>Mission</th>
                <th>Status</th>
                <th className="w-56">Progress</th>
                <th className="text-right">Submitted</th>
                <th className="text-right">Took</th>
                <th className="text-right" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row) => {
                const status = JOB_STATUS[row.status] ?? { label: row.status, tone: "neutral" as const };
                const took = row.finished_at ? (Date.parse(row.finished_at) - Date.parse(row.created_at)) / 1000 : null;
                return (
                  <tr key={row.id}>
                    <td>
                      <div className="font-medium">{KIND_LABEL[row.kind] ?? row.kind}</div>
                      <div className="font-mono text-2xs text-subtle">{row.id.slice(0, 8)}</div>
                    </td>
                    <td className="max-w-48 truncate text-muted">{row.mission.name}</td>
                    <td>
                      <Badge tone={status.tone} pulse={Boolean(status.live)}>
                        {status.label}
                      </Badge>
                    </td>
                    <td>
                      {row.status === "SUCCEEDED" || row.status === "FAILED" || row.status === "CANCELLED" ? (
                        <span className="text-xs text-muted">{row.progress_detail ?? status.label}</span>
                      ) : (
                        <div className="space-y-1">
                          <ProgressBar value={row.progress} />
                          <div className="flex justify-between gap-2 text-2xs text-muted">
                            <span className="truncate">{row.progress_detail ?? (row.status === "QUEUED" ? "Waiting for a worker" : "Starting")}</span>
                            <span className="num shrink-0">{Math.round(row.progress * 100)}%</span>
                          </div>
                        </div>
                      )}
                    </td>
                    <td className="whitespace-nowrap text-right text-xs text-muted" title={dateTime(row.created_at)}>
                      {relativeTime(row.created_at)}
                    </td>
                    <td className="num text-right text-muted">{took !== null ? duration(took) : "—"}</td>
                    <td className="whitespace-nowrap text-right">
                      {row.experiment_id ? (
                        <Link
                          to={`/studies?experiment=${row.experiment_id}&mission=${row.mission.id}`}
                          className="btn-ghost h-8 px-3 text-xs lg:h-8"
                        >
                          View result
                        </Link>
                      ) : row.status === "QUEUED" || row.status === "RUNNING" ? (
                        <button
                          className="btn h-8 border border-danger/30 px-3 text-xs text-danger hover:bg-danger/10 lg:h-8"
                          onClick={() => cancel(row)}
                          disabled={cancelling === row.id}
                        >
                          {cancelling === row.id ? "Cancelling…" : "Cancel"}
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      <QueueStudy
        open={queueing}
        missions={(data?.missions ?? []).filter((mission) => mission.status !== "PENDING")}
        onClose={() => setQueueing(false)}
        onQueued={() => {
          setQueueing(false);
          reload();
        }}
      />
    </div>
  );
}

function QueueStudy({
  open,
  missions,
  onClose,
  onQueued,
}: {
  open: boolean;
  missions: Mission[];
  onClose: () => void;
  onQueued: () => void;
}) {
  const notify = useToast();
  const rovers = useAsync<RoverConfig[]>(() => (open ? api.listRoverConfigs() : Promise.resolve([])), [open]);
  const [missionId, setMissionId] = useState("");
  const [roverId, setRoverId] = useState("");
  const [trials, setTrials] = useState("200");
  const [seed, setSeed] = useState("0");
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setProblem(null);
    setMissionId((current) => current || missions[0]?.id || "");
  }, [open, missions]);

  useEffect(() => {
    setRoverId((current) => current || rovers.data?.[0]?.id || "");
  }, [rovers.data]);

  const trialCount = Number(trials);
  const valid = useMemo(
    () => Boolean(missionId && roverId) && Number.isInteger(trialCount) && trialCount >= 1 && trialCount <= 5000 && Number.isInteger(Number(seed)) && Number(seed) >= 0,
    [missionId, roverId, trialCount, seed],
  );

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!valid) return;
    setSaving(true);
    setProblem(null);
    try {
      // The same corner-to-corner endpoints mission control starts with, so a
      // queued study is comparable with a traverse simulated there.
      const grid = await api.getTerrainGrid(missionId, 128, roverId);
      const edge = Math.max(2, Math.round(grid.rows * 0.05));
      const px = (cells: number) => Math.round(cells * grid.pixel_scale);
      const job = await api.submitMonteCarloJob(missionId, {
        start: { x: px(edge), y: px(edge) },
        goal: { x: px(grid.cols - 1 - edge), y: px(grid.rows - 1 - edge) },
        rover_config_id: roverId,
        trials: trialCount,
        seed: Number(seed),
      });
      notify({ title: "Robustness study queued", body: `${trialCount} trials · job ${job.id.slice(0, 8)}`, tone: "success" });
      onQueued();
    } catch (exc) {
      setProblem(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Queue robustness study"
      description="Monte Carlo over perturbed terrain, unmapped obstacles and energy draw, from the default start to the default goal."
      footer={
        <>
          <button type="button" className="btn-ghost" onClick={onClose}>
            Cancel
          </button>
          <button type="submit" form="queue-form" className="btn-primary" disabled={!valid || saving}>
            {saving ? "Queueing…" : "Queue study"}
          </button>
        </>
      }
    >
      {missions.length === 0 ? (
        <EmptyState compact title="No analysed missions" body="A study needs an analysed mission. Analyse one first." />
      ) : (
        <form id="queue-form" onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className="label" htmlFor="queue-mission">
              Mission
            </label>
            <select id="queue-mission" className="field" value={missionId} onChange={(event) => setMissionId(event.target.value)} data-autofocus>
              {missions.map((mission) => (
                <option key={mission.id} value={mission.id}>
                  {mission.name}
                </option>
              ))}
            </select>
          </div>
          <div className="sm:col-span-2">
            <label className="label" htmlFor="queue-rover">
              Rover
            </label>
            <select id="queue-rover" className="field" value={roverId} onChange={(event) => setRoverId(event.target.value)}>
              {(rovers.data ?? []).map((rover) => (
                <option key={rover.id} value={rover.id}>
                  {rover.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="label" htmlFor="queue-trials">
              Trials
            </label>
            <input id="queue-trials" className="field" inputMode="numeric" value={trials} onChange={(event) => setTrials(event.target.value)} />
            <p className="mt-1 text-2xs text-subtle">1 to 5,000. Inline studies stop at 500.</p>
          </div>
          <div>
            <label className="label" htmlFor="queue-seed">
              Seed
            </label>
            <input id="queue-seed" className="field" inputMode="numeric" value={seed} onChange={(event) => setSeed(event.target.value)} />
            <p className="mt-1 text-2xs text-subtle">Same seed, same trials, same answer.</p>
          </div>
          {problem && (
            <p role="alert" className="rounded-md border border-danger/30 bg-danger/5 px-3 py-2 text-xs text-danger sm:col-span-2">
              {problem}
            </p>
          )}
        </form>
      )}
    </Modal>
  );
}
