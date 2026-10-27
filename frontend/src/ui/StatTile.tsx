import type { ReactNode } from "react";

/** A KPI: tick label, the number in mono, and one line saying what it is measured against. */
export default function StatTile({
  label,
  value,
  unit,
  detail,
  loading,
  children,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  detail?: ReactNode;
  loading?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="panel flex min-w-0 flex-col gap-2 p-4">
      <div className="label-tick">{label}</div>
      {loading ? (
        <>
          <div className="skeleton h-8 w-24" />
          <div className="skeleton h-4 w-36" />
        </>
      ) : (
        <>
          <div className="flex items-baseline gap-1.5">
            <span className="num text-3xl font-medium leading-none text-fg">{value}</span>
            {unit && <span className="font-mono text-xs text-muted">{unit}</span>}
          </div>
          {detail && <div className="truncate text-xs text-muted">{detail}</div>}
          {children}
        </>
      )}
    </div>
  );
}
