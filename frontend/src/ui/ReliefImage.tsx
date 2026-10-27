import { Mountain } from "lucide-react";
import { useEffect, useState } from "react";

import { ramp, type Rgb } from "../three/palette";

/**
 * Regolith tones sampled from the Blender-baked albedo (frontend/public/3d/
 * regolith_albedo.jpg) at its 5th, 30th, 90th and 98th luminance percentiles,
 * so a thumbnail reads as the same ground the surface view shows.
 */
const REGOLITH: Rgb[] = [
  [92 / 255, 57 / 255, 35 / 255],
  [125 / 255, 78 / 255, 48 / 255],
  [153 / 255, 99 / 255, 63 / 255],
  [163 / 255, 108 / 255, 70 / 255],
];

// Sun from the north-west, 40 degrees up: the cartographic convention, so
// craters read as dips rather than domes.
const SUN = (() => {
  const v = [-0.55, -0.55, 0.63];
  const n = Math.hypot(...v);
  return v.map((c) => c / n);
})();

const SIZE = 256;
const cache = new Map<string, Promise<string>>();

/** Shade a grayscale DEM: Lambert lighting on its own gradients, tinted by height. */
function shade(src: string): Promise<string> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.decoding = "async";
    image.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = SIZE;
      canvas.height = SIZE;
      const ctx = canvas.getContext("2d", { willReadFrequently: true });
      if (!ctx) return reject(new Error("no 2d context"));
      ctx.drawImage(image, 0, 0, SIZE, SIZE);
      const pixels = ctx.getImageData(0, 0, SIZE, SIZE);
      const data = pixels.data;
      const height = new Float32Array(SIZE * SIZE);
      let lo = 1;
      let hi = 0;
      for (let i = 0; i < height.length; i += 1) {
        const h = data[i * 4] / 255;
        height[i] = h;
        lo = Math.min(lo, h);
        hi = Math.max(hi, h);
      }
      const span = Math.max(hi - lo, 1e-3);
      // Vertical exaggeration scaled to the tile's own relief, so a flat plain
      // still shows texture and a crater field does not turn to black and white.
      const z = 9 / span;
      const at = (x: number, y: number) => height[Math.min(SIZE - 1, Math.max(0, y)) * SIZE + Math.min(SIZE - 1, Math.max(0, x))];
      for (let y = 0; y < SIZE; y += 1) {
        for (let x = 0; x < SIZE; x += 1) {
          const dx = ((at(x + 1, y) - at(x - 1, y)) / 2) * z;
          const dy = ((at(x, y + 1) - at(x, y - 1)) / 2) * z;
          const len = Math.hypot(dx, dy, 1);
          const light = Math.max(0, (-dx * SUN[0] - dy * SUN[1] + SUN[2]) / len);
          const base = ramp(REGOLITH, (at(x, y) - lo) / span);
          const k = 0.3 + 0.95 * light;
          const o = (y * SIZE + x) * 4;
          data[o] = Math.min(255, base[0] * k * 255);
          data[o + 1] = Math.min(255, base[1] * k * 255);
          data[o + 2] = Math.min(255, base[2] * k * 255);
          data[o + 3] = 255;
        }
      }
      ctx.putImageData(pixels, 0, 0);
      resolve(canvas.toDataURL("image/png"));
    };
    image.onerror = () => reject(new Error(`could not decode ${src}`));
    image.src = src;
  });
}

/** A mission's terrain as shaded relief. Falls back to a placeholder for anything the browser cannot decode (TIFF). */
export default function ReliefImage({ src, alt = "", className = "" }: { src: string | null; alt?: string; className?: string }) {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!src) return;
    let cancelled = false;
    if (!cache.has(src)) cache.set(src, shade(src));
    cache.get(src)!.then(
      (value) => !cancelled && setUrl(value),
      () => {
        cache.delete(src);
        if (!cancelled) setFailed(true);
      },
    );
    return () => {
      cancelled = true;
    };
  }, [src]);

  if (!src || failed) {
    return (
      <div className={`dot-grid flex items-center justify-center bg-surface-2 ${className}`} role="img" aria-label={alt || "No preview"}>
        <Mountain className="h-5 w-5 text-subtle" strokeWidth={1.5} aria-hidden="true" />
      </div>
    );
  }
  if (!url) return <div className={`skeleton rounded-none ${className}`} aria-hidden="true" />;
  return <img src={url} alt={alt} className={`object-cover ${className}`} />;
}
