/** Formatting for the console: units always printed, numbers never rounded silently. */

const rtf = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

/** "3 min ago", "yesterday", "12 Oct". */
export function relativeTime(iso: string, now = Date.now()): string {
  const then = new Date(iso).getTime();
  const seconds = Math.round((then - now) / 1000);
  const abs = Math.abs(seconds);
  if (abs < 45) return "just now";
  if (abs < 60 * 45) return rtf.format(Math.round(seconds / 60), "minute");
  if (abs < 3600 * 20) return rtf.format(Math.round(seconds / 3600), "hour");
  if (abs < 86400 * 6) return rtf.format(Math.round(seconds / 86400), "day");
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}

export function dateTime(iso: string): string {
  return new Date(iso).toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** A duration in seconds as "7.5 h", "14 min", "42 s". */
export function duration(seconds: number): string {
  if (!Number.isFinite(seconds)) return "—";
  if (seconds >= 3600) return `${(seconds / 3600).toFixed(1)} h`;
  if (seconds >= 60) return `${Math.round(seconds / 60)} min`;
  return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
}

export function metres(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

export function kwh(value: number, digits = 2): string {
  return `${value.toFixed(digits)} kWh`;
}

export function percent(fraction: number, digits = 0): string {
  return `${(fraction * 100).toFixed(digits)}%`;
}

export function count(n: number): string {
  return n.toLocaleString("en-GB");
}

/** Experiment kinds as an operator would say them. */
export const KIND_LABEL: Record<string, string> = {
  route_study: "Route study",
  monte_carlo: "Robustness study",
  fleet_plan: "Fleet deconfliction",
  science_tour: "Science tour",
};

/** The same, in one word, for counts like "8 route · 4 fleet". */
export const KIND_SHORT: Record<string, string> = {
  route_study: "route",
  monte_carlo: "robustness",
  fleet_plan: "fleet",
  science_tour: "science",
};
