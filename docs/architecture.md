# AstraSynth — Architecture

## Contents

1. [System shape](#system-shape)
2. [What's real vs simulated](#whats-real-vs-simulated)
3. [Terrain analysis](#terrain-analysis)
4. [Hazard scoring](#hazard-scoring)
5. [Path planning](#path-planning)
6. [Risk and feasibility](#risk-and-feasibility)
7. [Report generation](#report-generation)
8. [Data model](#data-model)
9. [Bugs found and lessons learned](#bugs-found-and-lessons-learned)
10. [Known limitations](#known-limitations)
11. [Interview questions](#interview-questions)

---

## System shape

```
React + TypeScript                FastAPI
┌──────────────────┐             ┌────────────────────────────────────────┐
│ TerrainViewer    │◄───────────►│ routers/  (thin: validate → delegate)   │
│ HazardOverlay    │   REST      │            ↓                            │
│ PathVisualization│             │ services/mission_pipeline.py            │
│ RiskReportView   │             │   ├─ terrain_analyzer   OpenCV          │
└──────────────────┘             │   ├─ hazard_mapper      weighted score  │
                                  │   ├─ path_planner       A*              │
                                  │   ├─ risk_engine        arithmetic      │
                                  │   └─ report_generator   LLM ← last      │
                                  └───────────┬────────────────────────────┘
                                              │
                        ┌─────────────────────┴──────────────┐
                        │ PostgreSQL      storage/<mission>/  │
                        │ 5 tables        terrain · maps ·    │
                        │ JSONB metadata  analysis.npz        │
                        └────────────────────────────────────┘
```

Three layering rules the code actually enforces:

- **Services never import routers.** The analysis engines are pure functions over arrays and dataclasses. Every test in `tests/` calls them directly with no HTTP layer and no database.
- **Domain enums live in `app/enums.py`, not `app/models/`.** They started in the models package, which meant importing `TerrainClass` pulled SQLAlchemy into the computation layer and broke the no-DB tests. Moving them made the dependency direction one-way: models may import enums, enums import nothing.
- **The LLM is called from exactly one function**, at the end of one pipeline stage, over data that is already persisted.

---

## What's real vs simulated

### Real

| | |
|---|---|
| **Slope estimation** | Sobel gradients on an elevation model, normalised by the 3×3 kernel constant and converted to degrees via `arctan`. A test asserts a ramp rising 1 m per 1 m of ground reads as 45.0°. |
| **Obstacle detection** | Canny with per-image adaptive thresholds → morphological close → `findContours` → area filter. Contour centroids, areas, and enclosing radii are real measurements from the image. |
| **Roughness** | Local intensity standard deviation via two box filters (`E[x²] − E[x]²`), O(1) per pixel. |
| **Hazard scoring** | Weighted sum of three independently normalised terms, bounded to `[0, 1]`, with the weights stored on every result. |
| **Path planning** | A* over an 8-connected grid with an admissible, consistent heuristic. Verified against Dijkstra on identical input. |
| **Energy model** | `energy = rate × distance × (1 + k·|rise/run|)`, integrated per step from the elevation model. |
| **Feasibility** | Total energy against battery capacity. Arithmetic, no model, no heuristic. |
| **Terrain data** | Synthetic tiles from a documented generator, or real USGS MOLA/HRSC DEM crops. |

### Simulated or out of scope

| | |
|---|---|
| **Rover hardware** | No hardware in the loop. Rover configs are parameter sets. |
| **Live telemetry** | Nothing streams. One static tile per mission. |
| **Rover specifications** | Battery capacities and energy-per-metre figures are the right order of magnitude for solar planetary rovers, chosen so the three seeded rovers span the feasibility space on a ~1.3 km traverse. They are not manufacturer data. |
| **Elevation from imagery** | Grayscale intensity is treated as a *linear proxy* for elevation. For a real DEM tile that is correct, and `prepare_usgs_terrain.py` records the true elevation range so the scaling is right. For an ordinary photograph it would be wrong, and the system does not detect the difference. |
| **Terrain classification accuracy** | Validated for self-consistency against generated presets, not against labelled planetary terrain. There is no ground truth in this repo. |
| **Mid-traverse re-planning** | Static pre-mission planning only. |
| **Multi-rover coordination** | Not implemented. |

---

## Terrain analysis

The input is a single-channel image read as a coarse DEM: intensity `0–255` maps linearly onto `[0, elevation_range_m]` metres. That mapping plus `meters_per_pixel` is what converts pixel gradients into real angles.

```python
elevation_m   = (gray / 255) * elevation_range_m
dz_dx         = Sobel(elevation_m, 1, 0, ksize=3) / (8 * meters_per_pixel)
dz_dy         = Sobel(elevation_m, 0, 1, ksize=3) / (8 * meters_per_pixel)
gradient      = sqrt(dz_dx² + dz_dy²)          # rise/run, = tan(slope)
slope_deg     = degrees(arctan(gradient))
```

The `/8` is the normalisation constant for the 3×3 Sobel kernel. Without it every reported angle is wrong by a fixed factor and nothing downstream notices, because everything downstream is relative. `test_known_ramp_gives_the_trigonometric_answer` is the test that would catch it.

**Obstacle detection** uses adaptive Canny thresholds (see [bugs](#bugs-found-and-lessons-learned) — this was originally fixed thresholds and did not work). A morphological close runs between Canny and `findContours` because raw Canny output is thin broken fragments, and contouring that yields hundreds of slivers rather than the handful of coherent regions a planner cares about.

**Classification** is a threshold rule, not a neural network:

```
obstacle_area_fraction ≥ 0.20                          → crater_field
mean_slope ≤ 6° AND roughness ≤ 0.12 AND area < 0.10   → sandy_plain
otherwise                                               → rocky_highland
```

Deliberate. There is no labelled planetary terrain set here to train on, and an under-trained CNN would produce a classification nobody could justify. The rule set produces a classification *and the evidence for it* — every result records which rule fired and the measured values that triggered it. A CNN comparison is a documented V2, where the interesting output would be the comparison itself.

---

## Hazard scoring

```
hazard(x,y) = 0.5 · min(slope_deg / 30°, 1)          slope, saturating
            + 0.3 · 1/(1 + distance_to_obstacle_m)   obstacle proximity
            + 0.2 · normalised_local_std              roughness
```

Each term is independently normalised to `[0, 1]` before weighting, so with weights summing to 1 the score is bounded to `[0, 1]`. That bound is load-bearing: the risk tiers at 0.3 and 0.6 are meaningless without it, and `build_hazard_map` raises if the weights don't sum to 1.

Two choices worth defending:

- **The slope term saturates at 30° instead of dividing by 90°.** Traversability collapses well before vertical; a 30° and a 60° slope should both read as maximally hazardous to a *scorer*, with the hard cut-off enforced separately by the planner.
- **Obstacle proximity decays smoothly rather than being a binary mask.** `1/(1+d)` nudges the planner into leaving clearance around obstacles instead of hugging their edges — cheap insurance against localisation error in anything real.

The full weight set, formula string, and aggregate statistics are stored as `calculation_basis` in `analysis_metadata` and copied into every report's structured context. Any number in a generated report can be traced back to the formula that produced it.

---

## Path planning

```
cost(a, b)      = distance_m(a,b) · (1 + hazard(b)) · energy_factor(slope(a,b))
energy_factor(s) = 1 + k·|rise/run|,  k = 0.5
h(n)            = euclidean_distance_m(n, goal)
```

**Admissibility.** `hazard ≥ 0` and `energy_factor ≥ 1`, so every edge satisfies `cost(a,b) ≥ distance_m(a,b)`. Summed over any path, true cost is never below straight-line distance, so `h` never overestimates. It also satisfies the triangle inequality, so it's *consistent* — A* never needs to reopen a closed node, which is why the implementation can use a simple boolean closed set.

`test_matches_dijkstra_cost_on_random_terrain` checks this empirically: an inadmissible heuristic would return a cheaper-looking but genuinely worse path, showing up as a cost mismatch against Dijkstra.

**Two hazard layers.** `(1 + hazard)` is bounded by 2, so cost shaping alone can never force a detour longer than one cell — the planner will drive straight through a crater rim to save fifteen cells. So there are two layers, the same split the ROS navigation costmap uses:

- **cost layer** — `(1 + hazard)`, shapes the route within traversable ground
- **lethal layer** — `hazard ≥ 0.85`, or a step exceeding the rover's `max_traversable_slope_deg`, removes the edge from the graph entirely

Both blocked counts are recorded in `planner_metadata`, and exhausting the open set raises `PathNotFoundError` naming which constraint did the blocking. That's how a mission becomes genuinely infeasible rather than merely expensive.

**Why A\* over Dijkstra.** Identical optimality guarantee under an admissible heuristic, but Dijkstra expands uniformly in all directions while A* biases expansion toward the goal. Measured at 18–37% fewer node expansions, widening with grid size, at identical cost — `scripts/benchmark_planner.py`.

**Downsampling.** The hazard map the user sees is full resolution; the planning grid is capped at 192 px on the longest edge. A* on a 1024² image is ~1M nodes and ~8M edges, which is not an interactive plan. `INTER_AREA` is used rather than point sampling so a single-pixel hazard spike is averaged into its block instead of being dropped between sample points.

---

## Risk and feasibility

Two verdicts, deliberately separate:

```
risk  = 0.6 · mean(hazard along path) + 0.4 · min(energy/capacity, 1)
        LOW < 0.3 ≤ MEDIUM ≤ 0.6 < HIGH

feasibility:  energy > capacity          → INFEASIBLE
              energy > 0.85 · capacity   → FEASIBLE_WITH_MARGIN
              otherwise                   → FEASIBLE
```

A path can be HIGH risk and FEASIBLE (short, nasty terrain) or LOW risk and INFEASIBLE (gentle, far beyond range). Collapsing them into one score would hide exactly the case a planner most needs to see.

`min(energy/capacity, 1)` is clamped so an infeasible plan can't push the risk score outside `[0, 1]` and break the tiers; the *unclamped* utilisation and the (possibly negative) energy margin are reported separately and drive the feasibility verdict.

Over a full-diagonal traverse the energy term tends to dominate and most plans land in MEDIUM. That's the model behaving correctly rather than a calibration bug — a rover spending 70% of its battery on one drive genuinely isn't low risk. LOW is reachable on short traverses; `test_all_three_tiers_are_reachable` pins all three.

---

## Report generation

Strict ordering:

1. **Collect** — `build_structured_context` assembles the payload from what the deterministic engines already computed and persisted. Nothing in it is model-derived.
2. **Constrain** — JSON-only contract, an instruction not to introduce facts, and an assistant-turn prefill of `{` so the JSON-only rule is mechanical rather than a request the model might preamble past.
3. **Validate** — parse, require all three keys, and **drop any cited `segment_id` not present in `high_hazard_segments`**. A hallucinated citation is discarded and counted, never surfaced.
4. **Degrade** — any failure (no API key, timeout, connection error, unparseable JSON, missing key) falls back to a template built from the same context. Identical numbers, no prose.

The response carries `generated_by: "llm" | "template_fallback"` and the UI renders that badge, so a reader can always tell which one they're looking at.

**The claim, and how to check it:** the AI doesn't compute hazard or risk — the code does, deterministically. The AI narrates already-computed numbers and cites specific segment IDs. `GET /missions/{id}/risk-report` returns the full pre-LLM context; diff it against the narrative.

---

## Data model

Five tables. Every stage writes its inputs and parameters, not just its outputs.

| Table | Auditability column |
|---|---|
| `missions` | `terrain_source` — where the data came from, on every report |
| `terrain_analyses` | `analysis_metadata` — every CV parameter, hazard weights, aggregate stats |
| `rover_configs` | — |
| `rover_paths` | `planner_metadata` — nodes expanded, grid shape, cost constants, blocked-move counts |
| `mission_risk_reports` | `structured_context` — the frozen pre-LLM payload |

Arrays (hazard grid, elevation grid) go to `storage/<mission_id>/analysis.npz` rather than the database, referenced from `analysis_metadata`. Path planning reloads them instead of re-running the CV pipeline. The `arrays_path` key is stripped by the response schema — it's a server filesystem location and never reaches a client. Same for image paths: the API exposes `/static/...` URLs derived from paths under `storage_dir`, and anything resolving outside that directory serialises to `null`.

`Base.metadata.create_all` on startup is adequate while the schema is append-only. The moment a column needs to change shape, that becomes Alembic.

---

## Bugs found and lessons learned

Written after building and testing, not before.

**1. Fixed Canny thresholds detected nothing.** The first version hard-coded `Canny(60, 160)`. On the synthetic terrain it found **zero** obstacles on two of the three presets and two enormous merged blobs on the third. The gradient magnitudes of a smooth DEM sit in the single digits — a threshold of 60 is above every edge in the image. Fixed by deriving thresholds per image from its own gradient distribution (high = 97th percentile, low = half that, `L2gradient=True` so Canny's internal magnitude matches the one the thresholds came from). The percentile fixes roughly *what fraction* of the image counts as edge, which is the property actually wanted, and it makes the detector contrast-invariant. Absolute thresholds on gradient magnitude are only meaningful if you know the image's contrast in advance.

**2. Obstacle *count* ranked crater fields backwards.** The classifier keyed off contours per megapixel, and a crater field scored *lower* than a rock-strewn highland (34/Mpx vs 191/Mpx) — because craters are few and large while rocks are many and tiny. Switched the crater rule to obstacle **area fraction** (30.7% vs 8.6%), which separates them cleanly. Picking a feature that matches the physical distinction beats tuning a threshold on the wrong feature.

**3. Summing hazard along the path didn't normalise.** The original risk formula was `Σ(hazard) + energy/capacity`. That sum grows with path length, so a long safe traverse outscores a short lethal one and nothing maps onto the 0.3/0.6 thresholds. Switched to the length-normalised **mean**, with peak hazard reported separately so one extreme segment isn't averaged away. Any metric compared against a fixed threshold has to be scale-free in whatever the threshold isn't measured in.

**4. The hazard cost term could never force a detour.** A test asserting the planner routes around a 0.95-hazard band failed — and the planner was right. `(1 + hazard)` caps at 2×, so crossing one lethal cell always beats a fifteen-cell detour. Cost shaping expresses *preference*, never *prohibition*. Added the lethal-hazard layer alongside the cost layer, which is the structure real navigation stacks use and which I'd have copied from the start if I'd thought about the bound.

**5. Enums in the models package dragged SQLAlchemy into the computation layer.** `terrain_analyzer.py` imported `TerrainClass` from `app.models.enums`, which executed `app/models/__init__.py`, which imported SQLAlchemy. The CV code couldn't be imported without a database driver installed. Moved enums to `app/enums.py`. Package `__init__` files are import side effects, and a leaf module importing from a package pays for everything in that package's `__init__`.

**6. `StrEnum` broke on Python 3.10.** 3.11+ only. Switched to `class X(str, Enum)`, which behaves the same for the `.value` access this code does and widens the supported runtime.

**7. The default elevation range produced implausible terrain.** `elevation_range_m = 120` over a 512 px tile at 2 m/px meant 120 m of relief across 1 km — mean slope 23°, and 9,131 candidate moves blocked by the slope limit on a single plan. Dropped the default to 40 m, which gives 2–9° mean slopes across the presets. This is a *scenario* parameter, not a code constant, which is why `prepare_usgs_terrain.py` measures it from the real DEM and prints the value to put in `.env`.

**8. Rounding stored values broke exact test comparisons.** `total_distance_m` is rounded to millimetres for storage, so `9√2 = 12.727922...` comes back as `12.728` and a `rel=1e-6` assertion fails. Kept the rounding (millimetre precision on a kilometre traverse is not the weak link) and moved the tolerance to match the stored precision. Worth being deliberate about where rounding happens rather than discovering it from a failing test.

---

## Known limitations

- **Descending costs the same as climbing.** `energy_factor` uses `|rise/run|`. A real rover spends less going down than up (and recovers nothing). This over-charges descents; correcting it needs an asymmetric factor and a regenerative-braking assumption that would be invented rather than measured.
- **The planning grid caps at 192 px.** A hazard feature narrower than one grid cell is averaged into its cell and can't be routed around individually.
- **Grayscale intensity is assumed to be a linear elevation proxy.** True for a DEM, false for a photograph, and the system can't tell them apart.
- **No authentication.** Every endpoint is open. Fine for a local demo, not for deployment.
- **Uploads are capped at 25 MB and extension-checked**, but not content-sniffed — a renamed non-image fails at `cv2.imread` with a 422 rather than at upload.
- **Classification thresholds are tuned against generated terrain.** Real USGS tiles will likely need re-tuning; the parameters are all in `config.py` for exactly that reason.
- **Single terrain image per mission.** No mosaicking, no georeferencing across tiles.

---

## Interview questions

**Walk me through the terrain pipeline. Why those OpenCV operations?**
Sobel for slope because slope *is* the spatial derivative of elevation, and the 3×3 kernel gives it cheaply at every pixel — normalised by 8 and passed through `arctan` so the output is degrees, not an arbitrary number. Canny for obstacles because crater rims and rock edges are gradient ridges and Canny's non-maximum suppression plus hysteresis is the standard way to get thin connected edges out of those. `findContours` turns the edge map into discrete regions with measurable centroids and areas, which is what a planner needs — an edge image isn't an obstacle list. Roughness by local variance via box filters because it's O(1) per pixel regardless of window size and it's interpretable.

**Is the hazard heatmap real image processing or a coloured overlay?**
Real. The heatmap is a render of the same `[0,1]` array the planner consumes as its cost surface. If it were decorative, changing the weights would change the picture and not the path. It changes both.

**Why A\* over Dijkstra? Is your heuristic admissible?**
Same optimality guarantee, fewer expansions, because the heuristic biases search toward the goal. Admissible because every edge cost is ≥ its straight-line distance — `hazard ≥ 0` and `energy_factor ≥ 1` are both multiplicative factors of at least 1 — so straight-line distance can never overestimate. It's also consistent, so no node reopening. Measured: 18–37% fewer expansions than Dijkstra at identical total cost.

**What's actually energy-aware about the cost function?**
Each step multiplies by `1 + k·|rise/run|`, where rise/run comes from the elevation model, so climbing a 20° grade costs about 18% more energy per metre than flat ground. Energy accumulates in kWh along the path using the rover's `energy_per_meter_kwh`, and that total is what the feasibility check tests against battery capacity. It's not a proxy — it's the number that decides go/no-go.

**Does your AI compute risk, or narrate it?**
Narrate. Every figure exists in `structured_context` before any model call, that context is stored and returned by the API, and the validator drops any segment ID the model cites that isn't in it. With no API key configured the system still produces complete reports from a template.

**How do you detect an infeasible path?**
Two ways. Energy: total drive energy exceeds battery capacity — arithmetic. Topology: A* exhausts the open set because the rover's slope limit or the lethal-hazard threshold walls off every corridor, and `PathNotFoundError` names which constraint did it. That surfaces as a 422 with `error: no_traversable_path`, not a 500.

**What happens if the LLM call fails?**
Templated fallback with the same numbers, `narrative_source: "template_fallback"`, and the reason recorded. The endpoint never returns 5xx for a model problem. Tested by monkeypatching the client to raise.

**Real or synthetic terrain? Would this generalise to Mars?**
Both are supported. The repo ships a fractal generator so it runs offline and in CI; `prepare_usgs_terrain.py` crops real MOLA/HRSC DEM tiles and reads the true elevation range off the data. The CV and planning code is identical either way — nothing is tuned to the generator except the classification thresholds, which is exactly what I'd expect to re-tune on real tiles and why they're all in `config.py`.
