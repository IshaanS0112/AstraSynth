/**
 * The Blender-built assets the 3-D view loads: models, surface textures, sky.
 *
 * Built by assets/blender/build.py into public/3d/. Each is fetched once per
 * page and shared between scenes; every loader resolves to null on failure, so
 * a scene can always fall back to what it can draw itself.
 */

import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { RGBELoader } from "three/examples/jsm/loaders/RGBELoader.js";

const BASE = "/3d/";

export interface SkyInfo {
  /** Direction towards the sun in world space, as measured from the HDR. */
  sun: [number, number, number];
  sunElevationDeg: number;
}

export interface SurfaceSet {
  albedo: THREE.Texture;
  normal: THREE.Texture;
  roughness: THREE.Texture;
}

const cache = new Map<string, Promise<unknown>>();

function once<T>(key: string, load: () => Promise<T>): Promise<T | null> {
  if (!cache.has(key)) {
    cache.set(
      key,
      load().catch((error) => {
        console.warn(`3-D asset ${key} unavailable, using the fallback`, error);
        return null;
      }),
    );
  }
  return cache.get(key) as Promise<T | null>;
}

let gltf: GLTFLoader | null = null;
const textures = new THREE.TextureLoader();

/** A model's scene graph. Callers clone it before adding it to a scene. */
export function loadModel(name: "rover" | "beacon"): Promise<THREE.Group | null> {
  return once(`model:${name}`, async () => {
    gltf ??= new GLTFLoader();
    const result = await gltf.loadAsync(`${BASE}${name}.glb`);
    return result.scene;
  });
}

/** A tileable texture set; clone the textures to give them their own repeat. */
export function loadSurface(name: "regolith" | "strata"): Promise<SurfaceSet | null> {
  return once(`surface:${name}`, async () => {
    const load = async (kind: string, colour: boolean) => {
      const texture = await textures.loadAsync(`${BASE}${name}_${kind}.jpg`);
      texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
      texture.colorSpace = colour ? THREE.SRGBColorSpace : THREE.NoColorSpace;
      return texture;
    };
    const [albedo, normal, roughness] = await Promise.all([
      load("albedo", true),
      load("normal", false),
      load("roughness", false),
    ]);
    return { albedo, normal, roughness };
  });
}

/** The equirectangular Mars sky, for image-based lighting. */
export function loadSky(): Promise<THREE.DataTexture | null> {
  return once("sky:hdr", async () => {
    const texture = await new RGBELoader().loadAsync(`${BASE}mars_sky.hdr`);
    texture.mapping = THREE.EquirectangularReflectionMapping;
    return texture;
  });
}

export function loadSkyInfo(): Promise<SkyInfo | null> {
  return once("sky:info", async () => {
    const response = await fetch(`${BASE}sky.json`);
    if (!response.ok) throw new Error(`sky.json: ${response.status}`);
    return (await response.json()) as SkyInfo;
  });
}
