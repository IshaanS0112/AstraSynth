# Setup

Every step has a verification check. If a check fails, stop there rather than
continuing — see [Troubleshooting](#troubleshooting).

## Requirements

| | |
|---|---|
| Python | 3.10 or newer |
| Node | 22 or newer (only for frontend development; Docker handles it otherwise) |
| Docker | Required for the full stack; not required for the analysis pipeline or tests |
| `ANTHROPIC_API_KEY` | Optional. Without it, reports use the deterministic template |

---

## 1. Run the analysis pipeline (no Docker, no database)

The fastest way to confirm the core works. If this passes, anything that breaks
later is infrastructure rather than logic.

```bash
git clone https://github.com/IshaanS0112/AstraSynth.git
cd AstraSynth

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r backend/requirements-dev.txt

python scripts/generate_terrain.py
python scripts/run_pipeline_demo.py \
    data/sample_terrain/synthetic_crater_field_512.png \
    --rover survey --compare-dijkstra
```

Create the virtual environment at the repository root — the `scripts/` entry
points resolve `backend/` relative to it.

**Check.** Terrain generation is seeded, so the analysis and planning results
are reproducible. Node counts, distances and hazard scores match exactly; the
total path cost can differ in the last decimal place across platforms, since
it is a long floating-point accumulation and the summation order is not
identical everywhere.

```
=== TERRAIN ANALYSIS: synthetic_crater_field_512.png ===
  classification    crater_field
  slope mean/max    9.049 / 60.687 deg
  obstacles         9 (density 34.33/Mpx)
  hazard mean/max   0.2532 / 0.8441

=== PATH PLANNING (survey) ===
  waypoints         186
  distance          1324.665 m
  energy            4.0712 kWh of 6.0 kWh
  nodes expanded    13801

=== A* vs DIJKSTRA (same cost function) ===
  A*          13801 nodes, cost 1474.906...
  Dijkstra    36091 nodes, cost 1474.906...
  A* expanded 61.8% fewer nodes; costs equal: True

=== RISK ASSESSMENT ===
  risk score        0.3226 -> MEDIUM
  feasibility       FEASIBLE

=== MISSION REPORT (source: template_fallback) ===
```

`source: template_fallback` is correct without an API key — the deterministic
report path, working as designed.

### Tests

```bash
cd backend && python -m pytest
```

**Check:** `84 passed, 6 skipped`. The skips are the API tests, which need
PostgreSQL — they run in step 3.

---

## 2. Start the full stack

```bash
export ANTHROPIC_API_KEY=sk-ant-...    # optional
docker compose up --build
```

First build takes 3–5 minutes. Never commit the key — `backend/.env` is
gitignored and `.env.example` carries names only.

**Check.** Logs settle on:

```
astrasynth-db-1        | database system is ready to accept connections
astrasynth-backend-1   | INFO:  Application startup complete.
astrasynth-backend-1   | astrasynth: Seeded 3 rover configs
astrasynth-frontend-1  | start worker processes
```

From a second terminal:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -s http://localhost:8000/rover-configs | python3 -m json.tool
# three configs, battery_capacity_kwh 2.0, 6.0, 9.0
```

| | |
|---|---|
| Dashboard | http://localhost:5173 |
| API documentation | http://localhost:8000/docs |

---

## 3. Run the database-backed tests

With the stack up, the six previously skipped tests can run:

```bash
cd backend
TEST_DATABASE_URL=postgresql+psycopg2://astra:astra@localhost:5432/astrasynth \
  python -m pytest
```

**Check:** `90 passed`.

---

## 4. Walk a mission

1. **New mission** → upload a tile from `data/sample_terrain/`.
2. **Analyse terrain** — the map becomes a hazard heatmap with obstacle regions
   outlined; metrics populate.
3. **Set start** and **Set goal**, then click two points on the map.
4. Choose a rover and **Plan path** — the route draws coloured by per-segment
   hazard.
5. **Assess mission risk**, then **Generate report**.

Two things worth checking, because they are what the system claims:

- Re-plan the same start and goal with each of the three rovers. The route is
  identical; the verdict moves FEASIBLE → INFEASIBLE → FEASIBLE_WITH_MARGIN
  purely on battery arithmetic.
- Expand **Structured context (pre-LLM)** at the bottom of the report. Every
  figure in the narrative appears in that JSON, because the narrative is
  rendered from it.

---

## Development without Docker

Requires a PostgreSQL 16 instance.

```bash
# Database
createdb astrasynth
psql astrasynth -c "CREATE USER astra WITH PASSWORD 'astra' SUPERUSER;"

# Backend
cd backend
cp .env.example .env
uvicorn app.main:app --reload

# Frontend, in a second terminal
cd frontend
npm ci
npm run dev
```

The Vite dev server proxies `/api` and `/static` to port 8000, matching the
nginx configuration used in the Docker image.

### Before opening a pull request

```bash
cd backend  && python -m pytest && ruff check . && ruff format --check .
cd frontend && npm run typecheck && npm run build
```

---

## Other commands

```bash
# A* against Dijkstra across four grid sizes
python scripts/benchmark_planner.py

# Regenerate sample terrain (deterministic for a given seed)
python scripts/generate_terrain.py --size 512

# Full pipeline including the pre-LLM structured context
python scripts/run_pipeline_demo.py <terrain.png> --json
```

### Stopping

```bash
docker compose down       # keeps missions and uploaded terrain
docker compose down -v    # deletes them
```

---

## Troubleshooting

**`Error: pg_config executable not found` while installing `psycopg2-binary`**

pip found no prebuilt wheel for your Python version and fell back to compiling
from source. `backend/requirements.txt` version-ranges the compiled packages so
pip resolves a wheel for whichever interpreter is running; make sure you are on
the current file, then reinstall. `ModuleNotFoundError: No module named
'pydantic_settings'` immediately afterwards is downstream of the same failure —
the install aborted, so nothing landed.

**`source: no such file or directory: .venv/bin/activate`**

The virtual environment is missing or at a different level. It belongs at the
repository root. Rebuilding it loses nothing — a venv holds only downloaded
packages, which is why it is gitignored.

**`command not found: python` / `command not found: pip`**

macOS ships neither name. Use `python3` to create the venv; plain `python` and
`pip` exist only after activation succeeds.

**`port is already allocated` (5432, 8000, 5173)**

Usually a local PostgreSQL on 5432. Stop it, or remap the host side in
`docker-compose.yml` (`"5433:5432"`).

**Backend logs `connection refused` on startup**

PostgreSQL wasn't ready. Compose has a healthcheck gate for this and it should
resolve within ~10 seconds; if not, `docker compose down && docker compose up`.

**`409 Terrain must be analysed before a path can be planned`**

The pipeline stages are ordered and the API enforces it. Analyse first.

**`422 no_traversable_path`**

Working as intended — the rover's slope limit or the lethal-hazard threshold
walls off every route. The error reports how many candidate moves each
constraint blocked. Choose a different goal or a rover with a higher
`max_traversable_slope_deg`.

**Report shows "Deterministic fallback" when an LLM narrative was expected**

No `ANTHROPIC_API_KEY` reached the container. Export it before
`docker compose up`, then `docker compose up --force-recreate backend`. The
report is still complete; the panel shows the exact fallback reason.

**`ModuleNotFoundError: No module named 'app'`**

Wrong working directory or an inactive venv. `pytest` runs from `backend/`;
the `scripts/` entry points run from the repository root.

**6 tests reported as skipped**

Expected without a database. See step 3.
