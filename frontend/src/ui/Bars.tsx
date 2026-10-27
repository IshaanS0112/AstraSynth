import type { MissionStatus } from "../api/types";
import { MISSION_STAGES, MISSION_STATUS } from "./status";

/** The five-stage pipeline a mission moves through, filled up to where it is. */
export function StageBar({ status, className = "" }: { status: MissionStatus; className?: string }) {
  const reached = MISSION_STAGES.indexOf(status);
  return (
    <div
      className={`flex items-center gap-1 ${className}`}
      role="img"
      aria-label={`Pipeline: ${MISSION_STATUS[status]?.label ?? status}, stage ${reached + 1} of ${MISSION_STAGES.length}`}
    >
      {MISSION_STAGES.map((stage, index) => (
        <span
          key={stage}
          title={MISSION_STATUS[stage].label}
          className={`h-1 flex-1 rounded-full ${index <= reached ? "bg-accent" : "bg-line-strong"}`}
        />
      ))}
    </div>
  );
}

/** Two outcomes as one bar: how much of the whole went each way. */
export function OutcomeBar({ success, failed }: { success: number; failed: number }) {
  const total = success + failed;
  if (total === 0) return <div className="h-1.5 rounded-full bg-line-strong" />;
  return (
    <div
      className="flex h-1.5 gap-0.5 overflow-hidden rounded-full"
      role="img"
      aria-label={`${success} succeeded, ${failed} failed`}
    >
      {success > 0 && <span className="bg-success" style={{ flexGrow: success }} />}
      {failed > 0 && <span className="bg-danger" style={{ flexGrow: failed }} />}
    </div>
  );
}

/** A thin progress bar for a running job. */
export function ProgressBar({ value, tone = "info" }: { value: number; tone?: "info" | "success" | "danger" }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const fill = tone === "success" ? "bg-success" : tone === "danger" ? "bg-danger" : "bg-info";
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-line-strong"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
    >
      <div className={`h-full rounded-full ${fill} transition-[width] duration-180`} style={{ width: `${pct}%` }} />
    </div>
  );
}
