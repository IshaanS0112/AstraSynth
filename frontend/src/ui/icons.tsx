import type { ComponentType, SVGProps } from "react";

/** What a nav entry or a command needs from an icon: Lucide's and ours both fit. */
export type Icon = ComponentType<{ className?: string; strokeWidth?: number | string }>;

/** The product mark: a ridge line and the rover on it. */
export function BrandMark({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} aria-hidden="true">
      <path d="M6 23 L16 8 L26 23" fill="none" className="stroke-accent" strokeWidth="2.5" strokeLinejoin="round" />
      <circle cx="16" cy="19" r="2.5" className="fill-accent" />
    </svg>
  );
}

/**
 * A rocker-bogie rover in Lucide's grid and stroke, since Lucide has no rover:
 * mast and camera head, chassis, three wheels on their legs.
 */
export function RoverIcon({ strokeWidth = 1.5, ...props }: SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      <path d="M8 9V4.5h3.5" />
      <rect x="5" y="9" width="14" height="4.5" rx="1" />
      <path d="M8 13.5 5 18M12 13.5V18M16 13.5l3 4.5" />
      <circle cx="5" cy="19" r="1.75" />
      <circle cx="12" cy="19" r="1.75" />
      <circle cx="19" cy="19" r="1.75" />
    </svg>
  );
}
