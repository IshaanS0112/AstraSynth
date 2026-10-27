import { CircleAlert, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { ApiError } from "../api/client";

/** What an area will show, and the one action that fills it. */
export function EmptyState({
  title,
  body,
  action,
  icon,
  compact,
}: {
  title: string;
  body?: ReactNode;
  action?: ReactNode;
  icon?: ReactNode;
  compact?: boolean;
}) {
  return (
    <div
      className={`dot-grid flex flex-col items-center justify-center rounded-lg border border-dashed border-line-strong text-center ${
        compact ? "px-4 py-8" : "px-6 py-14"
      }`}
    >
      {icon && <div className="mb-3 text-subtle">{icon}</div>}
      <p className="text-sm font-medium text-fg">{title}</p>
      {body && <p className="mt-1 max-w-sm text-xs leading-5 text-muted">{body}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

/** What went wrong in plain words, a retry, and the code for whoever has to look into it. */
export function ErrorState({
  error,
  onRetry,
  title = "Could not load this",
}: {
  error: Error;
  onRetry?: () => void;
  title?: string;
}) {
  const status = error instanceof ApiError ? error.status : null;
  const unreachable = error instanceof TypeError || status === 502 || status === 504;
  return (
    <div role="alert" className="rounded-lg border border-danger/30 bg-danger/5 p-4">
      <div className="flex items-start gap-3">
        <CircleAlert className="mt-0.5 h-4 w-4 shrink-0 text-danger" strokeWidth={1.5} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-fg">{title}</p>
          <p className="mt-0.5 text-xs leading-5 text-muted">
            {unreachable
              ? "The API did not answer. Check that the backend is running on port 8000."
              : error.message}
          </p>
          <details className="mt-2 text-2xs text-subtle">
            <summary className="cursor-pointer select-none font-mono uppercase tracking-wider hover:text-muted">
              Details
            </summary>
            <pre className="mt-1 whitespace-pre-wrap break-all font-mono text-muted">
              {status ? `HTTP ${status} · ` : ""}
              {error.name}: {error.message}
            </pre>
          </details>
        </div>
        {onRetry && (
          <button className="btn-ghost h-8 shrink-0 px-3 text-xs lg:h-8" onClick={onRetry}>
            <RefreshCw className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden="true" />
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

/** Rows of skeleton matching a table's 40px rhythm. */
export function TableSkeleton({ rows = 5, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div className="divide-y divide-line" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex h-10 items-center gap-4 px-3">
          {Array.from({ length: cols }, (_, c) => (
            <div key={c} className={`skeleton h-3.5 ${c === 0 ? "w-40" : "flex-1"}`} />
          ))}
        </div>
      ))}
    </div>
  );
}
