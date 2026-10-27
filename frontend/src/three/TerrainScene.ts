/**
 * The 3-D planetary view: a plain class, no React.
 *
 * The renderer owns the canvas, scene graph and animation loop; React owns state
 * and decides what to show. The interface is imperative and coarse - hand it a
 * terrain, a layer, routes, markers, a rover pose - and each setter rebuilds
 * only what it owns, so a telemetry tick cannot rebuild a terrain mesh.
 *
 * Two kinds of layer share one mesh. Data layers (hazard, slope ...) are vertex
 * colours under even, neutral light, tuned so flat ground shows its legend
 * colour. The surface layer is a visual: Blender-baked regolith under the Mars
 * sky it was rendered with. Lethal ground is marked on both.
 */

import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";

import type { GridPoint, TerrainGrid, TerrainLayerName } from "../api/types";
import { loadModel, loadSky, loadSkyInfo, loadSurface, type SkyInfo, type SurfaceSet } from "./assets";
import { regolithMaterial, skyDome, strataMaterial, wheelTracks } from "./environment";
import { hex, layerRamps, mix, ramp, toLinear, unit, type Rgb } from "./palette";
import { PostFX, type Quality } from "./postfx";
import { RoverModel } from "./RoverModel";

/** The terrain always spans this many world units on its longest side. */
const WORLD_SPAN = 100;
/** Rover model length in metres; the model is drawn at a legible scale, not true scale. */
const ROVER_LENGTH_M = 3.0;
const ROVER_DISPLAY_CELLS = 3.4;
/** Rover wheel gauge and width in model metres, for the tracks it leaves. */
const ROVER_GAUGE_M = 2.04;
const ROVER_WHEEL_W_M = 0.4;
/** Bottom of the terrain block's side walls, in world units. */
const SKIRT_BASE = -WORLD_SPAN * 0.035;
/** Mesh vertices per grid cell along each axis: heights are smoothed between cells. */
const SUBDIVIDE = 2;
/** World units per repeat of the regolith and strata textures. */
const REGOLITH_TILE = 9;
const STRATA_TILE = 14;
const DATA_SUN = new THREE.Vector3(-0.55, 0.75, 0.35).normalize();

/** A palette colour as a three.js colour, converted from sRGB like every hex colour is. */
const srgb = (rgb: Rgb) => new THREE.Color().setRGB(rgb[0], rgb[1], rgb[2], THREE.SRGBColorSpace);
const fromHex = (value: string) => new THREE.Color(value);

export type CameraView = "orbit" | "top" | "three-quarter" | "follow";
export type SceneLayer = TerrainLayerName | "surface";
export type { Quality };

export interface RouteSpec {
  id: string;
  waypoints: GridPoint[];
  color: number;
  /** Drawn thicker and above the others. */
  emphasis?: boolean;
  /** Drawn as a dotted trail rather than a solid tube. */
  dashed?: boolean;
}

export interface MarkerSpec {
  id: string;
  point: GridPoint;
  color: number;
  shape: "start" | "goal" | "target" | "relay";
  label?: string;
}

export interface RoverPose {
  point: GridPoint;
  /** The previous point on the path, for the heading. */
  from?: GridPoint | null;
  /** True-scale sensor ring, in metres. Zero or absent draws nothing. */
  sensorRangeM?: number;
}

/** Catmull-Rom weights for a sample ``t`` of the way from p1 to p2. */
function cubic(p0: number, p1: number, p2: number, p3: number, t: number): number {
  return (
    p1 +
    0.5 * t * (p2 - p0 + t * (2 * p0 - 5 * p1 + 4 * p2 - p3 + t * (3 * (p1 - p2) + p3 - p0)))
  );
}

export class TerrainScene {
  private readonly renderer: THREE.WebGLRenderer;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly controls: OrbitControls;
  private readonly raycaster = new THREE.Raycaster();
  private readonly clock = new THREE.Clock();
  private readonly sun: THREE.DirectionalLight;
  private readonly hemisphere: THREE.HemisphereLight;
  private readonly postfx: PostFX;
  private readonly pmrem: THREE.PMREMGenerator;
  private readonly studioEnv: THREE.Texture;
  private marsEnv: THREE.Texture | null = null;
  private skyInfo: SkyInfo | null = null;
  private readonly dome: THREE.Mesh;
  private readonly floor: THREE.Points;
  private readonly dataBackground = new THREE.Color(hex("bg"));
  private readonly dataFog: THREE.Fog;
  private readonly surfaceFog: THREE.Fog;
  private readonly reducedMotion =
    typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  private readonly terrainGroup = new THREE.Group();
  private readonly contourGroup = new THREE.Group();
  private readonly routeGroup = new THREE.Group();
  private readonly trackGroup = new THREE.Group();
  private readonly markerGroup = new THREE.Group();
  private readonly ringGroup = new THREE.Group();
  private readonly rover = new RoverModel();

  private mesh: THREE.Mesh | null = null;
  private geometry: THREE.BufferGeometry | null = null;
  private skirt: THREE.Mesh | null = null;
  private dataMaterial: THREE.MeshStandardMaterial | null = null;
  private surfaceMaterial: THREE.MeshStandardMaterial | null = null;
  private skirtDataMaterial: THREE.MeshStandardMaterial | null = null;
  private skirtStrataMaterial: THREE.MeshStandardMaterial | null = null;
  private regolith: SurfaceSet | null = null;
  private strata: SurfaceSet | null = null;
  private beaconAsset: THREE.Group | null = null;

  private grid: TerrainGrid | null = null;
  private meshRows = 0;
  private meshCols = 0;
  private cellSize = 1;
  private verticalScale = 1;
  private exaggeration = 2;
  private currentLayer: SceneLayer = "hazard";
  private showLethal = true;
  private showContours = true;
  private follow = false;
  private lastView: CameraView = "three-quarter";
  /** Set once the operator drags or zooms; until then the view keeps itself fitted. */
  private userMoved = false;
  private beacons: THREE.Object3D[] = [];
  private beaconLights: THREE.MeshStandardMaterial[] = [];
  private lastMarkers: MarkerSpec[] = [];
  private executedPath: GridPoint[] = [];
  private frame = 0;
  private disposed = false;

  private autoQuality = true;
  private frameTimes: number[] = [];
  private framesSeen = 0;

  constructor(private readonly canvas: HTMLCanvasElement) {
    this.renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: "high-performance" });
    this.renderer.shadowMap.enabled = true;
    this.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    this.renderer.toneMapping = THREE.NeutralToneMapping;
    this.renderer.toneMappingExposure = 1.0;
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;

    this.scene.background = this.dataBackground;
    this.dataFog = new THREE.Fog(hex("bg"), WORLD_SPAN * 1.4, WORLD_SPAN * 3.2);
    this.surfaceFog = new THREE.Fog(fromHex("#a77a55"), WORLD_SPAN * 1.1, WORLD_SPAN * 3.6); // lint-ok: dust haze sampled from the Blender sky
    this.scene.fog = this.dataFog;

    this.camera = new THREE.PerspectiveCamera(42, 1, 0.1, 2400);
    this.controls = new OrbitControls(this.camera, canvas);
    this.controls.enableDamping = true;
    this.controls.dampingFactor = 0.08;
    this.controls.maxPolarAngle = Math.PI * 0.48; // never under the terrain
    this.controls.minDistance = WORLD_SPAN * 0.04;
    this.controls.maxDistance = WORLD_SPAN * 2.4;
    this.controls.addEventListener("start", () => {
      this.userMoved = true;
    });

    // Sky from above, regolith bounce from below: north-facing slopes stay
    // readable instead of going black, which on a hazard map reads as safe.
    // Balanced so flat ground renders at its legend colour (sun 0.72 + sky 0.29
    // of the albedo): the shading carries the relief and never the data.
    this.hemisphere = new THREE.HemisphereLight(
      srgb(mix(unit("fg"), unit("info"), 0.2)),
      srgb(mix(unit("bg"), unit("chart-4"), 0.25)),
      1.3,
    );
    this.scene.add(this.hemisphere);
    this.sun = new THREE.DirectionalLight(0xffffff, 3.0);
    this.sun.position.copy(DATA_SUN).multiplyScalar(WORLD_SPAN);
    this.sun.castShadow = true;
    this.sun.shadow.mapSize.set(4096, 4096);
    this.sun.shadow.bias = -0.0003;
    this.sun.shadow.normalBias = 0.35;
    const shadowCam = this.sun.shadow.camera;
    shadowCam.left = -WORLD_SPAN * 0.62;
    shadowCam.right = WORLD_SPAN * 0.62;
    shadowCam.top = WORLD_SPAN * 0.62;
    shadowCam.bottom = -WORLD_SPAN * 0.62;
    shadowCam.near = 1;
    shadowCam.far = WORLD_SPAN * 3;
    this.scene.add(this.sun);

    this.pmrem = new THREE.PMREMGenerator(this.renderer);
    this.studioEnv = this.pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
    this.rover.setEnvironment(this.studioEnv, 0.55);

    this.dome = skyDome(fromHex("#b98b62"), fromHex("#1c1714"), fromHex("#120d0a"), DATA_SUN); // lint-ok: Mars sky, not UI
    this.dome.visible = false;
    this.scene.add(this.dome);

    this.floor = this.buildFloor();
    this.scene.add(this.floor);
    for (const group of [this.terrainGroup, this.contourGroup, this.trackGroup, this.routeGroup, this.markerGroup, this.ringGroup]) {
      this.scene.add(group);
    }
    this.rover.root.visible = false;
    this.scene.add(this.rover.root);

    this.postfx = new PostFX(this.renderer, this.scene, this.camera);
    this.postfx.setQuality("balanced");

    this.resetCamera("three-quarter");
    this.animate();
    void this.loadAssets();
  }

  // --- assets -------------------------------------------------------------------

  private async loadAssets(): Promise<void> {
    const [roverModel, beacon, sky, info, regolith, strata] = await Promise.all([
      loadModel("rover"),
      loadModel("beacon"),
      loadSky(),
      loadSkyInfo(),
      loadSurface("regolith"),
      loadSurface("strata"),
    ]);
    if (this.disposed) return;
    if (roverModel) {
      this.rover.useAsset(roverModel.clone(true));
    }
    this.beaconAsset = beacon;
    if (sky) this.marsEnv = this.pmrem.fromEquirectangular(sky).texture;
    this.skyInfo = info;
    if (info) (this.dome.material as THREE.ShaderMaterial).uniforms.sunDir.value.fromArray(info.sun).normalize();
    this.regolith = regolith;
    this.strata = strata;
    this.buildSurfaceMaterials();
    this.setLayer(this.currentLayer, this.showLethal);
    this.setMarkers(this.lastMarkers);
  }

  /** True once the photographic surface can be drawn. */
  surfaceReady(): boolean {
    return this.regolith !== null;
  }

  private buildSurfaceMaterials(): void {
    const grid = this.grid;
    if (!grid) return;
    const anisotropy = Math.min(8, this.renderer.capabilities.getMaxAnisotropy());
    this.surfaceMaterial?.dispose();
    this.skirtStrataMaterial?.dispose();
    const span = this.cellSize * Math.max(grid.cols, grid.rows);
    this.surfaceMaterial = this.regolith ? regolithMaterial(this.regolith, span / REGOLITH_TILE, anisotropy, this.marsEnv) : null;
    const perimeter = 2 * this.cellSize * ((grid.cols - 1) + (grid.rows - 1));
    this.skirtStrataMaterial = this.strata ? strataMaterial(this.strata, perimeter / STRATA_TILE, anisotropy, this.marsEnv) : null;
  }

  // --- frame conversion ---------------------------------------------------------

  /** A grid layer sampled at fractional (row, col): bicubic for heights, bilinear otherwise. */
  private sample(values: number[][], row: number, col: number, smooth: boolean): number {
    const grid = this.grid!;
    const r = Math.min(grid.rows - 1, Math.max(0, row));
    const c = Math.min(grid.cols - 1, Math.max(0, col));
    const r0 = Math.floor(r);
    const c0 = Math.floor(c);
    const fr = r - r0;
    const fc = c - c0;
    const at = (rr: number, cc: number) =>
      values[Math.min(grid.rows - 1, Math.max(0, rr))][Math.min(grid.cols - 1, Math.max(0, cc))];
    if (!smooth) {
      const top = at(r0, c0) * (1 - fc) + at(r0, c0 + 1) * fc;
      const bottom = at(r0 + 1, c0) * (1 - fc) + at(r0 + 1, c0 + 1) * fc;
      return top * (1 - fr) + bottom * fr;
    }
    const rows = [-1, 0, 1, 2].map((dr) =>
      cubic(at(r0 + dr, c0 - 1), at(r0 + dr, c0), at(r0 + dr, c0 + 1), at(r0 + dr, c0 + 2), fc),
    );
    return cubic(rows[0], rows[1], rows[2], rows[3], fr);
  }

  private heightAt(row: number, col: number): number {
    if (!this.grid) return 0;
    // The same interpolation the mesh is built with, so anything draped on the
    // terrain sits on it between vertices too.
    return this.sample(this.grid.layers.elevation_m, row, col, true) * this.verticalScale;
  }

  private heightAtWorld = (x: number, z: number): number => {
    const grid = this.grid;
    if (!grid) return 0;
    return this.heightAt(z / this.cellSize + (grid.rows - 1) / 2, x / this.cellSize + (grid.cols - 1) / 2);
  };

  /** Grid cell (fractional) -> world position on the surface, plus ``lift``. */
  private cellToWorld(row: number, col: number, lift = 0): THREE.Vector3 {
    const grid = this.grid;
    if (!grid) return new THREE.Vector3();
    return new THREE.Vector3(
      (col - (grid.cols - 1) / 2) * this.cellSize,
      this.heightAt(row, col) + lift,
      (row - (grid.rows - 1) / 2) * this.cellSize,
    );
  }

  /** Image pixels (the frame the API speaks) -> fractional grid cell. */
  private pixelToCell(point: GridPoint): [number, number] {
    const grid = this.grid;
    if (!grid) return [0, 0];
    return [point.y / grid.pixel_scale, point.x / grid.pixel_scale];
  }

  private normalAt(row: number, col: number): THREE.Vector3 {
    const d = 0.75;
    const left = this.cellToWorld(row, col - d);
    const right = this.cellToWorld(row, col + d);
    const up = this.cellToWorld(row - d, col);
    const down = this.cellToWorld(row + d, col);
    const tangentX = right.sub(left);
    const tangentZ = down.sub(up);
    return new THREE.Vector3().crossVectors(tangentZ, tangentX).normalize();
  }

  // --- terrain ------------------------------------------------------------------

  setTerrain(grid: TerrainGrid, layer: SceneLayer = "hazard"): void {
    const sameShape = this.grid?.rows === grid.rows && this.grid?.cols === grid.cols;
    this.grid = grid;
    this.cellSize = WORLD_SPAN / Math.max(grid.rows, grid.cols);
    // One metre of rise is ``exaggeration`` metres across. The overlay always
    // says which, so a 2x view is never mistaken for true scale.
    this.verticalScale = (this.cellSize / grid.meters_per_cell) * this.exaggeration;

    this.clearGroup(this.terrainGroup);
    this.dataMaterial = null;
    this.skirtDataMaterial = null;
    this.mesh = null;
    this.geometry = null;
    this.skirt = null;

    // Vertices at every 1/SUBDIVIDE of a cell; heights between cells are
    // Catmull-Rom, so slopes shade smoothly instead of in facets.
    const rows = (grid.rows - 1) * SUBDIVIDE + 1;
    const cols = (grid.cols - 1) * SUBDIVIDE + 1;
    this.meshRows = rows;
    this.meshCols = cols;
    const positions = new Float32Array(rows * cols * 3);
    const uvs = new Float32Array(rows * cols * 2);
    for (let i = 0; i < rows; i += 1) {
      for (let j = 0; j < cols; j += 1) {
        const r = i / SUBDIVIDE;
        const c = j / SUBDIVIDE;
        const k = i * cols + j;
        positions[k * 3] = (c - (grid.cols - 1) / 2) * this.cellSize;
        positions[k * 3 + 1] = this.heightAt(r, c);
        positions[k * 3 + 2] = (r - (grid.rows - 1) / 2) * this.cellSize;
        uvs[k * 2] = j / (cols - 1);
        uvs[k * 2 + 1] = 1 - i / (rows - 1);
      }
    }
    const index: number[] = [];
    for (let i = 0; i < rows - 1; i += 1) {
      for (let j = 0; j < cols - 1; j += 1) {
        const a = i * cols + j;
        index.push(a, a + cols, a + 1, a + 1, a + cols, a + cols + 1);
      }
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    geometry.setAttribute("uv", new THREE.BufferAttribute(uvs, 2));
    geometry.setAttribute("color", new THREE.BufferAttribute(new Float32Array(rows * cols * 3), 3));
    geometry.setIndex(index);
    geometry.computeVertexNormals();

    this.dataMaterial = new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 0.93, metalness: 0.0 });
    const mesh = new THREE.Mesh(geometry, this.dataMaterial);
    mesh.receiveShadow = true;
    mesh.castShadow = true;
    this.mesh = mesh;
    this.geometry = geometry;
    this.terrainGroup.add(mesh);
    this.skirt = this.buildSkirt(grid);
    this.terrainGroup.add(this.skirt);

    this.buildSurfaceMaterials();
    this.rebuildContours();
    this.setLayer(layer, this.showLethal);
    this.rover.setScale((this.cellSize * ROVER_DISPLAY_CELLS) / ROVER_LENGTH_M);
    this.postfx.setAoRadius(this.cellSize * 1.2);
    if (!sameShape) this.resetCamera("three-quarter");
  }

  /** Side walls down to a base: the terrain reads as a block of ground, not a sheet. */
  private buildSkirt(grid: TerrainGrid): THREE.Mesh {
    const base = SKIRT_BASE;
    const ring: THREE.Vector3[] = [];
    const step = 1 / SUBDIVIDE;
    for (let c = 0; c < grid.cols - 1; c += step) ring.push(this.cellToWorld(0, c));
    for (let r = 0; r < grid.rows - 1; r += step) ring.push(this.cellToWorld(r, grid.cols - 1));
    for (let c = grid.cols - 1; c > 0; c -= step) ring.push(this.cellToWorld(grid.rows - 1, c));
    for (let r = grid.rows - 1; r > 0; r -= step) ring.push(this.cellToWorld(r, 0));
    ring.push(ring[0].clone());

    // u runs along the perimeter, v up the cliff in absolute height, so the
    // sediment layers stay level whatever the top edge does.
    const top = Math.max(...ring.map((p) => p.y));
    const height = top - base || 1;
    const vertices: number[] = [];
    const uvs: number[] = [];
    let travelled = 0;
    const perimeter = 2 * this.cellSize * ((grid.cols - 1) + (grid.rows - 1));
    for (let i = 0; i < ring.length - 1; i += 1) {
      const a = ring[i];
      const b = ring[i + 1];
      const ua = travelled / perimeter;
      travelled += Math.hypot(b.x - a.x, b.z - a.z);
      const ub = travelled / perimeter;
      const v = (y: number) => (y - base) / height;
      vertices.push(a.x, a.y, a.z, b.x, b.y, b.z, b.x, base, b.z);
      uvs.push(ua, v(a.y), ub, v(b.y), ub, 0);
      vertices.push(a.x, a.y, a.z, b.x, base, b.z, a.x, base, a.z);
      uvs.push(ua, v(a.y), ub, 0, ua, 0);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(vertices, 3));
    geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
    geometry.computeVertexNormals();
    this.skirtDataMaterial = new THREE.MeshStandardMaterial({
      color: srgb(mix(unit("surface-2"), unit("chart-4"), 0.12)),
      roughness: 1,
      side: THREE.DoubleSide,
    });
    const skirt = new THREE.Mesh(geometry, this.skirtDataMaterial);
    skirt.receiveShadow = true;
    return skirt;
  }

  /** A faint dot grid under everything: the Graphite signature, and a scale cue. */
  private buildFloor(): THREE.Points {
    const step = WORLD_SPAN / 24;
    const extent = WORLD_SPAN * 2.2;
    const points: number[] = [];
    for (let x = -extent; x <= extent; x += step) {
      for (let z = -extent; z <= extent; z += step) points.push(x, -WORLD_SPAN * 0.04, z);
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.Float32BufferAttribute(points, 3));
    return new THREE.Points(
      geometry,
      new THREE.PointsMaterial({ color: hex("line-strong"), size: 0.38, sizeAttenuation: true, toneMapped: false }),
    );
  }

  /**
   * Vertical exaggeration. A labelled knob rather than a baked-in value: colours
   * come from the data and stay honest at any setting; only the geometry's
   * apparent steepness changes.
   */
  setExaggeration(value: number): void {
    if (value === this.exaggeration) return;
    this.exaggeration = value;
    if (this.grid) {
      this.setTerrain(this.grid, this.currentLayer);
      this.setMarkers(this.lastMarkers);
      this.rebuildTracks();
    }
  }

  getExaggeration(): number {
    return this.exaggeration;
  }

  setLayer(layer: SceneLayer, showLethal = true): void {
    this.currentLayer = layer;
    this.showLethal = showLethal;
    const grid = this.grid;
    const geometry = this.geometry;
    const surface = layer === "surface" && this.surfaceMaterial !== null;
    this.applyLighting(surface);
    if (!grid || !geometry || !this.mesh) return;

    const colors = geometry.attributes.color as THREE.BufferAttribute;
    const lethalLinear = toLinear(mix(unit("danger"), unit("bg"), 0.45));
    const nearestLethal = (r: number, c: number) =>
      grid.layers.lethal[Math.min(grid.rows - 1, Math.round(r))][Math.min(grid.cols - 1, Math.round(c))];

    if (surface) {
      // A macro tint over the baked texture: dusty flats, darker rock where it
      // is steep, a little lighter with height. Not data - the legend says so.
      const [low, high] = grid.ranges.elevation_m ?? [0, 1];
      for (let i = 0; i < this.meshRows; i += 1) {
        for (let j = 0; j < this.meshCols; j += 1) {
          const r = i / SUBDIVIDE;
          const c = j / SUBDIVIDE;
          const slope = this.sample(grid.layers.slope_deg, r, c, false);
          const height = (this.sample(grid.layers.elevation_m, r, c, false) - low) / (high - low || 1);
          const rocky = THREE.MathUtils.smoothstep(slope, 9, 24);
          const lift = 0.86 + 0.26 * height;
          let tint: [number, number, number] = [
            (1.06 * (1 - rocky) + 0.66 * rocky) * lift,
            (1.0 * (1 - rocky) + 0.63 * rocky) * lift,
            (0.95 * (1 - rocky) + 0.64 * rocky) * lift,
          ];
          if (showLethal && nearestLethal(r, c)) tint = [1.7, 0.42, 0.36];
          colors.setXYZ(i * this.meshCols + j, tint[0], tint[1], tint[2]);
        }
      }
      this.mesh.material = this.surfaceMaterial!;
      if (this.skirt && this.skirtStrataMaterial) this.skirt.material = this.skirtStrataMaterial;
    } else {
      const key = (layer === "surface" ? "hazard" : layer) as TerrainLayerName;
      const values = grid.layers[key] ?? grid.layers.hazard;
      const [low, high] = grid.ranges[key] ?? [0, 1];
      const span = high - low || 1;
      const stops = layerRamps()[key] ?? layerRamps().hazard;
      for (let i = 0; i < this.meshRows; i += 1) {
        for (let j = 0; j < this.meshCols; j += 1) {
          const r = i / SUBDIVIDE;
          const c = j / SUBDIVIDE;
          // Lethal ground is drawn on every layer: inferring it from another tab
          // is how a route gets approved across a crater rim.
          let rgb: Rgb;
          if (showLethal && nearestLethal(r, c)) rgb = lethalLinear;
          // Interpolated in sRGB, like the legend's CSS gradient, then converted.
          else rgb = toLinear(ramp(stops, (this.sample(values, r, c, false) - low) / span));
          colors.setXYZ(i * this.meshCols + j, rgb[0], rgb[1], rgb[2]);
        }
      }
      this.mesh.material = this.dataMaterial!;
      if (this.skirt && this.skirtDataMaterial) this.skirt.material = this.skirtDataMaterial;
    }
    colors.needsUpdate = true;
    this.rebuildTracks();
  }

  /** Light, tone, sky and fog for a data layer or the photographic surface. */
  private applyLighting(surface: boolean): void {
    if (surface) {
      const sun = this.skyInfo ? new THREE.Vector3().fromArray(this.skyInfo.sun).normalize() : DATA_SUN;
      this.sun.position.copy(sun).multiplyScalar(WORLD_SPAN);
      this.sun.color.setRGB(1.0, 0.92, 0.82, THREE.SRGBColorSpace);
      this.sun.intensity = 3.3;
      this.hemisphere.visible = !this.marsEnv;
      this.renderer.toneMapping = THREE.AgXToneMapping;
      this.renderer.toneMappingExposure = 1.15;
      this.scene.background = null;
      this.scene.fog = this.surfaceFog;
      this.dome.visible = true;
      this.floor.visible = false;
      this.rover.setEnvironment(this.marsEnv ?? this.studioEnv, this.marsEnv ? 1.0 : 0.55);
      this.styleContours(true);
    } else {
      this.sun.position.copy(DATA_SUN).multiplyScalar(WORLD_SPAN);
      this.sun.color.set(0xffffff);
      this.sun.intensity = 3.0;
      this.hemisphere.visible = true;
      this.renderer.toneMapping = THREE.NeutralToneMapping;
      this.renderer.toneMappingExposure = 1.0;
      this.scene.background = this.dataBackground;
      this.scene.fog = this.dataFog;
      this.dome.visible = false;
      this.floor.visible = true;
      this.rover.setEnvironment(this.studioEnv, 0.55);
      this.styleContours(false);
    }
    for (const beacon of this.markerGroup.children) {
      beacon.traverse((o) => {
        const m = (o as THREE.Mesh).material as THREE.MeshStandardMaterial | undefined;
        if (m?.isMeshStandardMaterial && o.userData.asset) {
          m.envMap = surface ? this.marsEnv : this.studioEnv;
          m.needsUpdate = true;
        }
      });
    }
  }

  setContours(visible: boolean): void {
    this.showContours = visible;
    this.contourGroup.visible = visible;
  }

  /** Marching squares over the elevation grid, at a round interval. */
  private rebuildContours(): void {
    this.clearGroup(this.contourGroup);
    const grid = this.grid;
    if (!grid) return;
    const [low, high] = grid.ranges.elevation_m ?? [0, 1];
    const interval = niceInterval((high - low) / 14);
    if (!(interval > 0)) return;

    const minor: number[] = [];
    const major: number[] = [];
    const e = grid.layers.elevation_m;
    const lift = this.cellSize * 0.05;
    const first = Math.ceil(low / interval) * interval;

    for (let level = first, k = Math.round(first / interval); level <= high; level += interval, k += 1) {
      const out = k % 5 === 0 ? major : minor;
      for (let r = 0; r < grid.rows - 1; r += 1) {
        for (let c = 0; c < grid.cols - 1; c += 1) {
          const a = e[r][c];
          const b = e[r][c + 1];
          const d = e[r + 1][c];
          const f = e[r + 1][c + 1];
          const hits: [number, number][] = [];
          const edge = (z0: number, z1: number, r0: number, c0: number, r1: number, c1: number) => {
            if ((z0 < level) !== (z1 < level)) {
              const t = (level - z0) / (z1 - z0);
              hits.push([r0 + (r1 - r0) * t, c0 + (c1 - c0) * t]);
            }
          };
          edge(a, b, r, c, r, c + 1);
          edge(b, f, r, c + 1, r + 1, c + 1);
          edge(d, f, r + 1, c, r + 1, c + 1);
          edge(a, d, r, c, r + 1, c);
          for (let i = 0; i + 1 < hits.length; i += 2) {
            const p = this.cellToWorld(hits[i][0], hits[i][1], lift);
            const q = this.cellToWorld(hits[i + 1][0], hits[i + 1][1], lift);
            out.push(p.x, p.y, p.z, q.x, q.y, q.z);
          }
        }
      }
    }

    const line = (positions: number[], opacity: number) => {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
      return new THREE.LineSegments(
        geometry,
        new THREE.LineBasicMaterial({ color: hex("fg"), transparent: true, opacity, depthWrite: false, toneMapped: false }),
      );
    };
    if (minor.length) this.contourGroup.add(line(minor, 0.13));
    if (major.length) this.contourGroup.add(line(major, 0.32));
    this.contourGroup.visible = this.showContours;
    this.styleContours(this.currentLayer === "surface" && this.surfaceMaterial !== null);
  }

  /**
   * Light lines over colour data; faint dark lines, like a printed survey map,
   * over the photographic surface, where white lines read as terracing.
   */
  private styleContours(surface: boolean): void {
    this.contourGroup.children.forEach((child, i) => {
      const material = (child as THREE.LineSegments).material as THREE.LineBasicMaterial;
      const major = this.contourGroup.children.length === 2 ? i === 1 : true;
      if (surface) {
        material.color.setRGB(0.16, 0.09, 0.05, THREE.SRGBColorSpace);
        material.opacity = major ? 0.42 : 0.2;
      } else {
        material.color.set(hex("fg"));
        material.opacity = major ? 0.32 : 0.13;
      }
    });
  }

  /** The contour interval in metres, for the legend. */
  contourInterval(): number {
    const grid = this.grid;
    if (!grid) return 0;
    const [low, high] = grid.ranges.elevation_m ?? [0, 1];
    return niceInterval((high - low) / 14);
  }

  // --- routes ----------------------------------------------------------------------

  setRoutes(routes: RouteSpec[]): void {
    this.clearGroup(this.routeGroup);
    this.executedPath = routes.find((r) => r.id === "executed")?.waypoints ?? [];
    this.rebuildTracks();
    if (!this.grid) return;

    for (const route of routes) {
      if (route.waypoints.length < 2) continue;
      const lift = route.emphasis ? this.cellSize * 0.32 : this.cellSize * 0.22;
      const points = densify(route.waypoints.map((p) => this.pixelToCell(p))).map(([r, c]) =>
        this.cellToWorld(r, c, lift),
      );
      if (points.length < 2) continue;

      if (route.dashed) {
        // A dotted trail: what was planned, under what was driven.
        const dot = new THREE.SphereGeometry(this.cellSize * 0.11, 8, 6);
        const mat = new THREE.MeshBasicMaterial({ color: route.color, transparent: true, opacity: 0.85 });
        const dots = new THREE.InstancedMesh(dot, mat, Math.ceil(points.length / 2));
        const m = new THREE.Matrix4();
        let n = 0;
        for (let i = 0; i < points.length; i += 2) {
          m.makeTranslation(points[i].x, points[i].y, points[i].z);
          dots.setMatrixAt(n, m);
          n += 1;
        }
        dots.count = n;
        dots.name = route.id;
        this.routeGroup.add(dots);
        continue;
      }

      const curve = new THREE.CatmullRomCurve3(points, false, "centripetal");
      const radius = this.cellSize * (route.emphasis ? 0.17 : 0.12);
      const tube = new THREE.Mesh(
        new THREE.TubeGeometry(curve, Math.min(1200, points.length * 3), radius, 8, false),
        new THREE.MeshStandardMaterial({
          color: route.color,
          emissive: route.color,
          emissiveIntensity: route.emphasis ? 0.55 : 0.3,
          roughness: 0.45,
        }),
      );
      tube.castShadow = true;
      tube.name = route.id;
      this.routeGroup.add(tube);
    }
  }

  /** Wheel tracks behind the rover, in the surface view only: on a data layer they would be noise. */
  private rebuildTracks(): void {
    this.clearGroup(this.trackGroup);
    if (!this.grid || this.currentLayer !== "surface" || this.executedPath.length < 2) return;
    const scale = (this.cellSize * ROVER_DISPLAY_CELLS) / ROVER_LENGTH_M;
    const points = densify(this.executedPath.map((p) => this.pixelToCell(p))).map(([r, c]) => this.cellToWorld(r, c));
    const tracks = wheelTracks(points, ROVER_GAUGE_M * scale, ROVER_WHEEL_W_M * scale, this.heightAtWorld);
    if (tracks) this.trackGroup.add(tracks);
  }

  // --- markers ----------------------------------------------------------------------

  setMarkers(markers: MarkerSpec[]): void {
    this.lastMarkers = markers;
    this.clearGroup(this.markerGroup);
    this.beacons = [];
    this.beaconLights = [];
    if (!this.grid) return;
    const s = this.cellSize;
    const modelScale = (this.cellSize * ROVER_DISPLAY_CELLS) / ROVER_LENGTH_M;

    for (const marker of markers) {
      const [row, col] = this.pixelToCell(marker.point);
      const ground = this.cellToWorld(row, col);
      const group = new THREE.Group();
      group.position.copy(ground);
      group.name = marker.id;
      const solid = new THREE.MeshStandardMaterial({
        color: marker.color,
        emissive: marker.color,
        emissiveIntensity: 0.35,
        roughness: 0.5,
      });

      if (marker.shape === "start") {
        const pad = new THREE.Mesh(new THREE.CylinderGeometry(s * 1.1, s * 1.25, s * 0.18, 6), solid);
        pad.position.y = s * 0.09;
        group.add(pad);
        group.add(this.label(marker.label ?? "START", marker.color, s * 3.2));
      } else if (marker.shape === "goal") {
        if (this.beaconAsset) {
          // The Blender survey beacon, at the rover's display scale.
          const beacon = this.beaconAsset.clone(true);
          beacon.scale.setScalar(modelScale);
          beacon.traverse((o) => {
            const mesh = o as THREE.Mesh;
            if (!mesh.isMesh) return;
            mesh.castShadow = true;
            mesh.receiveShadow = true;
            mesh.material = (mesh.material as THREE.Material).clone();
            const m = mesh.material as THREE.MeshStandardMaterial;
            m.envMap = this.currentLayer === "surface" ? this.marsEnv : this.studioEnv;
            mesh.userData.asset = true;
            if (o.name === "beacon_light") this.beaconLights.push(m);
          });
          group.add(beacon);
        } else {
          const pole = new THREE.Mesh(new THREE.CylinderGeometry(s * 0.07, s * 0.07, s * 4.2, 8), solid);
          pole.position.y = s * 2.1;
          group.add(pole);
        }
        const ring = new THREE.Mesh(
          new THREE.TorusGeometry(s * 1.15, s * 0.07, 8, 40),
          new THREE.MeshBasicMaterial({ color: marker.color, transparent: true, opacity: 0.9 }),
        );
        ring.rotation.x = -Math.PI / 2;
        ring.position.y = s * 0.12;
        group.add(ring);
        this.beacons.push(ring);
        group.add(this.label(marker.label ?? "GOAL", marker.color, s * 5.0));
      } else if (marker.shape === "target") {
        const gem = new THREE.Mesh(new THREE.OctahedronGeometry(s * 0.62), solid);
        gem.position.y = s * 1.5;
        group.add(gem);
        const halo = new THREE.Mesh(
          new THREE.RingGeometry(s * 0.85, s * 1.0, 32),
          new THREE.MeshBasicMaterial({ color: marker.color, transparent: true, opacity: 0.55, side: THREE.DoubleSide }),
        );
        halo.rotation.x = -Math.PI / 2;
        halo.position.y = s * 0.08;
        group.add(halo);
        if (marker.label) group.add(this.label(marker.label, marker.color, s * 2.9));
      } else {
        const mast = new THREE.Mesh(new THREE.CylinderGeometry(s * 0.1, s * 0.28, s * 3.0, 8), solid);
        mast.position.y = s * 1.5;
        group.add(mast);
        if (marker.label) group.add(this.label(marker.label, marker.color, s * 3.8));
      }
      group.traverse((o) => {
        if ((o as THREE.Mesh).isMesh) o.castShadow = true;
      });
      this.markerGroup.add(group);
    }
  }

  /** A text label that always faces the camera, in the console's mono face. */
  private label(text: string, color: number, height: number): THREE.Sprite {
    const canvas = document.createElement("canvas");
    const ctx = canvas.getContext("2d");
    const font = '500 40px "Geist Mono", ui-monospace, Menlo, monospace';
    if (ctx) {
      ctx.font = font;
      const width = Math.ceil(ctx.measureText(text).width) + 36;
      canvas.width = width;
      canvas.height = 64;
      ctx.font = font;
      ctx.fillStyle = `rgb(${unit("surface").map((v) => Math.round(v * 255)).join(" ")} / 0.88)`;
      ctx.beginPath();
      ctx.roundRect(0, 0, width, 64, 10);
      ctx.fill();
      const c = new THREE.Color(color);
      ctx.fillStyle = `rgb(${Math.round(c.r * 255)} ${Math.round(c.g * 255)} ${Math.round(c.b * 255)})`;
      ctx.textBaseline = "middle";
      ctx.fillText(text, 18, 34);
    }
    const texture = new THREE.CanvasTexture(canvas);
    texture.colorSpace = THREE.SRGBColorSpace;
    const sprite = new THREE.Sprite(
      new THREE.SpriteMaterial({ map: texture, depthTest: false, transparent: true, toneMapped: false }),
    );
    const aspect = canvas.width / canvas.height;
    const h = this.cellSize * 1.15;
    sprite.scale.set(h * aspect, h, 1);
    sprite.position.y = height;
    sprite.renderOrder = 10;
    return sprite;
  }

  // --- rover -----------------------------------------------------------------------

  setRover(pose: RoverPose | null): void {
    this.clearGroup(this.ringGroup);
    if (!pose || !this.grid) {
      this.rover.root.visible = false;
      return;
    }
    const [row, col] = this.pixelToCell(pose.point);
    const position = this.cellToWorld(row, col);
    let heading = new THREE.Vector3(1, 0, 0);
    if (pose.from) {
      const [fr, fc] = this.pixelToCell(pose.from);
      const d = new THREE.Vector3(col - fc, 0, row - fr);
      if (d.lengthSq() > 1e-6) heading = d.normalize();
    }
    const firstPlacement = !this.rover.root.visible;
    this.rover.root.visible = true;
    this.rover.setPose(position, heading, this.normalAt(row, col), firstPlacement);
    // The opening view is framed around the rover once there is one, unless
    // the operator has already taken the camera.
    if (firstPlacement && !this.userMoved && this.lastView === "three-quarter") this.resetCamera("three-quarter");

    // True scale, so drawn only when it reaches past the (enlarged) model: a
    // ring hidden under the rover would claim a precision the view lacks.
    const radius = (pose.sensorRangeM ?? 0) * (this.cellSize / this.grid.meters_per_cell);
    if (radius > this.cellSize * ROVER_DISPLAY_CELLS * 0.6) {
      this.ringGroup.add(this.drapedRing(row, col, radius, hex("info"), 0.55, 0.05));
    }
  }

  /** How many times larger than life the rover is drawn, for the overlay. */
  roverScale(): number {
    if (!this.grid) return 0;
    return (ROVER_DISPLAY_CELLS * this.grid.meters_per_cell) / ROVER_LENGTH_M;
  }

  /** A ring at true scale, following the ground rather than cutting through hills. */
  private drapedRing(row: number, col: number, radiusWorld: number, color: number, opacity: number, thickness: number): THREE.Mesh {
    const segments = 72;
    const points: THREE.Vector3[] = [];
    for (let i = 0; i < segments; i += 1) {
      const a = (i / segments) * Math.PI * 2;
      const r = row + (Math.sin(a) * radiusWorld) / this.cellSize;
      const c = col + (Math.cos(a) * radiusWorld) / this.cellSize;
      points.push(this.cellToWorld(r, c, this.cellSize * 0.18));
    }
    const curve = new THREE.CatmullRomCurve3(points, true);
    return new THREE.Mesh(
      new THREE.TubeGeometry(curve, segments * 2, this.cellSize * thickness * 2, 6, true),
      new THREE.MeshBasicMaterial({ color, transparent: true, opacity, depthWrite: false }),
    );
  }

  // --- interaction --------------------------------------------------------------------

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
    return { x: Math.round(col * this.grid.pixel_scale), y: Math.round(row * this.grid.pixel_scale) };
  }

  resetCamera(view: CameraView): void {
    this.lastView = view;
    this.userMoved = false;
    this.follow = view === "follow";
    if (this.follow) {
      // Behind the rover and a little to one side, looking where it is headed.
      const target = this.rover.root.visible ? this.rover.root.position.clone() : new THREE.Vector3();
      const forward = new THREE.Vector3(1, 0, 0).applyQuaternion(this.rover.root.quaternion).setY(0);
      if (forward.lengthSq() < 1e-6) forward.set(1, 0, 0);
      forward.normalize();
      const side = new THREE.Vector3(-forward.z, 0, forward.x);
      const back = WORLD_SPAN * 0.13;
      this.controls.target.copy(target);
      this.camera.position
        .copy(target)
        .addScaledVector(forward, -back)
        .addScaledVector(side, back * 0.35)
        .setY(target.y + WORLD_SPAN * 0.075);
      this.controls.update();
      return;
    }

    let side = new THREE.Vector2(-0.68, 0.74);
    let elevation = 32;
    if (view === "top") {
      side = new THREE.Vector2(0, 1); // north stays up the screen
      elevation = 89.9;
    } else if (view === "orbit") {
      side = new THREE.Vector2(0, 1);
      elevation = 23;
    } else if (this.rover.root.visible) {
      // Stand on the rover's side of the map, a little off its diagonal: what
      // is being planned for sits in the foreground and the route runs away.
      const p = this.rover.root.position;
      const out = new THREE.Vector2(p.x, p.z);
      if (out.length() > WORLD_SPAN * 0.12) side = out.normalize().rotateAround(new THREE.Vector2(), 0.42);
    }
    this.fitView(side, THREE.MathUtils.degToRad(elevation));
  }

  /**
   * Put the camera along ``side`` at ``elevation`` radians, as close as it can
   * be while the whole terrain block stays in frame for the canvas's current
   * aspect - so the same view works on a phone and on a wide monitor.
   */
  private fitView(side: THREE.Vector2, elevation: number): void {
    const direction = new THREE.Vector3(
      side.x * Math.cos(elevation),
      Math.sin(elevation),
      side.y * Math.cos(elevation),
    ).normalize();
    const corners: THREE.Vector3[] = [];
    let target = new THREE.Vector3();
    const grid = this.grid;
    if (grid) {
      const halfX = (this.cellSize * (grid.cols - 1)) / 2;
      const halfZ = (this.cellSize * (grid.rows - 1)) / 2;
      const top = (grid.ranges.elevation_m?.[1] ?? 0) * this.verticalScale;
      for (const x of [-halfX, halfX]) {
        for (const z of [-halfZ, halfZ]) {
          corners.push(new THREE.Vector3(x, SKIRT_BASE, z), new THREE.Vector3(x, top, z));
        }
      }
      target = new THREE.Vector3(0, (SKIRT_BASE + top) / 2, 0);
    }

    const place = (aim: THREE.Vector3, distance: number) => {
      this.camera.position.copy(aim).addScaledVector(direction, distance);
      this.camera.lookAt(aim);
      this.camera.updateMatrixWorld();
    };
    const fits = (aim: THREE.Vector3, distance: number) => {
      place(aim, distance);
      return corners.every((corner) => {
        if (corner.clone().applyMatrix4(this.camera.matrixWorldInverse).z > -this.camera.near) return false;
        const p = corner.clone().project(this.camera);
        return Math.abs(p.x) <= 0.92 && Math.abs(p.y) <= 0.88;
      });
    };
    // Whether the block fits only gets easier with distance, so bisect it.
    const nearest = (aim: THREE.Vector3) => {
      let near = WORLD_SPAN * 0.2;
      let far = WORLD_SPAN * 4;
      for (let i = 0; i < 22; i += 1) {
        const mid = (near + far) / 2;
        if (fits(aim, mid)) far = mid;
        else near = mid;
      }
      return far;
    };

    let distance = WORLD_SPAN * 1.1;
    if (corners.length) {
      distance = nearest(target);
      // Perspective draws the near half of the block larger than the far half,
      // so aiming at its middle wastes the top of the frame. Slide the aim
      // along the ground - towards the camera, then sideways - to wherever
      // the block can be drawn largest.
      const away = new THREE.Vector3(-side.x, 0, -side.y);
      const across = new THREE.Vector3(side.y, 0, -side.x);
      for (const axis of [away, across]) {
        const at = (offset: number) => target.clone().addScaledVector(axis, offset);
        let lo = -WORLD_SPAN * 0.3;
        let hi = WORLD_SPAN * 0.3;
        for (let i = 0; i < 16; i += 1) {
          const a = lo + (hi - lo) / 3;
          const b = hi - (hi - lo) / 3;
          if (nearest(at(a)) < nearest(at(b))) hi = b;
          else lo = a;
        }
        const aim = at((lo + hi) / 2);
        const d = nearest(aim);
        if (d < distance) {
          target = aim;
          distance = d;
        }
      }
    }
    place(target, distance);
    this.controls.target.copy(target);
    this.controls.update();
  }

  /** Metres per CSS pixel at the view centre, for the scale bar. */
  metresPerPixel(viewportHeightPx: number): number {
    if (!this.grid || viewportHeightPx <= 0) return 0;
    const distance = this.camera.position.distanceTo(this.controls.target);
    const visibleHeight = 2 * distance * Math.tan(THREE.MathUtils.degToRad(this.camera.fov / 2));
    return (visibleHeight / viewportHeightPx) * (this.grid.meters_per_cell / this.cellSize);
  }

  /** Camera azimuth in radians, for the compass: 0 when north is up the screen. */
  azimuth(): number {
    return this.controls.getAzimuthalAngle();
  }

  /** "auto" starts balanced and moves once, up or down, on measured frame times. */
  setQuality(quality: Quality | "auto"): void {
    this.autoQuality = quality === "auto";
    this.frameTimes = [];
    this.framesSeen = 0;
    this.postfx.setQuality(quality === "auto" ? "balanced" : quality);
    const rect = this.canvas.getBoundingClientRect();
    this.postfx.setSize(Math.max(1, rect.width), Math.max(1, rect.height));
  }

  /** The preset actually in use, which under "auto" can differ from the request. */
  quality(): Quality {
    return this.postfx.quality;
  }

  resize(width: number, height: number): void {
    if (width === 0 || height === 0) return;
    this.postfx.setSize(width, height);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    // Until the operator takes the camera, keep the view fitted to the canvas.
    if (!this.userMoved && !this.follow && this.grid) this.resetCamera(this.lastView);
  }

  dispose(): void {
    this.disposed = true;
    cancelAnimationFrame(this.frame);
    for (const group of [this.terrainGroup, this.contourGroup, this.routeGroup, this.trackGroup, this.markerGroup, this.ringGroup]) {
      this.clearGroup(group);
    }
    for (const material of [this.surfaceMaterial, this.skirtStrataMaterial]) {
      if (!material) continue;
      for (const tex of [material.map, material.normalMap, material.roughnessMap]) tex?.dispose();
      material.dispose();
    }
    this.rover.dispose();
    this.postfx.dispose();
    this.studioEnv.dispose();
    this.marsEnv?.dispose();
    this.pmrem.dispose();
    this.controls.dispose();
    this.renderer.dispose();
  }

  // --- internals ------------------------------------------------------------------------

  private animate = (): void => {
    if (this.disposed) return;
    this.frame = requestAnimationFrame(this.animate);
    const dt = Math.min(this.clock.getDelta(), 0.1);
    this.rover.update(dt, this.reducedMotion);

    if (this.follow && this.rover.root.visible) {
      // Carry the camera with the rover without taking the operator's orbit away.
      const delta = this.rover.root.position.clone().sub(this.controls.target);
      this.controls.target.add(delta);
      this.camera.position.add(delta);
    }
    if (!this.reducedMotion) {
      const t = this.clock.elapsedTime;
      for (const beacon of this.beacons) {
        const pulse = 1 + 0.12 * Math.sin(t * 2.4);
        beacon.scale.set(pulse, pulse, pulse);
      }
      for (const light of this.beaconLights) light.emissiveIntensity = 6 + 4 * Math.sin(t * 3.1);
    }
    this.controls.update();
    this.postfx.render();
    this.tuneQuality(dt);
  };

  /** Under "auto": skip the first frames (shader compiles), then judge 90 frames once. */
  private tuneQuality(dt: number): void {
    if (!this.autoQuality || document.hidden) return;
    this.framesSeen += 1;
    if (this.framesSeen < 45) return;
    this.frameTimes.push(dt);
    if (this.frameTimes.length < 90) return;
    const average = this.frameTimes.reduce((a, b) => a + b, 0) / this.frameTimes.length;
    this.autoQuality = false;
    if (average < 1 / 55) this.postfx.setQuality("cinematic");
    else if (average > 1 / 24) this.postfx.setQuality("performance");
    const rect = this.canvas.getBoundingClientRect();
    this.postfx.setSize(Math.max(1, rect.width), Math.max(1, rect.height));
  }

  private clearGroup(group: THREE.Group): void {
    // WebGL resources are not GC'd with their JS object, and a traverse replay
    // rebuilds route groups every frame of the scrub. Loaded assets share their
    // geometry with the loader's cache, so only their cloned materials go.
    for (const child of [...group.children]) {
      group.remove(child);
      child.traverse((object) => {
        const withGeometry = object as THREE.Mesh;
        if (!object.userData.asset) withGeometry.geometry?.dispose();
        const material = withGeometry.material as THREE.Material | THREE.Material[] | undefined;
        const mats = Array.isArray(material) ? material : material ? [material] : [];
        for (const m of mats) {
          if (!object.userData.asset) (m as THREE.SpriteMaterial).map?.dispose();
          m.dispose();
        }
      });
    }
  }
}

/** A round contour interval: 0.5, 1, 2, 2.5, 5, 10 ... metres. */
function niceInterval(raw: number): number {
  if (!(raw > 0)) return 0;
  const power = 10 ** Math.floor(Math.log10(raw));
  const scaled = raw / power;
  const step = scaled < 1.5 ? 1 : scaled < 2.25 ? 2 : scaled < 3.5 ? 2.5 : scaled < 7.5 ? 5 : 10;
  return step * power;
}

/** Add points between waypoints that are more than a cell apart, so tubes hug the ground. */
function densify(cells: [number, number][]): [number, number][] {
  const out: [number, number][] = [];
  for (let i = 0; i < cells.length; i += 1) {
    const current = cells[i];
    if (i > 0) {
      const previous = cells[i - 1];
      const gap = Math.hypot(current[0] - previous[0], current[1] - previous[1]);
      const steps = Math.floor(gap / 0.5);
      for (let s = 1; s < steps; s += 1) {
        const t = s / steps;
        out.push([previous[0] + (current[0] - previous[0]) * t, previous[1] + (current[1] - previous[1]) * t]);
      }
    }
    out.push(current);
  }
  return out;
}
