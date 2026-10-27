import type { RoverPath } from "../api/types";
import { layerRamps, ramp } from "../three/palette";

interface Props {
  path: RoverPath | null;
  start: { x: number; y: number } | null;
  end: { x: number; y: number } | null;
}

/** The same success -> warning -> danger ramp the 3-D hazard layer uses. */
function hazardColour(score: number): string {
  const [r, g, b] = ramp(layerRamps().hazard, score).map((v) => Math.round(v * 255));
  return `rgb(${r} ${g} ${b})`;
}

export default function PathVisualization({ path, start, end }: Props) {
  // Full class names, never built from parts: Tailwind only generates classes
  // that appear literally in the source.
  const TONE = {
    accent: { stroke: "stroke-accent", fill: "fill-accent" },
    fg: { stroke: "stroke-fg", fill: "fill-fg" },
  } as const;
  const marker = (point: { x: number; y: number } | null, tone: keyof typeof TONE, label: string) =>
    point && (
      <g>
        <circle cx={point.x} cy={point.y} r={7} fill="none" className={TONE[tone].stroke} strokeWidth={2.5} />
        <circle cx={point.x} cy={point.y} r={2.5} className={TONE[tone].fill} />
        <text x={point.x + 11} y={point.y + 4} className={`${TONE[tone].fill} font-mono`} fontSize={11} fontWeight={500}>
          {label}
        </text>
      </g>
    );

  return (
    <g>
      {path && path.waypoints.length > 1 && (
        <>
          {/* Dark casing underneath so the route stays legible over a red
              high-hazard region, where a thin coloured line disappears. */}
          <polyline
            points={path.waypoints.map((w) => `${w.x},${w.y}`).join(" ")}
            fill="none"
            className="stroke-canvas"
            strokeWidth={5}
            strokeLinejoin="round"
            opacity={0.7}
          />
          {path.waypoints.slice(1).map((waypoint, index) => {
            const previous = path.waypoints[index];
            return (
              <line
                key={waypoint.segment_id}
                x1={previous.x}
                y1={previous.y}
                x2={waypoint.x}
                y2={waypoint.y}
                stroke={hazardColour(waypoint.hazard_score)}
                strokeWidth={2.5}
                strokeLinecap="round"
              />
            );
          })}
        </>
      )}
      {marker(start, "accent", "START")}
      {marker(end, "fg", "GOAL")}
    </g>
  );
}
