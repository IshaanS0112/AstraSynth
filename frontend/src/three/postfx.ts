/**
 * Post-processing, by quality preset.
 *
 * performance  straight to the canvas with the context's own antialiasing
 * balanced     a multisampled HDR target, bloom on emissive parts, output
 * cinematic    adds ground-truth ambient occlusion and a higher pixel ratio
 *
 * Bloom has a threshold above lit white paint, so only what emits light glows:
 * a status lamp, a beacon. Ambient occlusion darkens creases, never flat ground,
 * which is why the data layers keep their legend colours with it on.
 */

import * as THREE from "three";
import { EffectComposer } from "three/examples/jsm/postprocessing/EffectComposer.js";
import { GTAOPass } from "three/examples/jsm/postprocessing/GTAOPass.js";
import { OutputPass } from "three/examples/jsm/postprocessing/OutputPass.js";
import { RenderPass } from "three/examples/jsm/postprocessing/RenderPass.js";
import { UnrealBloomPass } from "three/examples/jsm/postprocessing/UnrealBloomPass.js";

export type Quality = "performance" | "balanced" | "cinematic";

const PIXEL_RATIO: Record<Quality, number> = { performance: 1, balanced: 1.5, cinematic: 2 };

export class PostFX {
  private composer: EffectComposer | null = null;
  private width = 1;
  private height = 1;
  quality: Quality = "balanced";

  constructor(
    private readonly renderer: THREE.WebGLRenderer,
    private readonly scene: THREE.Scene,
    private readonly camera: THREE.PerspectiveCamera,
    /** World-space size of a terrain cell, so the AO radius scales with the map. */
    private aoRadius = 1.2,
  ) {}

  setQuality(quality: Quality): void {
    this.quality = quality;
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, PIXEL_RATIO[quality]));
    this.renderer.shadowMap.needsUpdate = true;
    this.rebuild();
  }

  setAoRadius(radius: number): void {
    this.aoRadius = radius;
    if (this.quality === "cinematic") this.rebuild();
  }

  setSize(width: number, height: number): void {
    this.width = width;
    this.height = height;
    this.renderer.setSize(width, height, false);
    this.composer?.setPixelRatio(this.renderer.getPixelRatio());
    this.composer?.setSize(width, height);
  }

  render(): void {
    if (this.composer) this.composer.render();
    else this.renderer.render(this.scene, this.camera);
  }

  dispose(): void {
    this.composer?.dispose();
    this.composer = null;
  }

  private rebuild(): void {
    this.composer?.dispose();
    this.composer = null;
    if (this.quality === "performance") return;

    const ratio = this.renderer.getPixelRatio();
    const target = new THREE.WebGLRenderTarget(this.width * ratio, this.height * ratio, {
      type: THREE.HalfFloatType,
      samples: 4,
    });
    const composer = new EffectComposer(this.renderer, target);
    composer.setPixelRatio(ratio);
    composer.setSize(this.width, this.height);
    composer.addPass(new RenderPass(this.scene, this.camera));

    if (this.quality === "cinematic") {
      const ao = new GTAOPass(this.scene, this.camera, this.width, this.height);
      ao.blendIntensity = 0.85;
      ao.updateGtaoMaterial({
        radius: this.aoRadius,
        distanceExponent: 1.5,
        thickness: 1.2,
        scale: 1.1,
        samples: 16,
      });
      ao.updatePdMaterial({ lumaPhi: 10, depthPhi: 2, normalPhi: 3, radius: 6, rings: 2, samples: 16 });
      composer.addPass(ao);
    }

    composer.addPass(new UnrealBloomPass(new THREE.Vector2(this.width, this.height), 0.55, 0.45, 1.4));
    composer.addPass(new OutputPass());
    this.composer = composer;
  }
}
