import { useEffect, useState } from "react";

import { api } from "../api/client";

export interface ApiStatus {
  state: "checking" | "up" | "down";
  /** Round trip of the last liveness probe, through the dev proxy or nginx. */
  latencyMs: number | null;
  checkedAt: number | null;
}

/** Probe /health on an interval. Liveness only: it says the process answers, not that it is ready. */
export function useApiStatus(intervalMs = 15000): ApiStatus {
  const [status, setStatus] = useState<ApiStatus>({ state: "checking", latencyMs: null, checkedAt: null });

  useEffect(() => {
    let cancelled = false;
    const probe = async () => {
      if (document.hidden) return;
      const started = performance.now();
      try {
        await api.health();
        if (!cancelled) setStatus({ state: "up", latencyMs: performance.now() - started, checkedAt: Date.now() });
      } catch {
        if (!cancelled) setStatus({ state: "down", latencyMs: null, checkedAt: Date.now() });
      }
    };
    probe();
    const id = window.setInterval(probe, intervalMs);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [intervalMs]);

  return status;
}
