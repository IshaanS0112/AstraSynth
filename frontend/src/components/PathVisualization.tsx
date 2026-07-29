import type { RoverPath } from "../api/types";

interface Props {
  path: RoverPath | null;
  start: { x: number; y: number } | null;
  end: { x: number; y: number } | null;
}

/** Green (safe) through amber to red (hazardous), matching the heatmap ramp. */
function hazardColour(score: number): string {
  const clamped = Math.max(0, Math.min(1, score));
  const hue = (1 - clamped) * 120; // 120deg green -> 0deg red
  return `hsl(${hue}, 85%, 55%)`;
}

export default function PathVisualization({ path, start, end }: Props) {
  const marker = (
    point: { x: number; y: number } | null,
    colour: string,
    label: string,
  ) =>
    point && (
      <g>
        <circle cx={point.x} cy={point.y} r={7} fill="none" stroke={colour} strokeWidth={2.5} />
        <circle cx={point.x} cy={point.y} r={2.5} fill={colour} />
        <text x={point.x + 11} y={point.y + 4} fill={colour} fontSize={11} fontWeight={600}>
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
            stroke="#020617"
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
      {marker(start, "#5eead4", "START")}
      {marker(end, "#f472b6", "GOAL")}
    </g>
  );
}
