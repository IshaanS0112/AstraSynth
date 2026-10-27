/**
 * What surrounds the terrain in the photographic view: a sky dome, the surface
 * and cliff materials, and the wheel tracks the rover leaves.
 *
 * None of this is data. The surface view is labelled as a visual, the data
 * layers keep their own materials, and lethal ground is still marked on top.
 */

import * as THREE from "three";

import type { SurfaceSet } from "./assets";

/** A gradient dome: Mars haze at the horizon, fading into the console's dark at the zenith. */
export function skyDome(horizon: THREE.Color, zenith: THREE.Color, ground: THREE.Color, sun: THREE.Vector3): THREE.Mesh {
  const material = new THREE.ShaderMaterial({
    side: THREE.BackSide,
    depthWrite: false,
    fog: false,
    uniforms: {
      horizon: { value: horizon },
      zenith: { value: zenith },
      ground: { value: ground },
      sunDir: { value: sun.clone().normalize() },
    },
    vertexShader: /* glsl */ `
      varying vec3 vDir;
      void main() {
        vDir = position;
        vec4 clip = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        gl_Position = clip.xyww;
      }
    `,
    fragmentShader: /* glsl */ `
      uniform vec3 horizon;
      uniform vec3 zenith;
      uniform vec3 ground;
      uniform vec3 sunDir;
      varying vec3 vDir;
      void main() {
        vec3 d = normalize(vDir);
        float h = d.y;
        vec3 sky = mix(horizon, zenith, smoothstep(0.0, 0.62, h));
        vec3 below = mix(horizon * 0.55, ground, smoothstep(0.0, 0.2, -h));
        vec3 colour = h >= 0.0 ? sky : below;
        float s = max(dot(d, sunDir), 0.0);
        colour += horizon * (pow(s, 6.0) * 0.35 + pow(s, 64.0) * 0.5) * smoothstep(-0.1, 0.2, h);
        gl_FragColor = vec4(colour, 1.0);
        #include <tonemapping_fragment>
        #include <colorspace_fragment>
      }
    `,
  });
  const dome = new THREE.Mesh(new THREE.SphereGeometry(900, 48, 24), material);
  dome.renderOrder = -10;
  dome.frustumCulled = false;
  return dome;
}

/** Clone a texture set so this scene can give it its own repeat and anisotropy. */
function cloneSet(set: SurfaceSet, repeat: THREE.Vector2, anisotropy: number): SurfaceSet {
  const one = (t: THREE.Texture) => {
    const c = t.clone();
    c.repeat.copy(repeat);
    c.anisotropy = anisotropy;
    c.needsUpdate = true;
    return c;
  };
  return { albedo: one(set.albedo), normal: one(set.normal), roughness: one(set.roughness) };
}

/**
 * Regolith over the terrain. Vertex colours carry a macro tint (dusty flats,
 * darker rock on steep ground), and the albedo is sampled at two scales and
 * blended, so the tile does not read as a grid at any zoom.
 */
export function regolithMaterial(set: SurfaceSet, repeat: number, anisotropy: number, envMap: THREE.Texture | null): THREE.MeshStandardMaterial {
  const t = cloneSet(set, new THREE.Vector2(repeat, repeat), anisotropy);
  const material = new THREE.MeshStandardMaterial({
    map: t.albedo,
    normalMap: t.normal,
    normalScale: new THREE.Vector2(1.1, 1.1),
    roughnessMap: t.roughness,
    roughness: 1,
    metalness: 0,
    vertexColors: true,
    envMap,
    envMapIntensity: 0.9,
  });
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace(
      "#include <map_fragment>",
      /* glsl */ `
        #ifdef USE_MAP
          vec4 near = texture2D( map, vMapUv );
          vec4 far = texture2D( map, vMapUv * 0.173 + vec2( 0.31, 0.57 ) );
          vec4 sampledDiffuseColor = mix( near, far, 0.42 );
          diffuseColor *= sampledDiffuseColor;
        #endif
      `,
    );
  };
  material.customProgramCacheKey = () => "regolith-two-scale";
  return material;
}

/** Sediment layers on the cliff faces of the terrain block. */
export function strataMaterial(set: SurfaceSet, repeatU: number, anisotropy: number, envMap: THREE.Texture | null): THREE.MeshStandardMaterial {
  const t = cloneSet(set, new THREE.Vector2(repeatU, 1), anisotropy);
  for (const tex of [t.albedo, t.normal, t.roughness]) tex.wrapT = THREE.ClampToEdgeWrapping;
  return new THREE.MeshStandardMaterial({
    map: t.albedo,
    normalMap: t.normal,
    roughnessMap: t.roughness,
    roughness: 1,
    metalness: 0,
    envMap,
    envMapIntensity: 0.7,
  });
}

/** Grouser marks: a chevron every few texels down the length of a track. */
function grouserTexture(): THREE.CanvasTexture {
  const canvas = document.createElement("canvas");
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.fillStyle = "rgb(255 255 255)"; // lint-ok: a luminance mask the material colour multiplies
    ctx.fillRect(0, 0, 64, 64);
    ctx.strokeStyle = "rgb(150 150 150)"; // lint-ok: same mask, the grouser imprint
    ctx.lineWidth = 7;
    ctx.beginPath();
    ctx.moveTo(4, 8);
    ctx.lineTo(32, 26);
    ctx.lineTo(60, 8);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(4, 40);
    ctx.lineTo(32, 58);
    ctx.lineTo(60, 40);
    ctx.stroke();
  }
  const texture = new THREE.CanvasTexture(canvas);
  texture.wrapS = texture.wrapT = THREE.RepeatWrapping;
  texture.colorSpace = THREE.SRGBColorSpace;
  return texture;
}

/**
 * Two wheel tracks along ``path`` (world positions on the surface), ``gauge``
 * apart, each ``width`` wide, draped with ``heightAt``. Darker than the ground
 * around them, the way disturbed regolith is.
 */
export function wheelTracks(
  path: THREE.Vector3[],
  gauge: number,
  width: number,
  heightAt: (x: number, z: number) => number,
): THREE.Mesh | null {
  if (path.length < 2) return null;
  const positions: number[] = [];
  const uvs: number[] = [];
  const indices: number[] = [];
  const lift = width * 0.04;
  let base = 0;
  for (const side of [-1, 1]) {
    let travelled = 0;
    for (let i = 0; i < path.length; i += 1) {
      const p = path[i];
      const ahead = path[Math.min(i + 1, path.length - 1)];
      const behind = path[Math.max(i - 1, 0)];
      const dir = new THREE.Vector3(ahead.x - behind.x, 0, ahead.z - behind.z);
      if (dir.lengthSq() < 1e-9) dir.set(1, 0, 0);
      dir.normalize();
      const lateral = new THREE.Vector3(-dir.z, 0, dir.x);
      if (i > 0) travelled += Math.hypot(p.x - path[i - 1].x, p.z - path[i - 1].z);
      const centre = p.clone().addScaledVector(lateral, (side * gauge) / 2);
      for (const edge of [-1, 1]) {
        const v = centre.clone().addScaledVector(lateral, (edge * width) / 2);
        positions.push(v.x, heightAt(v.x, v.z) + lift, v.z);
        uvs.push(edge < 0 ? 0 : 1, (travelled / width) * 2.5);
      }
      if (i > 0) {
        // Wound so the faces point up: the strip is seen from above.
        const a = base + (i - 1) * 2;
        indices.push(a, a + 1, a + 2, a + 1, a + 3, a + 2);
      }
    }
    base += path.length * 2;
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("uv", new THREE.Float32BufferAttribute(uvs, 2));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  const material = new THREE.MeshStandardMaterial({
    color: new THREE.Color().setRGB(0.3, 0.2, 0.15, THREE.SRGBColorSpace),
    map: grouserTexture(),
    roughness: 1,
    transparent: true,
    opacity: 0.72,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
    polygonOffsetUnits: -2,
  });
  const mesh = new THREE.Mesh(geometry, material);
  mesh.receiveShadow = true;
  mesh.renderOrder = 1;
  return mesh;
}
