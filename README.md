# AstraSynth

**AI-assisted planetary mission intelligence — terrain hazard analysis, energy-aware rover path planning, and battery feasibility assessment.**

FastAPI · OpenCV · PostgreSQL · React + TypeScript · Docker

---

## Why I built this

I got interested in rover autonomy after following Chandrayaan-3's landing and reading about how Curiosity's drive planners decide where a rover can actually go. The part that stuck with me is that it isn't one big AI decision — it's a chain of very concrete computations: work out how steep the ground is, work out what you can't drive over, find the cheapest route through what's left, then check whether the battery can actually pay for it.

So I built that chain. The domain is planetary, but the pipeline — sensor imagery → structured risk metrics → constrained optimisation → readable report — is the same shape as drone survey planning or warehouse robot routing.

I also wanted to be able to answer one specific question honestly: *is the AI actually computing anything here, or is your code doing the work and the model just describing it?* In AstraSynth the model does the second thing, deliberately, and the system is built so you can verify that.

---

## What it does

1. **Terrain analysis** — reads a grayscale DEM tile, computes per-pixel slope in degrees (Sobel), detects discrete obstacle regions (Canny → contours), measures surface roughness (local intensity variance), and classifies the terrain with an explainable rule set.
2. **Hazard mapping** — combines slope, obstacle proximity, and roughness into a bounded `[0, 1]` hazard score per pixel, using weights stored alongside every result.
3. **Path planning** — A* over the hazard grid with an energy-aware cost function and a hard traversability constraint from the rover's slope limit.
4. **Risk assessment** — length-normalised hazard exposure plus a battery feasibility check against the rover's actual capacity.
5. **Mission report** — every figure above is frozen into a structured JSON context *first*; the LLM then narrates that context under a JSON-only contract, and any segment it cites that isn't in the context is discarded.

---

## The line between computed and narrated

This is the design decision the rest of the project hangs off.

| Stage | Who computes it |
|---|---|
| Slope, obstacles, roughness | OpenCV — `terrain_analyzer.py` |
| Hazard score per pixel | Weighted formula — `hazard_mapper.py` |
| Optimal route, distance, energy | A* — `path_planner.py` |
| Risk tier, feasibility verdict | Arithmetic — `risk_engine.py` |
| Readable narrative | LLM — `report_generator.py` |

Every number in a generated report exists in `structured_context` before any model is called. That context is stored in the database, returned by `GET /missions/{id}/risk-report`, and rendered in the UI behind a "structured context (pre-LLM)" toggle. If the model call fails, times out, or returns malformed JSON, a template produces the same report from the same numbers — only the prose is missing. **With no `ANTHROPIC_API_KEY` configured at all, the system still produces complete mission reports.**

---

## Measured results

`python scripts/benchmark_planner.py` — A* against Dijkstra over the same grid, same cost function:

| Planning grid | A* nodes expanded | Dijkstra nodes | Reduction | Costs agree |
|---|---|---|---|---|
| 64 × 64 | 3,273 | 3,995 | 18.1% | ✅ |
| 128 × 128 | 13,331 | 16,369 | 18.6% | ✅ |
| 192 × 192 | 24,431 | 36,850 | 33.7% | ✅ |
| 256 × 256 | 41,280 | 65,523 | 37.0% | ✅ |

Run inside a Linux container; absolute timings depend on hardware, the expansion ratio doesn't. "Costs agree" matters more than the speedup: if A* ever returned a *cheaper* cost than Dijkstra, the heuristic would be inadmissible and the path wouldn't be optimal. `pytest` asserts this.

Rule-based terrain classification on the three generated presets:

| Terrain | Mean slope | Obstacles | Obstacle area | Classified as |
|---|---|---|---|---|
| `sandy_plain` | 2.4° | 13 | 4.7% | `sandy_plain` ✅ |
| `rocky_highland` | 7.3° | 50 | 8.6% | `rocky_highland` ✅ |
| `crater_field` | 9.0° | 9 | 30.7% | `crater_field` ✅ |

This is self-consistency against terrain I generated to be those types — **not** validation against labelled Mars terrain, which this repo does not have and does not claim.

---

## Scope

**This is a mission-planning simulation built on public terrain imagery. It does not control real rover hardware, does not ingest live telemetry, and has not been validated against actual mission data.** Rover configurations are illustrative parameter sets of the right order of magnitude, not manufacturer specifications.

`docs/architecture.md` has a full "What's real vs simulated" breakdown and the list of bugs found while building it.

---

## Quick start

> **Never run this before?** `docs/RUNNING.md` is a step-by-step walkthrough from
> opening a terminal through to verifying every stage works, with a check after
> each step and a troubleshooting section.

```bash
git clone https://github.com/Ishaana0112/AstraSynth.git && cd AstraSynth

# Generate sample terrain (no download required, runs offline)
pip install opencv-python-headless numpy
python scripts/generate_terrain.py

# Bring up Postgres + API + dashboard
export ANTHROPIC_API_KEY=sk-...      # optional; without it, reports use the fallback
docker compose up --build
```

- Dashboard → http://localhost:5173
- API docs → http://localhost:8000/docs

Then: **New mission** → upload a tile from `data/sample_terrain/` → **Analyse terrain** → click a start and goal on the map → **Plan path** → **Assess risk** → **Generate report**.

### Without Docker

```bash
# Backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env                 # point DATABASE_URL at your Postgres
uvicorn app.main:app --reload

# Frontend
cd frontend && npm install && npm run dev
```

### Run the pipeline with no database at all

```bash
python scripts/run_pipeline_demo.py data/sample_terrain/synthetic_crater_field_512.png \
    --rover survey --compare-dijkstra --json
```

Prints every intermediate result from CV through to the generated report. The fastest way to see that the numbers come out of the algorithms rather than the API layer.

---

## Terrain data

The repo ships a **generator**, not a dataset. `scripts/generate_terrain.py` produces fractal heightfields (diamond-square) with stamped craters and rock fields — statistically similar to real terrain, reproducible from a seed, and small enough to live in git.

For real data, `scripts/prepare_usgs_terrain.py` crops a tile out of the USGS global Mars DEM:

> **Mars MGS MOLA — MEX HRSC Blended DEM Global 200m v2**, USGS Astrogeology Science Center, 2018.
> [Catalogue entry](https://astrogeology.usgs.gov/search/map/mars_mgs_mola_mex_hrsc_blended_dem_global_200m)
>
> Fergason, R. L., Hare, T. M., & Laura, J. (2018). *HRSC and MOLA Blended Digital Elevation Model at 200m v2.* Astrogeology PDS Annex, U.S. Geological Survey.

The global product is ~11 GB, so the script reads a window out of a file you've downloaded rather than pulling it automatically. It writes a sidecar JSON with the tile's true elevation range so `ELEVATION_RANGE_M` is set from the data instead of guessed — get that wrong and every slope angle downstream is off by a constant factor.

---

## Tests

```bash
cd backend && pytest -v
```

84 tests, no network and no database required. They cover:

- **A\* correctness** — optimal diagonal on flat terrain against a hand-computed distance; identical cost to Dijkstra on random terrain (the empirical admissibility check); detour around an impassable cliff; `PathNotFoundError` when the goal is genuinely walled off; no step ever exceeds the rover's slope limit.
- **Energy model** — flat-ground energy equals rate × distance exactly; climbing the same distance costs strictly more than the flat case; cumulative energy is monotonic and matches the total.
- **Slope estimation** — a ramp rising 1 m per 1 m of ground reads as 45.0°, which is what catches a wrong Sobel normalisation constant.
- **Risk and feasibility** — every threshold boundary including the exact-equality cases; all three risk tiers are reachable from real inputs; the score stays in `[0, 1]` even when a plan is infeasible.
- **Report generation** — hallucinated segment IDs are dropped; malformed JSON and model exceptions degrade to the fallback instead of propagating.

Six further API tests exercise the full HTTP pipeline and skip automatically unless PostgreSQL is reachable (the models use `JSONB` and native `UUID`, which SQLite can't emulate — see `tests/test_api.py`).

---

## API

| Method | Endpoint | |
|---|---|---|
| `POST` | `/missions` | Create mission (multipart terrain upload) |
| `GET` | `/missions` · `/missions/{id}` | List / fetch |
| `POST` | `/missions/{id}/analyze-terrain` | Run the CV pipeline |
| `GET` | `/missions/{id}/terrain-analysis` | Slope map, heatmap, contours, classification |
| `POST` | `/missions/{id}/plan-path` | A* plan for a start, goal, and rover |
| `GET` | `/missions/{id}/path` · `/paths` | Latest / all plans |
| `POST` | `/missions/{id}/assess-risk` | Deterministic risk + feasibility (no model call) |
| `GET` | `/missions/{id}/risk-report` | Risk verdict + structured context |
| `POST` | `/missions/{id}/generate-report` | Narrate the stored context |
| `GET` | `/missions/{id}/ai-report` | Generated narrative |
| `GET` `POST` | `/rover-configs` | List / create rover configurations |

---

## Structure

```
backend/app/
  services/
    terrain_analyzer.py    Sobel slope · adaptive Canny · contours · roughness · classification
    hazard_mapper.py       Weighted hazard scoring · heatmap rendering · planning downsample
    path_planner.py        A* · energy-aware cost · lethal-hazard and slope layers
    risk_engine.py         Risk tiering · battery feasibility
    report_generator.py    Structured context → constrained LLM → validated → fallback
    mission_pipeline.py    Stage orchestration and persistence
  models/ schemas/ routers/ db/
  tests/                   84 tests, no network or DB required
frontend/src/
  components/              TerrainViewer · HazardOverlay · PathVisualization · RiskReportView
  pages/                   Dashboard · MissionDetail · NewMission
scripts/
  generate_terrain.py      Synthetic fractal terrain with craters and rock fields
  prepare_usgs_terrain.py  Crop real USGS Mars DEM tiles
  run_pipeline_demo.py     Full pipeline, no database
  benchmark_planner.py     A* vs Dijkstra
docs/architecture.md       Design decisions · what's real vs simulated · bugs found
```

---

## Roadmap

- [x] OpenCV terrain analysis with adaptive thresholding
- [x] Weighted hazard scoring with stored calculation basis
- [x] A* with energy-aware cost + lethal-hazard layer
- [x] Battery feasibility engine
- [x] Structured-context report generation with validated citations and fallback
- [x] React dashboard with interactive start/goal selection
- [x] Docker Compose stack, 84 tests, A*/Dijkstra benchmark
- [ ] Dynamic re-planning against simulated obstacle discovery mid-traverse
- [ ] CNN terrain classifier compared head-to-head against the rule-based one
- [ ] Multi-rover coordination

---

## Licence

MIT. Terrain data from USGS Astrogeology is public domain and must be cited as shown above.
