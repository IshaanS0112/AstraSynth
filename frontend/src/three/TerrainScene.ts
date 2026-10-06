/**
 * The 3-D planetary view: a plain class, no React.
 *
 * The renderer owns the canvas, scene graph and animation loop; React owns state
 * and decides what to show. The interface is imperative and coarse - hand it a
 * terrain, a layer name, routes, markers - and each setter rebuilds only what it
 * owns, so a telemetry tick cannot rebuild a terrain mesh.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import type { GridPoint, TerrainGrid, TerrainLayerName } from "../api/types";

/** The terrain always spans this many world units on its longest side. */
const WORLD_SPAN = 100;

export type CameraView = "orbit" | "top" | "three-quarter";

export interface RouteSpec {
  id: string;
  waypoints: GridPoint[];
  color: number;
  /** Drawn thicker and above the others. */
  emphasis?: boolean;
  dashed?: boolean;
}

export interface MarkerSpec {
  id: string;
  point: GridPoint;
  color: number;
  shape: "start" | "goal" | "target" | "rover" | "relay";
  label?: string;
}

/** Linear interpolation between colour stops given as [r, g, b] in 0-1. */
function ramp(stops: [number, number, number][], t: number): [number, number, number] {
  const clamped = Math.min(1, Math.max(0, t));
  const scaled = clamped * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  const local = scaled - index;
  const a = stops[index];
  const b = stops[index + 1];
  return [
    a[0] + (b[0] - a[0]) * local,
    a[1] + (b[1] - a[1]) * local,
    a[2] + (b[2] - a[2]) * local,
  ];
}

// Hazard and slope share a ramp: they mean the same thing to an operator.
const RAMPS: Record<TerrainLayerName, [number, number, number][]> = {
  elevation_m: [
    [0.13, 0.15, 0.19],
    [0.35, 0.36, 0.4],
    [0.62, 0.6, 0.58],
    [0.86, 0.85, 0.83],
  ],
  hazard: [
    [0.13, 0.36, 0.26],
    [0.51, 0.66, 0.25],
    [0.85, 0.62, 0.11],
    [0.75, 0.21, 0.18],
  ],
  slope_deg: [
    [0.13, 0.36, 0.26],
    [0.51, 0.66, 0.25],
    [0.85, 0.62, 0.11],
    [0.75, 0.21, 0.18],
  ],
  uncertainty: [
    [0.09, 0.12, 0.18],
    [0.16, 0.3, 0.44],
    [0.24, 0.6, 0.66],
    [0.37, 0.92, 0.83],
  ],
};

export class TerrainScene {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly raycaster = new THREE.Raycaster();

  private readonly routeGroup = new THREE.Group();
  private readonly markerGroup = new THREE.Group();

  private mesh: THREE.Mesh | null = null;
  private geometry: THREE.PlaneGeometry | null = null;
  private grid: TerrainGrid | null = null;
  private cellSize = 1;
  private verticalScale = 1;
  private exaggeration = 1;
  private frame = 0;
  private disposed = false;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({
      canvas,
      antialias: true,
      // Opaque: the scene paints its own ground.
      alpha: false,
    });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    this.scene.background = new THREE.Color(0x080b12);
    this.scene.fog = new THREE.Fog(0x080b12, WORLD_SPAN * 1.6, WORLD_SPAN * 3.4);

    this.camera = new THREE.PerspectiveCamera(45, 1, 0.1, 2000);
    this.camera.position.set(0, WORLD_SPAN * 0.6, WORLD_SPAN * 0.78);

    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.49; // never go under the terrain
    this.controls.minDistance = WORLD_SPAN * 0.15;
    this.controls.maxDistance = WORLD_SPAN * 2.5;

    // Two lights: one directional source makes north-facing slopes read as
    // black, which on a hazard map is indistinguishable from safe.
    const key = new THREE.DirectionalLight(0xffffff, 1.5);
    key.position.set(-1, 2, 1.2);
    this.scene.add(key);
    this.scene.add(new THREE.AmbientLight(0x94a3b8, 0.85));

    this.scene.add(this.routeGroup);
    this.scene.add(this.markerGroup);

    this.animate();
  }

  // --- geometry and framing -------------------------------------------------

  /** Grid cell -> world position, the single source of truth for placement. */
  private cellToWorld(row: number, col: number, lift = 0): THREE.Vector3 {
    const grid = this.grid;
    if (!grid) return new THREE.Vector3();
    const r = Math.min(grid.rows - 1, Math.max(0, Math.round(row)));
    const c = Math.min(grid.cols - 1, Math.max(0, Math.round(col)));
    return new THREE.Vector3(
      (c - (grid.cols - 1) / 2) * this.cellSize,
      grid.layers.elevation_m[r][c] * this.verticalScale + lift,
      (r - (grid.rows - 1) / 2) * this.cellSize,
    );
  }

  /** Image pixels (the frame the API speaks) -> grid cell. */
  private pixelToCell(point: GridPoint): [number, number] {
    const grid = this.grid;
    if (!grid) return [0, 0];
    return [point.y / grid.pixel_scale, point.x / grid.pixel_scale];
  }

  setTerrain(grid: TerrainGrid, layer: TerrainLayerName = "hazard"): void {
    this.grid = grid;
    this.cellSize = WORLD_SPAN / Math.max(grid.rows, grid.cols);
    // True scale: one metre of rise is one metre across, so the slope layer can
    // be trusted. Exaggeration is a separate, labelled control.
    this.verticalScale = (this.cellSize / grid.meters_per_cell) * this.exaggeration;

    this.disposeMesh();

    const geometry = new THREE.PlaneGeometry(
      this.cellSize * (grid.cols - 1),
      this.cellSize * (grid.rows - 1),
      grid.cols - 1,
      grid.rows - 1,
    );
    // PlaneGeometry is XY with +Y up; rotating it flat maps +Y onto world -Z,
    // which is why row 0 lands at -Z and matches cellToWorld.
    geometry.rotateX(-Math.PI / 2);

    const position = geometry.attributes.position as THREE.BufferAttribute;
    for (let row = 0; row < grid.rows; row += 1) {
      for (let col = 0; col < grid.cols; col += 1) {
        position.setY(row * grid.cols + col, grid.layers.elevation_m[row][col] * this.verticalScale);
      }
    }
    position.needsUpdate = true;

    geometry.setAttribute(
      "color",
      new THREE.BufferAttribute(new Float32Array(grid.rows * grid.cols * 3), 3),
    );
    geometry.computeVertexNormals();

    const material = new THREE.MeshStandardMaterial({
      vertexColors: true,
      roughness: 0.95,
      metalness: 0.0,
      flatShading: false,
    });

    this.geometry = geometry;
    this.mesh = new THREE.Mesh(geometry, material);
    this.scene.add(this.mesh);
    this.setLayer(layer);
    this.resetCamera("three-quarter");
  }

  /**
   * Vertical exaggeration, default 1 (true scale). A knob rather than a baked-in
   * value: the slope layer is only trustworthy at 1, but 40 m of relief across a
   * 1 km tile is nearly invisible there.
   */
  setExaggeration(value: number): void {
    this.exaggeration = value;
    if (this.grid) this.setTerrain(this.grid, this.currentLayer);
  }

  private currentLayer: TerrainLayerName = "hazard";

  setLayer(layer: TerrainLayerName, showLethal = true): void {
    this.currentLayer = layer;
    const grid = this.grid;
    const geometry = this.geometry;
    if (!grid || !geometry) return;

    const values = grid.layers[layer];
    const [low, high] = grid.ranges[layer] ?? [0, 1];
    const span = high - low || 1;
    const stops = RAMPS[layer];
    const colors = geometry.attributes.color as THREE.BufferAttribute;

    for (let row = 0; row < grid.rows; row += 1) {
      for (let col = 0; col < grid.cols; col += 1) {
        const index = row * grid.cols + col;
        let rgb = ramp(stops, (values[row][col] - low) / span);
        // Lethal ground is drawn on every layer, not just hazard: inferring it
        // from another tab is how a route gets approved across a crater rim.
        if (showLethal && grid.layers.lethal[row][col]) {
          rgb = [0.55, 0.1, 0.12];
        }
        colors.setXYZ(index, rgb[0], rgb[1], rgb[2]);
      }
    }
    colors.needsUpdate = true;
  }

  // --- routes and markers ---------------------------------------------------

  setRoutes(routes: RouteSpec[]): void {
    this.clearGroup(this.routeGroup);
    if (!this.grid) return;

    for (const route of routes) {
      if (route.waypoints.length < 2) continue;
      const points = route.waypoints.map((point) => {
        const [row, col] = this.pixelToCell(point);
        return this.cellToWorld(row, col, route.emphasis ? 0.9 : 0.55);
      });
      const geometry = new THREE.BufferGeometry().setFromPoints(points);
      const material = route.dashed
        ? new THREE.LineDashedMaterial({
            color: route.color,
            dashSize: this.cellSize * 0.9,
            gapSize: this.cellSize * 0.7,
            transparent: true,
            opacity: 0.75,
          })
        : new THREE.LineBasicMaterial({ color: route.color });
      const line = new THREE.Line(geometry, material);
      if (route.dashed) line.computeLineDistances();
      line.renderOrder = route.emphasis ? 3 : 2;
      line.name = route.id;
      this.routeGroup.add(line);
    }
  }

  setMarkers(markers: MarkerSpec[]): void {
    this.clearGroup(this.markerGroup);
    if (!this.grid) return;

    for (const marker of markers) {
      const [row, col] = this.pixelToCell(marker.point);
      const size = this.cellSize * (marker.shape === "rover" ? 1.5 : 1.15);
      const geometry =
        marker.shape === "target"
          ? new THREE.OctahedronGeometry(size)
          : marker.shape === "rover"
            ? new THREE.ConeGeometry(size * 0.7, size * 2.0, 12)
            : marker.shape === "relay"
              ? new THREE.CylinderGeometry(size * 0.2, size * 0.45, size * 2.4, 8)
              : new THREE.SphereGeometry(size * 0.8, 16, 12);

      const mesh = new THREE.Mesh(
        geometry,
        new THREE.MeshStandardMaterial({
          color: marker.color,
          emissive: marker.color,
          emissiveIntensity: marker.shape === "rover" ? 0.75 : 0.4,
          roughness: 0.4,
        }),
      );
      mesh.position.copy(this.cellToWorld(row, col, size * 1.4));
      mesh.name = marker.id;
      this.markerGroup.add(mesh);
    }
  }

  // --- interaction ----------------------------------------------------------

  /**
   * Canvas coordinates -> the image pixel under the cursor, or null off-terrain.
   * Image pixels, not cells, because that is the frame every API endpoint speaks.
   */
  pick(clientX: number, clientY: number): GridPoint | null {
    if (!this.mesh || !this.grid) return null;
    const rect = this.canvas.getBoundingClientRect();
    const pointer = new THREE.Vector2(
      ((clientX - rect.left) / rect.width) * 2 - 1,
      -((clientY - rect.top) / rect.height) * 2 + 1,
    );
    this.raycaster.setFromCamera(pointer, this.camera);
    const hit = this.raycaster.intersectObject(this.mesh, false)[0];
    if (!hit) return null;

    const col = hit.point.x / this.cellSize + (this.grid.cols - 1) / 2;
    const row = hit.point.z / this.cellSize + (this.grid.rows - 1) / 2;
    return {
      x: Math.round(col * this.grid.pixel_scale),
      y: Math.round(row * this.grid.pixel_scale),
    };
  }

  resetCamera(view: CameraView): void {
    // 0.75 spans: a 45-degree FOV over a 100-unit plane puts the far edge just
    // inside frame. Further back wastes the panel.
    const distance = WORLD_SPAN * (view === "top" ? 0.82 : 0.9);
    if (view === "top") {
      this.camera.position.set(0, distance * 1.55, 0.001);
    } else if (view === "three-quarter") {
      this.camera.position.set(-distance * 0.7, distance * 0.62, distance * 0.8);
    } else {
      this.camera.position.set(0, distance * 0.45, distance);
    }
    this.controls.target.set(0, 0, 0);
    this.controls.update();
  }

  resize(width: number, height: number): void {
    if (width === 0 || height === 0) return;
    this.renderer.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    this.clearGroup(this.routeGroup);
    this.clearGroup(this.markerGroup);
    this.disposeMesh();
    this.controls.dispose();
    this.renderer.dispose();
  }

  // --- internals ------------------------------------------------------------

  private animate = (): void => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.animate);
    this.controls.update();
    this.renderer.render(this.scene, this.camera);
  };

  private disposeMesh(): void {
    if (!this.mesh) return;
    this.scene.remove(this.mesh);
    this.mesh.geometry.dispose();
    (this.mesh.material as THREE.Material).dispose();
    this.mesh = null;
    this.geometry = null;
  }

  private clearGroup(group: THREE.Group): void {
    // WebGL resources are not GC'd with their JS object, and a traverse replay
    // rebuilds this group every frame of the scrub.
    for (const child of [...group.children]) {
      group.remove(child);
      const withGeometry = child as THREE.Mesh | THREE.Line;
      withGeometry.geometry?.dispose();
      const material = withGeometry.material;
      if (Array.isArray(material)) material.forEach((m) => m.dispose());
      else material?.dispose();
    }
  }
}
