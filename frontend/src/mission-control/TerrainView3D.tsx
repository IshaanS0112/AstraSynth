/**
 * React's view of the 3-D scene.
 *
 * The component owns the canvas and the scene's lifetime; it does not own the
 * scene graph. Each prop maps to exactly one imperative setter, guarded by an
 * effect on the thing that setter depends on - so a telemetry tick does not
 * rebuild a terrain mesh, and a layer change does not rebuild the routes.
 */

import { useEffect, useRef } from "react";

import type { GridPoint, TerrainGrid, TerrainLayerName } from "../api/types";
import { TerrainScene, type CameraView, type MarkerSpec, type RouteSpec } from "../three/TerrainScene";

interface Props {
  grid: TerrainGrid | null;
  layer: TerrainLayerName;
  showLethal: boolean;
  routes: RouteSpec[];
  markers: MarkerSpec[];
  cameraRequest: { view: CameraView; nonce: number } | null;
  exaggeration: number;
  onPick?: (point: GridPoint) => void;
  picking?: boolean;
}

export default function TerrainView3D({
  grid,
  layer,
  showLethal,
  routes,
  markers,
  cameraRequest,
  exaggeration,
  onPick,
  picking = false,
}: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const sceneRef = useRef<TerrainScene | null>(null);
  // The handler is read at click time rather than captured, so changing it does
  // not require tearing down and rebuilding the whole scene.
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

    return () => {
      observer.disconnect();
      scene.dispose();
      sceneRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (grid) sceneRef.current?.setTerrain(grid, layer);
    // Intentionally keyed on the grid alone: a new layer is a colour update,
    // not a rebuild, and is handled by the effect below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [grid]);

  useEffect(() => {
    sceneRef.current?.setLayer(layer, showLethal);
  }, [layer, showLethal, grid]);

  useEffect(() => {
    sceneRef.current?.setRoutes(routes);
  }, [routes]);

  useEffect(() => {
    sceneRef.current?.setMarkers(markers);
  }, [markers]);

  useEffect(() => {
    if (cameraRequest) sceneRef.current?.resetCamera(cameraRequest.view);
  }, [cameraRequest]);

  useEffect(() => {
    sceneRef.current?.setExaggeration(exaggeration);
  }, [exaggeration, grid]);

  return (
    <div ref={wrapRef} className="relative h-full w-full">
      <canvas
        ref={canvasRef}
        className={`h-full w-full ${picking ? "cursor-crosshair" : "cursor-grab"}`}
        onClick={(event) => {
          if (!picking) return;
          const point = sceneRef.current?.pick(event.clientX, event.clientY);
          if (point) pickHandler.current?.(point);
        }}
      />
      {!grid && (
        <div className="absolute inset-0 grid place-items-center text-xs text-slate-500">
          Analyse the terrain to load the 3-D view.
        </div>
      )}
    </div>
  );
}
