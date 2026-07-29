import type { Obstacle } from "../api/types";

/**
 * Draws the obstacle regions the contour detector found.
 *
 * Rendered as circles from each contour's minimum enclosing circle rather than
 * the raw contour polygon: the point of this layer is to show *where the
 * detector fired and how big the feature is*, which the enclosing circle
 * conveys without shipping thousands of polygon vertices to the browser.
 */
export default function HazardOverlay({ obstacles }: { obstacles: Obstacle[] | null }) {
  if (!obstacles?.length) return null;

  return (
    <g>
      {obstacles.map((obstacle) => (
        <g key={obstacle.id}>
          <circle
            cx={obstacle.x}
            cy={obstacle.y}
            r={obstacle.radius_px}
            fill="none"
            stroke="#fbbf24"
            strokeWidth={1.5}
            strokeDasharray="4 3"
            opacity={0.75}
          />
          <circle cx={obstacle.x} cy={obstacle.y} r={2} fill="#fbbf24" opacity={0.9} />
        </g>
      ))}
    </g>
  );
}
