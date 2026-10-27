/**
 * A six-wheeled planetary rover, built from primitives.
 *
 * Laid out after the rocker-bogie rovers (Sojourner through Perseverance):
 * a warm electronics box on a differential, a rocker per side carrying the front
 * wheel, a bogie carrying the middle and rear wheels, a remote-sensing mast with
 * a stereo camera head, a high-gain antenna, an RTG at the back and a stowed
 * robotic arm. Proportions are illustrative, not any real vehicle's drawings.
 *
 * Built once and moved, never rebuilt: a traverse replay updates the pose every
 * frame. Model space is metres, +X forward, +Y up.
 *
 * This procedural model is the fallback. When the Blender-built rover.glb loads,
 * ``useAsset`` hides it and binds the same animation to the asset's named nodes:
 * wheel_FL ... wheel_RR spin about their axles, steer_FL ... steer_RR turn the
 * corner wheels into the curve, mast_head pans, status_light pulses. Both share
 * one frame and one scale, so nothing else changes.
 */

import * as THREE from "three";

import { hex, mix, unit } from "./palette";

const WHEEL_RADIUS = 0.26;
const WHEEL_WIDTH = 0.38;
const TRACK_HALF = 1.0; // centre of the wheelbase to the wheel's mid-plane
const WHEEL_X = { front: 1.05, middle: 0.05, rear: -0.95 } as const;
const ASSET_WHEELS = ["FL", "ML", "RL", "FR", "MR", "RR"].map((k) => `wheel_${k}`);
const ASSET_CORNERS = ["FL", "FR", "RL", "RR"];
// Corner steering stops here, short of the actuator's travel: past this a
// replay is turning on the spot, and the wheels would only flail.
const MAX_STEER = 0.6;

/** A steerable wheel, placed relative to the fixed middle axle; ``left`` is positive to the rover's left. */
interface Corner {
  node: THREE.Object3D;
  rest: THREE.Quaternion;
  ahead: number;
  left: number;
  angle: number;
}

function material(color: number, roughness: number, metalness: number): THREE.MeshStandardMaterial {
  return new THREE.MeshStandardMaterial({ color, roughness, metalness });
}

function colour(rgb: [number, number, number]): number {
  const [r, g, b] = rgb.map((v) => Math.round(v * 255));
  return (r << 16) | (g << 8) | b;
}

/** A cylinder running from ``a`` to ``b``: struts, arms, masts. */
function strut(a: THREE.Vector3, b: THREE.Vector3, radius: number, mat: THREE.Material): THREE.Mesh {
  const direction = new THREE.Vector3().subVectors(b, a);
  const length = direction.length();
  const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, length, 10), mat);
  mesh.position.copy(a).addScaledVector(direction, 0.5);
  mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), direction.normalize());
  return mesh;
}

export class RoverModel {
  readonly root = new THREE.Group();
  private readonly wheels: THREE.Group[] = [];
  private readonly mastHead = new THREE.Group();
  private readonly statusLight: THREE.Mesh;

  private readonly procedural: THREE.Object3D[] = [];
  private asset: THREE.Object3D | null = null;
  private assetWheels: THREE.Object3D[] = [];
  private assetMast: THREE.Object3D | null = null;
  private assetLight: THREE.MeshStandardMaterial | null = null;
  private assetLightBase = 1;
  private corners: Corner[] = [];
  private assetCorners: Corner[] = [];
  private lastHeading: number | null = null;
  private curvature = 0;

  private readonly targetPosition = new THREE.Vector3();
  private readonly targetQuaternion = new THREE.Quaternion();
  private spin = 0;
  private distanceRolled = 0;
  private placed = false;
  private mastPhase = 0;

  constructor() {
    const body = material(colour(mix(unit("fg"), unit("muted"), 0.18)), 0.62, 0.08);
    const deck = material(colour(mix(unit("surface-2"), unit("muted"), 0.35)), 0.5, 0.35);
    const metal = material(colour(mix(unit("muted"), unit("fg"), 0.25)), 0.42, 0.7);
    const dark = material(colour(mix(unit("surface"), unit("line-strong"), 0.6)), 0.7, 0.4);
    const tyre = material(colour(mix(unit("muted"), unit("surface-2"), 0.45)), 0.48, 0.62);
    const glass = new THREE.MeshStandardMaterial({
      color: hex("surface"),
      roughness: 0.15,
      metalness: 0.8,
      emissive: hex("info"),
      emissiveIntensity: 0.18,
    });

    // --- mobility: six wheels on a rocker-bogie --------------------------------
    for (const side of [-1, 1]) {
      const z = side * TRACK_HALF;
      for (const x of [WHEEL_X.front, WHEEL_X.middle, WHEEL_X.rear]) {
        const wheel = this.buildWheel(tyre, dark, metal);
        wheel.position.set(x, WHEEL_RADIUS, z);
        this.wheels.push(wheel);
        this.root.add(wheel);
        if (x !== WHEEL_X.middle) {
          // Model left is -Z: forward x up = +Z points to the right.
          this.corners.push({ node: wheel, rest: wheel.quaternion.clone(), ahead: x - WHEEL_X.middle, left: -z, angle: 0 });
        }
      }

      const hub = (x: number) => new THREE.Vector3(x, WHEEL_RADIUS, z - side * 0.24);
      const rockerPivot = new THREE.Vector3(0.28, 0.66, side * 0.86);
      const bogiePivot = new THREE.Vector3(-0.44, 0.5, side * 0.93);
      this.root.add(strut(hub(WHEEL_X.front), rockerPivot, 0.045, metal));
      this.root.add(strut(rockerPivot, bogiePivot, 0.045, metal));
      this.root.add(strut(bogiePivot, hub(WHEEL_X.middle), 0.04, metal));
      this.root.add(strut(bogiePivot, hub(WHEEL_X.rear), 0.04, metal));
      for (const pivot of [rockerPivot, bogiePivot]) {
        const joint = new THREE.Mesh(new THREE.SphereGeometry(0.07, 12, 8), dark);
        joint.position.copy(pivot);
        this.root.add(joint);
      }
      // Rocker to body: the pin the whole suspension pivots on.
      this.root.add(strut(rockerPivot, new THREE.Vector3(0.28, 0.66, side * 0.58), 0.05, dark));
    }

    // --- chassis ----------------------------------------------------------------
    const web = new THREE.Mesh(new THREE.BoxGeometry(1.85, 0.5, 1.12), body);
    web.position.set(0.05, 0.86, 0);
    this.root.add(web);

    const topDeck = new THREE.Mesh(new THREE.BoxGeometry(1.95, 0.06, 1.2), deck);
    topDeck.position.set(0.05, 1.14, 0);
    this.root.add(topDeck);

    // Differential bar across the deck, linking the two rockers.
    const differential = new THREE.Mesh(new THREE.BoxGeometry(0.08, 0.06, 1.62), metal);
    differential.position.set(0.28, 1.2, 0);
    this.root.add(differential);

    // Hazard cameras, front and rear: the eyes the autonomy actually drives on.
    for (const [x, facing] of [
      [0.99, 1],
      [-0.9, -1],
    ] as const) {
      for (const z of [-0.18, 0.18]) {
        const cam = new THREE.Mesh(new THREE.BoxGeometry(0.06, 0.08, 0.1), glass);
        cam.position.set(x + facing * 0.02, 0.72, z);
        this.root.add(cam);
      }
    }

    // --- power: RTG at the rear, finned -----------------------------------------
    const rtg = new THREE.Group();
    const core = new THREE.Mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.62, 16), dark);
    rtg.add(core);
    for (let i = 0; i < 8; i += 1) {
      const fin = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.56, 0.16), dark);
      const angle = (i / 8) * Math.PI * 2;
      fin.position.set(Math.cos(angle) * 0.22, 0, Math.sin(angle) * 0.22);
      fin.rotation.y = -angle;
      rtg.add(fin);
    }
    rtg.position.set(-1.12, 1.02, 0);
    rtg.rotation.z = -Math.PI / 3.2;
    this.root.add(rtg);

    // --- communications ---------------------------------------------------------
    const hgaPost = strut(new THREE.Vector3(-0.32, 1.16, 0.38), new THREE.Vector3(-0.32, 1.42, 0.38), 0.035, metal);
    this.root.add(hgaPost);
    const dish = new THREE.Mesh(new THREE.CylinderGeometry(0.27, 0.27, 0.035, 28), body);
    dish.position.set(-0.32, 1.46, 0.38);
    dish.rotation.set(0.35, 0, 0.55);
    this.root.add(dish);
    const feed = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.03, 0.12, 8), dark);
    feed.position.copy(dish.position).add(new THREE.Vector3(0.05, 0.07, 0.02));
    feed.rotation.copy(dish.rotation);
    this.root.add(feed);
    this.root.add(strut(new THREE.Vector3(-0.62, 1.16, -0.4), new THREE.Vector3(-0.62, 1.62, -0.4), 0.018, metal));

    // --- remote-sensing mast ----------------------------------------------------
    const mastBase = new THREE.Vector3(0.62, 1.16, -0.38);
    const mastTop = new THREE.Vector3(0.62, 2.12, -0.38);
    this.root.add(strut(mastBase, mastTop, 0.05, metal));
    const head = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.2, 0.42), body);
    this.mastHead.add(head);
    for (const z of [-0.12, 0.12]) {
      const lens = new THREE.Mesh(new THREE.CylinderGeometry(0.045, 0.045, 0.06, 14), glass);
      lens.rotation.z = Math.PI / 2;
      lens.position.set(0.15, 0.0, z);
      this.mastHead.add(lens);
    }
    const aperture = new THREE.Mesh(new THREE.CylinderGeometry(0.075, 0.075, 0.07, 18), glass);
    aperture.rotation.z = Math.PI / 2;
    aperture.position.set(0.15, 0.075, 0);
    this.mastHead.add(aperture);
    this.mastHead.position.copy(mastTop).add(new THREE.Vector3(0, 0.12, 0));
    this.root.add(this.mastHead);

    // --- robotic arm, stowed across the front -----------------------------------
    const shoulder = new THREE.Vector3(1.0, 0.98, 0.42);
    const elbow = new THREE.Vector3(1.32, 0.86, 0.12);
    const wrist = new THREE.Vector3(1.24, 0.74, -0.32);
    this.root.add(strut(shoulder, elbow, 0.045, metal));
    this.root.add(strut(elbow, wrist, 0.04, metal));
    const turret = new THREE.Mesh(new THREE.CylinderGeometry(0.12, 0.12, 0.16, 12), dark);
    turret.position.copy(wrist);
    this.root.add(turret);

    // The one accent on the model: a status light, so the rover reads as ours.
    this.statusLight = new THREE.Mesh(
      new THREE.SphereGeometry(0.05, 12, 8),
      new THREE.MeshStandardMaterial({ color: hex("accent"), emissive: hex("accent"), emissiveIntensity: 1.4 }),
    );
    this.statusLight.position.set(0.62, 1.18, 0.0);
    this.root.add(this.statusLight);

    this.root.traverse((object) => {
      if ((object as THREE.Mesh).isMesh) {
        object.castShadow = true;
        object.receiveShadow = true;
      }
    });
    this.procedural.push(...this.root.children);
  }

  /** Swap the procedural model for the Blender-built one. */
  useAsset(model: THREE.Object3D): void {
    if (this.asset) return;
    for (const part of this.procedural) part.visible = false;
    model.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      // Materials are cloned: the loaded original is shared between scenes.
      mesh.material = Array.isArray(mesh.material) ? mesh.material.map((m) => m.clone()) : mesh.material.clone();
    });
    this.assetWheels = ASSET_WHEELS.map((name) => model.getObjectByName(name)).filter(
      (node): node is THREE.Object3D => node !== undefined,
    );
    this.assetMast = model.getObjectByName("mast_head") ?? null;

    // Corner pivots, measured in the model's own frame before it is parented.
    model.updateMatrixWorld(true);
    const at = (name: string) => model.getObjectByName(name)?.getWorldPosition(new THREE.Vector3()) ?? null;
    const middle = [at("wheel_ML"), at("wheel_MR")].filter((p): p is THREE.Vector3 => p !== null);
    const axle = middle.length ? middle.reduce((sum, p) => sum + p.x, 0) / middle.length : 0;
    this.assetCorners = ASSET_CORNERS.flatMap((key) => {
      const node = model.getObjectByName(`steer_${key}`);
      const position = at(`steer_${key}`);
      if (!node || !position) return [];
      return [{ node, rest: node.quaternion.clone(), ahead: position.x - axle, left: -position.z, angle: 0 }];
    });

    const light = model.getObjectByName("status_light") as THREE.Mesh | undefined;
    if (light && !Array.isArray(light.material)) {
      this.assetLight = light.material as THREE.MeshStandardMaterial;
      this.assetLightBase = this.assetLight.emissiveIntensity;
    }
    this.root.add(model);
    this.asset = model;
  }

  /** Image-based lighting for the asset's metals and paint; null for none. */
  setEnvironment(envMap: THREE.Texture | null, intensity: number): void {
    this.root.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) {
        const standard = m as THREE.MeshStandardMaterial;
        if (!standard.isMeshStandardMaterial) continue;
        if (Boolean(standard.envMap) !== Boolean(envMap)) standard.needsUpdate = true;
        standard.envMap = envMap;
        standard.envMapIntensity = intensity;
      }
    });
  }

  private buildWheel(tyre: THREE.Material, dark: THREE.Material, metal: THREE.Material): THREE.Group {
    const wheel = new THREE.Group();
    const spinner = new THREE.Group();
    wheel.add(spinner);

    const rim = new THREE.Mesh(new THREE.CylinderGeometry(WHEEL_RADIUS, WHEEL_RADIUS, WHEEL_WIDTH, 28, 1, true), tyre);
    rim.rotation.x = Math.PI / 2;
    spinner.add(rim);

    // Grousers: the chevron-ish cleats that bite into regolith.
    for (let i = 0; i < 18; i += 1) {
      const angle = (i / 18) * Math.PI * 2;
      const cleat = new THREE.Mesh(new THREE.BoxGeometry(0.028, 0.026, WHEEL_WIDTH * 1.01), dark);
      cleat.position.set(Math.cos(angle) * (WHEEL_RADIUS + 0.011), Math.sin(angle) * (WHEEL_RADIUS + 0.011), 0);
      cleat.rotation.z = angle;
      spinner.add(cleat);
    }

    // Outer face: a hub and curved spokes, so the spin is visible.
    for (const face of [-1, 1]) {
      const hubCap = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.03, 16), metal);
      hubCap.rotation.x = Math.PI / 2;
      hubCap.position.z = face * (WHEEL_WIDTH / 2 - 0.02);
      spinner.add(hubCap);
      for (let s = 0; s < 6; s += 1) {
        const angle = (s / 6) * Math.PI * 2;
        const spoke = new THREE.Mesh(new THREE.BoxGeometry(WHEEL_RADIUS * 0.86, 0.024, 0.012), metal);
        spoke.position.set(Math.cos(angle) * WHEEL_RADIUS * 0.47, Math.sin(angle) * WHEEL_RADIUS * 0.47, face * (WHEEL_WIDTH / 2 - 0.03));
        spoke.rotation.z = angle + 0.35;
        spinner.add(spoke);
      }
    }
    return wheel;
  }

  /**
   * Place the rover on the ground. ``heading`` is the horizontal direction of
   * travel; ``normal`` is the terrain normal under it, so the chassis pitches
   * and rolls with the slope it is standing on.
   */
  setPose(position: THREE.Vector3, heading: THREE.Vector3, normal: THREE.Vector3, immediate = false): void {
    const up = normal.clone().normalize();
    const forward = heading.clone().sub(up.clone().multiplyScalar(heading.dot(up)));
    if (forward.lengthSq() < 1e-8) forward.set(1, 0, 0);
    forward.normalize();
    const side = new THREE.Vector3().crossVectors(forward, up).normalize();
    const basis = new THREE.Matrix4().makeBasis(forward, up, side);
    this.targetQuaternion.setFromRotationMatrix(basis);
    this.targetPosition.copy(position);

    if (immediate || !this.placed) {
      this.root.position.copy(this.targetPosition);
      this.root.quaternion.copy(this.targetQuaternion);
      this.placed = true;
    }
  }

  /** Ease towards the target pose and turn the wheels by the distance covered. */
  update(dt: number, reducedMotion: boolean): void {
    const before = this.root.position.clone();
    const k = reducedMotion ? 1 : 1 - Math.exp(-dt * 10);
    this.root.position.lerp(this.targetPosition, k);
    this.root.quaternion.slerp(this.targetQuaternion, k);

    const travelled = before.distanceTo(this.root.position) / Math.max(this.root.scale.x, 1e-6);
    this.distanceRolled += travelled;
    this.spin = -this.distanceRolled / WHEEL_RADIUS;
    for (const wheel of this.wheels) {
      (wheel.children[0] as THREE.Group).rotation.z = this.spin;
    }
    // The asset's wheel nodes sit at the hub with the axle along local Z, the
    // same convention, so the same angle turns them the right way.
    for (const wheel of this.assetWheels) wheel.rotation.z = this.spin;

    this.steerCorners(dt, travelled, reducedMotion);

    if (!reducedMotion) {
      // The mast scans slowly, as a navigation camera does between drives.
      this.mastPhase += dt * 0.6;
      this.mastHead.rotation.y = Math.sin(this.mastPhase) * 0.5;
      if (this.assetMast) this.assetMast.rotation.y = Math.sin(this.mastPhase) * 0.5;
      const pulse = 1.1 + Math.sin(this.mastPhase * 4) * 0.4;
      (this.statusLight.material as THREE.MeshStandardMaterial).emissiveIntensity = pulse;
      if (this.assetLight) this.assetLight.emissiveIntensity = this.assetLightBase * (0.55 + 0.35 * Math.sin(this.mastPhase * 4));
    }
  }

  /**
   * Turn the corner wheels into the curve the chassis is actually driving.
   * Curvature is heading change per metre rolled; each corner then takes its
   * Ackermann angle, atan(ahead * k / (1 - left * k)), so the inner wheels turn
   * further than the outer ones and the rear pair steers against the front.
   */
  private steerCorners(dt: number, travelled: number, immediate: boolean): void {
    const forward = new THREE.Vector3(1, 0, 0).applyQuaternion(this.root.quaternion);
    // Positive to the left: turning from +X towards -Z.
    const heading = Math.atan2(-forward.z, forward.x);
    if (this.lastHeading !== null && travelled > 1e-4) {
      const turned = Math.atan2(Math.sin(heading - this.lastHeading), Math.cos(heading - this.lastHeading));
      const k = immediate ? 1 : 1 - Math.exp(-dt * 4);
      this.curvature += (turned / travelled - this.curvature) * k;
    }
    this.lastHeading = heading;

    const ease = immediate ? 1 : 1 - Math.exp(-dt * 6);
    const yaw = new THREE.Quaternion();
    const up = new THREE.Vector3(0, 1, 0);
    for (const corner of [...this.corners, ...this.assetCorners]) {
      const target = Math.atan((corner.ahead * this.curvature) / Math.max(0.2, 1 - corner.left * this.curvature));
      corner.angle += (THREE.MathUtils.clamp(target, -MAX_STEER, MAX_STEER) - corner.angle) * ease;
      corner.node.quaternion.copy(corner.rest).multiply(yaw.setFromAxisAngle(up, corner.angle));
    }
  }

  /** Model metres per world unit; the scene scales the rover to be legible. */
  setScale(scale: number): void {
    this.root.scale.setScalar(scale);
  }

  dispose(): void {
    for (const part of this.procedural) {
      part.traverse((object) => {
        const mesh = object as THREE.Mesh;
        if (!mesh.isMesh) return;
        mesh.geometry.dispose();
        const mat = mesh.material;
        if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
        else mat.dispose();
      });
    }
    // The asset's geometry is shared with the loader's cache; its materials are ours.
    this.asset?.traverse((object) => {
      const mesh = object as THREE.Mesh;
      if (!mesh.isMesh) return;
      for (const m of Array.isArray(mesh.material) ? mesh.material : [mesh.material]) m.dispose();
    });
  }
}
