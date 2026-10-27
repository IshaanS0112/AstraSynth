/** @type {import('tailwindcss').Config} */

// Graphite, from the shared design system (Ishaan Singh projects). Every colour
// is a token backed by an RGB-triplet CSS variable in src/index.css, so alpha
// modifiers like `bg-success/15` still work.
//
// `colors` REPLACES Tailwind's palette instead of extending it. With the default
// palette gone, a stray `text-slate-500` produces nothing at all - it shows up
// in review as unstyled, rather than shipping as a quiet off-system colour.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      inherit: "inherit",
      canvas: token("bg"),
      surface: token("surface"),
      "surface-2": token("surface-2"),
      line: token("line"),
      "line-strong": token("line-strong"),
      fg: token("fg"),
      muted: token("muted"),
      subtle: token("subtle"),
      accent: { DEFAULT: token("accent"), fg: token("accent-fg") },
      success: token("success"),
      warning: token("warning"),
      danger: token("danger"),
      info: token("info"),
      series: {
        2: token("chart-2"),
        3: token("chart-3"),
        4: token("chart-4"),
        5: token("chart-5"),
        6: token("chart-6"),
      },
      // Names the existing components use, kept as aliases onto Graphite.
      void: token("bg"),
      panel: token("surface"),
      edge: token("line"),
    },
    extend: {
      borderColor: { DEFAULT: token("line") },
      fontFamily: {
        sans: ["Geist", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ['"Geist Mono"', "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: { "2xs": ["11px", "16px"] },
      // Status badges tint at 12%, per the design system.
      opacity: { 12: "0.12" },
      borderRadius: { md: "6px", lg: "10px" },
      transitionDuration: { 120: "120ms", 180: "180ms" },
      transitionTimingFunction: { enter: "cubic-bezier(0.2, 0, 0, 1)" },
    },
  },
  plugins: [],
};
