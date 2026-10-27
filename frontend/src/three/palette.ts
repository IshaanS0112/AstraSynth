/**
 * Graphite tokens for code that cannot use Tailwind classes: three.js materials,
 * canvas text, and the colour ramps the terrain layers are drawn with.
 *
 * Read from the CSS variables in index.css at runtime, so the stylesheet stays
 * the single source of truth - a token changed there changes the 3-D scene too.
 */

export type Rgb = [number, number, number];

export type TokenName =
  | "bg"
  | "surface"
  | "surface-2"
  | "line"
  | "line-strong"
  | "fg"
  | "muted"
  | "subtle"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "chart-2"
  | "chart-3"
  | "chart-4"
  | "chart-5"
  | "chart-6";

// Used only if the stylesheet has not loaded, so the scene never renders black.
const FALLBACK: Record<TokenName, Rgb> = {
  bg: [10, 11, 13],
  surface: [17, 19, 23],
  "surface-2": [23, 26, 31],
  line: [35, 38, 45],
  "line-strong": [52, 56, 66],
  fg: [232, 234, 237],
  muted: [144, 151, 161],
  subtle: [95, 102, 112],
  accent: [198, 244, 50],
  success: [52, 211, 153],
  warning: [251, 191, 36],
  danger: [248, 113, 113],
  info: [96, 165, 250],
  "chart-2": [96, 165, 250],
  "chart-3": [251, 191, 36],
  "chart-4": [242, 128, 90],
  "chart-5": [60, 200, 180],
  "chart-6": [138, 145, 156],
};

const cache = new Map<TokenName, Rgb>();

/** A token as 0-255 RGB. */
export function rgb255(name: TokenName): Rgb {
  const hit = cache.get(name);
  if (hit) return hit;
  let value = FALLBACK[name];
  if (typeof document !== "undefined") {
    const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
    const parts = raw.split(/\s+/).map(Number);
    if (parts.length === 3 && parts.every((p) => Number.isFinite(p))) {
      value = parts as Rgb;
    }
  }
  cache.set(name, value);
  return value;
}

/** A token as a three.js hex number. */
export function hex(name: TokenName): number {
  const [r, g, b] = rgb255(name);
  return (r << 16) | (g << 8) | b;
}

/** A token as a CSS colour string, optionally with alpha. */
export function css(name: TokenName, alpha = 1): string {
  const [r, g, b] = rgb255(name);
  return alpha === 1 ? `rgb(${r} ${g} ${b})` : `rgb(${r} ${g} ${b} / ${alpha})`;
}

/** A token as 0-1 RGB, for vertex colours. */
export function unit(name: TokenName): Rgb {
  const [r, g, b] = rgb255(name);
  return [r / 255, g / 255, b / 255];
}

/**
 * sRGB 0-1 -> linear 0-1. three.js reads vertex colours as linear light, so a
 * ramp written without this renders paler than the legend drawn from it.
 */
export function toLinear(rgb: Rgb): Rgb {
  return rgb.map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)) as Rgb;
}

/** ``a`` moved ``t`` of the way towards ``b``. */
export function mix(a: Rgb, b: Rgb, t: number): Rgb {
  return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t];
}

/** Linear interpolation along a list of 0-1 RGB stops. */
export function ramp(stops: Rgb[], t: number): Rgb {
  const clamped = Math.min(1, Math.max(0, Number.isFinite(t) ? t : 0));
  const scaled = clamped * (stops.length - 1);
  const index = Math.min(stops.length - 2, Math.floor(scaled));
  return mix(stops[index], stops[index + 1], scaled - index);
}

/**
 * The colour ramps each terrain layer is drawn with, built from status tokens
 * so the meaning carries over: hazard runs success -> warning -> danger, the
 * same three colours the rest of the console uses for good, caution and bad.
 * The first stop is each ramp's darkest end, mixed towards the page colour so
 * safe ground recedes instead of glowing.
 */
export function layerRamps(): Record<string, Rgb[]> {
  const bg = unit("bg");
  const hazard: Rgb[] = [
    mix(bg, unit("success"), 0.45),
    mix(unit("success"), unit("warning"), 0.35),
    unit("warning"),
    unit("danger"),
  ];
  return {
    hazard,
    slope_deg: hazard,
    elevation_m: [mix(bg, unit("muted"), 0.25), mix(bg, unit("muted"), 0.6), unit("muted"), unit("fg")],
    uncertainty: [mix(bg, unit("info"), 0.2), mix(bg, unit("info"), 0.55), unit("info"), mix(unit("info"), unit("fg"), 0.55)],
    // The rover's own keep-out band: invisible far from obstacles, warning near them.
    clearance_cost: [mix(bg, unit("muted"), 0.3), mix(unit("muted"), unit("warning"), 0.4), unit("warning"), unit("chart-4")],
  };
}

/** A ramp as a CSS gradient, so a legend matches the terrain it describes. */
export function rampGradient(stops: Rgb[]): string {
  const parts = stops.map((s, i) => {
    const [r, g, b] = s.map((v) => Math.round(v * 255));
    return `rgb(${r} ${g} ${b}) ${Math.round((i / (stops.length - 1)) * 100)}%`;
  });
  return `linear-gradient(90deg, ${parts.join(", ")})`; // lint-ok: a legend for a data ramp, not decoration
}
