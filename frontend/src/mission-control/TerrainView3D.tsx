/**
 * React's view of the 3-D scene.
 *
 * The component owns the canvas and the scene's lifetime; it does not own the
 * scene graph. Each prop maps to exactly one imperative setter, guarded by an
 * effect on the thing that setter depends on - so a telemetry tick moves the
 * rover without rebuilding the terrain, and a layer change recolours without
 * rebuilding the routes.
 */

import { useEffect, useRef, useState } from "react";

import type { GridPoint, TerrainGrid } from "../api/types";
import {
  TerrainScene,
  type CameraView,
  type MarkerSpec,
  type Quality,
  type RouteSpec,
  type RoverPose,
  type SceneLayer,
} from "../three/TerrainScene";

interface Props {
  grid: TerrainGrid | null;
  layer: SceneLayer;
  quality: Quality | "auto";
  showLethal: boolean;
  routes: RouteSpec[];
  markers: MarkerSpec[];
  rover: RoverPose | null;
  contours: boolean;
  cameraRequest: { view: CameraView; nonce: number } | null;
  exaggeration: number;
  onPick?: (point: GridPoint) => void;
  picking?: boolean;
}

interface Hud {
  azimuth: number;
  barMetres: number;
  barPixels: number;
  contourM: number;
  roverScale: number;
}

const BAR_TARGET_PX = 96;

/** A round scale-bar length near ``metres``: 1, 2, 5, 10, 20, 50 ... */
function roundMetres(metres: number): number {
  if (!(metres > 0)) return 0;
  const power = 10 ** Math.floor(Math.log10(metres));
  const scaled = metres / power;
  return (scaled < 2 ? 1 : scaled < 5 ? 2 : 5) * power;
}

export default function TerrainView3D({
  grid,
  layer,
  showLethal,
  routes,
  markers,
  rover,
  contours,
  cameraRequest,
  exaggeration,
  quality,
  onPick,
  picking = false,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<TerrainScene | null>(null);
  const [hud, setHud] = useState<Hud | null>(null);
  // Read at click time rather than captured, so changing the handler does not
  // tear down and rebuild the whole scene.
  const pickHandler = useRef(onPick);
  pickHandler.current = onPick;

  useEffect(() => {
    if (!canvasRef.current || !wrapRef.current) return;
    const scene = new TerrainScene(canvasRef.current);
    sceneRef.current = scene;

    const observer = new ResizeObserver(([entry]) => {
      scene.resize(entry.contentRect.width, entry.contentRect.height);
    });
    observer.observe(wrapRef.current);
    scene.resize(wrapRef.current.clientWidth, wrapRef.current.clientHeight);

    // The compass and scale bar follow the camera. Four times a second is
    // plenty for a readout and keeps React out of the render loop.
    const interval = window.setInterval(() => {
      const height = wrapRef.current?.clientHeight ?? 0;
      const metresPerPx = scene.metresPerPixel(height);
      const barMetres = roundMetres(metresPerPx * BAR_TARGET_PX);
      setHud({
        azimuth: scene.azimuth(),
        barMetres,
        barPixels: metresPerPx > 0 ? barMetres / metresPerPx : 0,
        contourM: scene.contourInterval(),
        roverScale: scene.roverScale(),
      });
    }, 250);

    return () => {
      window.clearInterval(interval);
      observer.disconnect();
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (grid) sceneRef.current?.setTerrain(grid, layer);
    // Keyed on the grid alone: a new layer is a recolour, handled below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid]);

  useEffect(() => {
    sceneRef.current?.setLayer(layer, showLethal);
  }, [layer, showLethal, grid]);

  useEffect(() => {
    sceneRef.current?.setContours(contours);
  }, [contours, grid]);

  useEffect(() => {
    sceneRef.current?.setRoutes(routes);
  }, [routes, grid, exaggeration]);

  useEffect(() => {
    sceneRef.current?.setMarkers(markers);
  }, [markers, grid, exaggeration]);

  useEffect(() => {
    sceneRef.current?.setRover(rover);
  }, [rover, grid, exaggeration]);

  useEffect(() => {
    if (cameraRequest) sceneRef.current?.resetCamera(cameraRequest.view);
  }, [cameraRequest]);

  useEffect(() => {
    sceneRef.current?.setQuality(quality);
  }, [quality]);

  useEffect(() => {
    sceneRef.current?.setExaggeration(exaggeration);
  }, [exaggeration, grid]);

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        aria-label="3-D terrain view. Drag to orbit, scroll to zoom."
        className={`h-full w-full ${picking ? "cursor-crosshair" : "cursor-grab active:cursor-grabbing"}`}
        onClick={(event) => {
          if (!picking) return;
          const point = sceneRef.current?.pick(event.clientX, event.clientY);
          if (point) pickHandler.current?.(point);
        }}
      />

      {grid && hud && (
        <>
          <div className="pointer-events-none absolute right-3 top-3 flex flex-row-reverse items-start gap-2 sm:flex-col sm:items-end">
            <div className="flex items-center gap-2 rounded-md border border-line bg-surface/90 px-2 py-1.5">
              <svg width="28" height="28" viewBox="-14 -14 28 28" role="img" aria-label="North">
                <circle r="12.5" className="fill-none stroke-line-strong" strokeWidth="1" />
                <g transform={`rotate(${(-hud.azimuth * 180) / Math.PI})`}>
                  <path d="M0 -10 L3.5 1 L0 -1.5 L-3.5 1 Z" className="fill-accent" />
                  <path d="M0 10 L3.5 -1 L0 1.5 L-3.5 -1 Z" className="fill-subtle" />
                </g>
              </svg>
              <span className="font-mono text-2xs text-muted">N</span>
            </div>
            <div className="rounded-md border border-line bg-surface/90 px-2 py-1.5">
              <div className="h-1.5 border-x border-b border-fg" style={{ width: `${Math.round(hud.barPixels)}px` }} />
              <div className="mt-1 text-right font-mono text-2xs tabular-nums text-fg">
                {hud.barMetres >= 1000 ? `${hud.barMetres / 1000} km` : `${hud.barMetres} m`}
              </div>
            </div>
          </div>

          {/* Every departure from true scale is printed, so none is mistaken for data. */}
          <div className="pointer-events-none absolute right-3 top-16 rounded-md border border-line bg-surface/90 px-2 py-1 text-right font-mono text-2xs leading-relaxed text-muted sm:bottom-3 sm:top-auto">
            <div>
              relief <span className="text-fg">{exaggeration}×</span>
              {hud.roverScale > 1.5 && (
                <>
                  {" · "}rover <span className="text-fg">{Math.round(hud.roverScale)}×</span>
                </>
              )}
            </div>
            {contours && hud.contourM > 0 && (
              <div>
                contours <span className="text-fg">{hud.contourM} m</span>
              </div>
            )}
          </div>
        </>
      )}

      {!grid && (
        <div className="absolute inset-0 grid place-items-center">
          <div className="max-w-xs text-center">
            <p className="label-tick justify-center">Terrain</p>
            <p className="mt-2 text-sm text-muted">Loading the terrain grid for this mission.</p>
          </div>
        </div>
      )}
    </div>
  );
}
