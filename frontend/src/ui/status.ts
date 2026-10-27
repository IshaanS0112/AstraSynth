/** One place that decides how every status in the system reads. */

import type { JobStatus, MissionStatus } from "../api/types";
import type { Tone } from "./Badge";

export const MISSION_STAGES: MissionStatus[] = [
  "PENDING",
  "ANALYZED",
  "PATH_PLANNED",
  "RISK_ASSESSED",
  "REPORT_GENERATED",
];

export const MISSION_STATUS: Record<MissionStatus, { label: string; tone: Tone }> = {
  PENDING: { label: "Awaiting analysis", tone: "neutral" },
  ANALYZED: { label: "Analysed", tone: "info" },
  PATH_PLANNED: { label: "Path planned", tone: "info" },
  RISK_ASSESSED: { label: "Risk assessed", tone: "warning" },
  REPORT_GENERATED: { label: "Reported", tone: "success" },
};

export const JOB_STATUS: Record<JobStatus, { label: string; tone: Tone; live?: boolean }> = {
  QUEUED: { label: "Queued", tone: "neutral", live: true },
  RUNNING: { label: "Running", tone: "info", live: true },
  CANCELLING: { label: "Cancelling", tone: "warning", live: true },
  SUCCEEDED: { label: "Succeeded", tone: "success" },
  FAILED: { label: "Failed", tone: "danger" },
  CANCELLED: { label: "Cancelled", tone: "neutral" },
};

/** Traverse and experiment outcomes share two states. */
export function outcomeTone(status: "SUCCESS" | "FAILED"): Tone {
  return status === "SUCCESS" ? "success" : "danger";
}

/** "ROVER_BLOCKED" -> "Rover blocked". */
export function humanize(code: string | null | undefined): string {
  if (!code) return "";
  const words = code.toLowerCase().replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}
