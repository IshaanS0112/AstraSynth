import { useCallback, useRef, useState } from "react";

import { assetUrl } from "../api/client";
import type { Obstacle, RoverPath } from "../api/types";
import HazardOverlay from "./HazardOverlay";
import PathVisualization from "./PathVisualization";

export type Marker = { x: number; y: number } | null;

interface Props {
  terrainImageUrl: string | null;
  hazardHeatmapUrl: string | null;
  slopeMapUrl: string | null;
  obstacles: Obstacle[] | null;
  path: RoverPath | null;
  start: Marker;
  end: Marker;
  onPick: (point: { x: number; y: number }) => void;
  pickMode: "start" | "end" | null;
}

type Layer = "terrain" | "hazard" | "slope";

/**
 * Renders the terrain image at its natural pixel size inside a responsive box,
 * with every overlay drawn in the *image's own* coordinate space.
 *
 * The click handler converts a DOM click back into image pixel coordinates, so
 * the {x, y} sent to the planner is always in the same frame the backend
 * analysed - not in whatever size the browser happened to lay the image out at.
 */
export default function TerrainViewer({
  terrainImageUrl,
  hazardHeatmapUrl,
  slopeMapUrl,
  obstacles,
  path,
  start,
  end,
  onPick,
  pickMode,
}: Props) {
  const [layer, setLayer] = useState<Layer>("hazard");
  const [showObstacles, setShowObstacles] = useState(true);
  const [naturalSize, setNaturalSize] = useState({ width: 512, height: 512 });
  const imageRef = useRef<HTMLImageElement>(null);

  const activeUrl =
    layer === "hazard" && hazardHeatmapUrl
      ? hazardHeatmapUrl
      : layer === "slope" && slopeMapUrl
        ? slopeMapUrl
        : terrainImageUrl;

  const handleClick = useCallback(
    (event: React.MouseEvent<HTMLDivElement>) => {
      if (!pickMode || !imageRef.current) return;
      const rect = imageRef.current.getBoundingClientRect();
      const scaleX = naturalSize.width / rect.width;
      const scaleY = naturalSize.height / rect.height;
      const x = Math.round((event.clientX - rect.left) * scaleX);
      const y = Math.round((event.clientY - rect.top) * scaleY);
      if (x < 0 || y < 0 || x >= naturalSize.width || y >= naturalSize.height) return;
      onPick({ x, y });
    },
    [pickMode, naturalSize, onPick],
  );

  if (!terrainImageUrl) {
    return (
      <div className="panel flex h-96 items-center justify-center text-sm text-slate-500">
        No terrain image available.
      </div>
    );
  }

  return (
    <div className="panel overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-edge px-4 py-3">
        {(["terrain", "hazard", "slope"] as Layer[]).map((option) => {
          const available =
            option === "terrain" || (option === "hazard" ? hazardHeatmapUrl : slopeMapUrl);
          return (
            <button
              key={option}
              type="button"
              disabled={!available}
              onClick={() => setLayer(option)}
              className={`rounded px-3 py-1 text-xs uppercase tracking-wider transition disabled:opacity-30 ${
                layer === option
                  ? "bg-accent text-void"
                  : "border border-edge text-slate-400 hover:text-accent"
              }`}
            >
              {option}
            </button>
          );
        })}

        <label className="ml-auto flex items-center gap-2 text-xs text-slate-400">
          <input
            type="checkbox"
            checked={showObstacles}
            onChange={(event) => setShowObstacles(event.target.checked)}
            className="accent-teal-400"
          />
          obstacles ({obstacles?.length ?? 0})
        </label>
      </div>

      <div
        className={`relative select-none ${pickMode ? "cursor-crosshair" : ""}`}
        onClick={handleClick}
      >
        <img
          ref={imageRef}
          src={assetUrl(activeUrl)}
          alt="Terrain"
          className="block w-full"
          onLoad={(event) =>
            setNaturalSize({
              width: event.currentTarget.naturalWidth,
              height: event.currentTarget.naturalHeight,
            })
          }
        />

        <svg
          className="pointer-events-none absolute inset-0 h-full w-full"
          viewBox={`0 0 ${naturalSize.width} ${naturalSize.height}`}
          preserveAspectRatio="none"
        >
          {showObstacles && <HazardOverlay obstacles={obstacles} />}
          <PathVisualization path={path} start={start} end={end} />
        </svg>

        {pickMode && (
          <div className="absolute left-3 top-3 rounded bg-void/85 px-3 py-1 text-xs text-accent">
            Click to set {pickMode} point
          </div>
        )}
      </div>
    </div>
  );
}
