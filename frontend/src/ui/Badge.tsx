import type { ReactNode } from "react";

export type Tone = "neutral" | "success" | "warning" | "danger" | "info" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-surface-2 text-muted border-line-strong",
  success: "bg-success/12 text-success border-success/25",
  warning: "bg-warning/12 text-warning border-warning/25",
  danger: "bg-danger/12 text-danger border-danger/25",
  info: "bg-info/12 text-info border-info/25",
  accent: "bg-accent/12 text-accent border-accent/25",
};

/** Status as a small mono pill. The dot carries the tone for anyone skimming a column. */
export default function Badge({
  tone = "neutral",
  dot = true,
  pulse = false,
  children,
  title,
}: {
  tone?: Tone;
  dot?: boolean;
  pulse?: boolean;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={`inline-flex h-5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 font-mono text-2xs uppercase tracking-wider ${TONES[tone]}`}
    >
      {dot && (
        <span className={`h-1.5 w-1.5 rounded-full bg-current ${pulse ? "animate-pulse" : ""}`} aria-hidden="true" />
      )}
      {children}
    </span>
  );
}
