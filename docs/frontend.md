# Frontend

Two views over the same API.

**The classic mission pages** (`pages/Dashboard`, `MissionDetail`, `NewMission`)
are the V1 flow: upload a tile, analyse it, click a start and a goal, plan, assess,
generate a report. They still work and are still the shortest path from nothing to
a mission report.

**Mission control** (`pages/MissionControl`) is the V3 view, and it inverts the
layout. In the classic pages the numbers are the page and the map is an
illustration. In mission control the terrain *is* the application and everything
else is a drill-down from it — because the questions an operator actually asks are
geometric ("where is it, where is it going, what is it about to drive into") and a
table makes them reconstruct the geometry in their head.

---

## The renderer is not a React component

`three/TerrainScene.ts` is a plain class. It owns a canvas, a scene graph and an
animation loop, and it knows nothing about React, the API, or mission state. Its
whole interface is four setters: a terrain, a layer, some routes, some markers.

`mission-control/TerrainView3D.tsx` is the only file that bridges the two, and it
maps each prop to exactly one setter behind an effect keyed on that prop alone.
That is what stops a telemetry tick from rebuilding a terrain mesh, and a layer
change from rebuilding the routes.

The split matters more than it looks. A scene graph is mutable state with a
lifetime; a React tree is a pure function re-run on every change. Expressing the
first as the second means either rebuilding WebGL resources far more often than
necessary, or threading refs through everything until the component is a class
with extra steps.

**One frame of reference, defined once.** `cellToWorld(row, col)` is the single
place a grid cell becomes a position, and every route, marker and pick goes
through it. Routes arrive from the API in *image pixels*; `pixelToCell` converts
at the boundary, so nothing downstream needs to know what resolution the viewer
happens to be showing.

**WebGL resources are not garbage collected.** Scrubbing a traverse rebuilds the
route group on every frame, so `clearGroup` disposes geometries and materials
explicitly. Without it, the leak is per frame, not per mount.

---

## Layers

Elevation, hazard, propagated uncertainty, and slope, as vertex colours on one
mesh. Switching layers rewrites a colour attribute; it does not rebuild geometry.

Two decisions worth stating:

**Untraversable ground is marked on every layer, not just the hazard one.** An
operator reading the slope map still needs to see where the rover cannot go.
Making them infer it from a different tab is how a route gets approved across a
crater rim.

**Relief is drawn to true scale by default.** One metre of rise is the same length
as one metre across. Exaggeration is offered as a labelled 1×/2×/4× control,
because the two uses genuinely conflict — a slope layer is only trustworthy at
true scale, and 40 m of relief across a 1 km tile is nearly invisible at it — and
the honest resolution is a knob that starts at truth and says when it is lying.

---

## Playback

The traverse comes back as a complete run: the initial plan, the executed path,
and a timestamped event log. Playback scrubs an index through that, so it is a
replay rather than a live stream — the simulation already finished on the server.

The clock is a fixed 120 ms interval, **not** `requestAnimationFrame`. A
simulation clock tied to the renderer's frame rate plays the same run at
different speeds on different machines.

The event log distinguishes a **repair** from a **reroute**. With a noisy sensor
almost every step changes the belief slightly and triggers a D* Lite repair, so a
raw repair count is a number about the sensor, not about the mission. What an
operator needs is the handful of times the route actually changed, which is the
default filter.

---

## Charts

Both are single-series by design.

The **Pareto plot** shows routes, not competing categories, so it uses emphasis —
one hue for the front, muted outline for the dominated — rather than a categorical
palette, and fill-versus-outline carries the distinction without relying on colour
at all. Only the corners of the front are direct-labelled; a label on every point
goes unread. Clicking a route draws it on the terrain.

The **robustness panel** leads with a number, because "will this mission finish"
has one answer and a bar chart of a single value is not a chart. Below it, a
percentile strip rather than a mean, and a failure breakdown where each mode ships
an icon and a label so the status colour is never carrying the meaning alone.

Where identity genuinely matters — one colour per rover in a deconflicted fleet —
the palette is `#0d9488 · #8b5cf6 · #d97706 · #e11d48`, checked for CVD
separation, chroma, lightness and contrast against the panel surface rather than
chosen by eye.

---

## Bundle

three.js is most of the JavaScript, and most users of the classic pages never open
the 3-D view, so `MissionControl` is behind `React.lazy`. The entry bundle is
about 61 kB gzipped; the mission-control chunk is about 137 kB and loads on
navigation.

---

## What CI checks, and what it does not

CI typechecks and builds. That proves the code compiles; it does not prove the
terrain mesh renders, that clicking the terrain places a target where the operator
aimed, or that a 30-second study returns before the page gives up.

`scripts/check_mission_control.mjs` drives the real thing in a real browser —
loads a mission, runs a traverse, sweeps the weights, runs a robustness study,
places science targets by clicking the terrain, deconflicts a fleet — and fails on
any console error, page error or failed request. It is **not** in CI, because a CI
job that downloads Chromium to screenshot a page is a slow way to learn the build
works. It is in the repo so the check is repeatable rather than something that
happened once on a laptop. Its usage is in its header comment.
