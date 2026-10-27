import type { ReactNode } from "react";

/** Title and one line of context on the left, the page's actions on the right. */
export default function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold tracking-tight text-fg">{title}</h1>
        {description && <p className="mt-1 max-w-2xl text-sm text-muted">{description}</p>}
        {meta && <div className="mt-2 flex flex-wrap items-center gap-2">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}

/** A section inside a page: tick label, an optional claim, and an action on the right. */
export function Section({
  label,
  title,
  action,
  children,
  className = "",
}: {
  label: string;
  title?: ReactNode;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={className}>
      <div className="mb-3 flex items-end justify-between gap-3">
        <div className="min-w-0">
          <h2 className="label-tick">{label}</h2>
          {title && <p className="mt-1 text-sm text-fg">{title}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}
