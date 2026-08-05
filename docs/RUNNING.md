# Running AstraSynth

Written for a fresh machine. Every step has a **check** — if the check fails, stop there rather than continuing, and see [Troubleshooting](#troubleshooting).

The project lives at:

```
/Users/ishaansingh/BASE/02-projects/AstraSynth
```

---

## Step 0 — Open a terminal in the project folder

Open **Terminal** (Cmd+Space → "Terminal") and run:

```bash
cd ~/BASE/02-projects/AstraSynth
```

**Check:** `ls` should print:

```
README.md  backend  data  docker-compose.yml  docs  frontend  scripts
```

If you'd rather use VS Code: `code ~/BASE/02-projects/AstraSynth`, then use its built-in terminal (Ctrl+`). Open the **AstraSynth** folder itself — not `BASE` and not `backend` — or the import paths won't resolve.

---

## Step 1 — Prove the algorithms work (2 minutes, no Docker, no database)

Do this first. It runs the entire analysis pipeline end to end and needs nothing but Python. If this works, the core of the project works, and anything that breaks later is infrastructure, not logic.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
```

That takes a minute or two. Your prompt should now start with `(.venv)`.

```bash
python scripts/run_pipeline_demo.py \
    data/sample_terrain/synthetic_crater_field_512.png \
    --rover survey --compare-dijkstra
```

**Check:** you should see roughly this — exact numbers will match, since everything is seeded:

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
  A*          13801 nodes, cost 1474.9063
  Dijkstra    36091 nodes, cost 1474.9063
  A* expanded 61.8% fewer nodes; costs equal: True

=== RISK ASSESSMENT ===
  risk score        0.3226 -> MEDIUM
  feasibility       FEASIBLE

=== MISSION REPORT (source: template_fallback) ===
  Path risk: MEDIUM (score 0.3226). Feasibility: FEASIBLE. ...
```

`source: template_fallback` is **correct** here — you haven't set an API key yet, so the report is built from the deterministic template. That's the fallback path working as designed.

### Run the tests

```bash
cd backend && pytest && cd ..
```

**Check:** `84 passed, 6 skipped`. The 6 skips are the API tests, which need PostgreSQL — they'll run in Step 3.

---

## Step 2 — Install Docker Desktop

This is what runs PostgreSQL, the API, and the dashboard together.

1. Download from **https://www.docker.com/products/docker-desktop/** — pick **Apple Silicon** or **Intel Chip** to match your Mac (Apple menu → About This Mac).
2. Open the `.dmg`, drag Docker to Applications, launch it, accept the agreement.
3. Wait for the whale icon in the menu bar to stop animating.

**Check:**

```bash
docker --version && docker compose version
```

Both should print a version. If you get `command not found`, Docker Desktop isn't running — launch it from Applications and wait for the whale to settle.

---

## Step 3 — Start everything

```bash
cd ~/BASE/02-projects/AstraSynth
docker compose up --build
```

First run takes 3–5 minutes (downloading Postgres, Python, Node, building both images). Later runs take seconds.

**Optional, for AI-written reports** — before running the command above:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
```

Without it everything still works; reports use the deterministic template. Don't put the key in a file you commit.

**Check:** the log settles and stops scrolling, ending with something like:

```
astrasynth-backend-1   | INFO:     Application startup complete.
astrasynth-backend-1   | ... astrasynth: Seeded 3 rover configs
astrasynth-frontend-1  | ... start worker processes
```

Leave this terminal running. Open a **second** terminal tab (Cmd+T) for the checks below.

```bash
curl http://localhost:8000/health
# {"status":"ok"}

curl -s http://localhost:8000/rover-configs | python3 -m json.tool | head -20
# three rover configs with battery_capacity_kwh 2.0, 6.0, 9.0
```

Now open **http://localhost:5173** in your browser. You should see a dark "AstraSynth" header and an empty missions list.

Also worth opening: **http://localhost:8000/docs** — auto-generated interactive API documentation for all 15 endpoints.

---

## Step 4 — Run a mission through the UI

1. **http://localhost:5173** → click **New mission** (top right).
2. **Mission name:** anything, e.g. `Crater field traverse`.
3. **Terrain source:** `synthetic` (this field is provenance — it shows on every report).
4. **Terrain image:** click, then navigate to
   `BASE → 02-projects → AstraSynth → data → sample_terrain` and pick
   `synthetic_crater_field_512.png`.
   *(Tip: in the file picker press Cmd+Shift+G and paste `~/BASE/02-projects/AstraSynth/data/sample_terrain`.)*
5. **Create mission.**

You land on the mission page. Now work down the right-hand panel:

| # | Action | What should happen |
|---|---|---|
| 1 | **Analyse terrain** | Takes ~2 s. The map turns into a colour hazard heatmap, yellow dashed circles appear over detected obstacles, and the metrics fill in: `crater_field`, mean slope ~9°, 9 obstacles. |
| 2 | **Set start** → click near the top-left of the map | A teal `START` marker appears where you clicked. |
| 3 | **Set goal** → click near the bottom-right | A pink `GOAL` marker appears. |
| 4 | Pick a rover — start with **Survey-Class (medium)** | |
| 5 | **Plan path (A\*)** | A route draws across the map, green through amber to red by hazard. Metrics show distance, energy, waypoints, nodes expanded. |
| 6 | **Assess mission risk** | A report panel appears below with risk tier, feasibility, and a battery utilisation bar. |
| 7 | **Generate report** | A narrative appears with a badge reading either **LLM narrative** or **Deterministic fallback**. |

**Things worth checking, because they're what the project is actually claiming:**

- Toggle the map between **TERRAIN / HAZARD / SLOPE** (buttons top-left of the map). The hazard layer is the same array the planner used as its cost surface, not decoration.
- Expand **"Structured context (pre-LLM, auditable)"** at the bottom. Every number in the narrative above appears in that JSON — which is the point: the model narrated it, it didn't compute it.
- Re-plan the same start and goal with **Scout-Class (light)**. Same route, but feasibility flips to **INFEASIBLE** — the traverse needs more energy than a 2.0 kWh battery holds. Then try **Heavy Lab-Class**: FEASIBLE_WITH_MARGIN. Same terrain, three different verdicts, purely from battery arithmetic.

---

## Step 5 — Run the API tests against the live database

Now that Postgres is up, the 6 skipped tests can run:

```bash
cd ~/BASE/02-projects/AstraSynth/backend
source ../.venv/bin/activate
pytest -v tests/test_api.py
```

**Check:** `6 passed`. These drive the whole HTTP pipeline — upload → analyse → plan → assess → report — and assert that an unreachable goal returns 422 rather than 500, and that server file paths never leak into responses.

Full suite:

```bash
pytest
# 90 passed
```

---

## Stopping and restarting

```bash
# Stop (in the terminal running compose): Ctrl+C
# Or from anywhere:
docker compose down

# Restart later — fast, images already built:
docker compose up

# Wipe the database and uploaded terrain and start clean:
docker compose down -v
```

`down` keeps your missions. `down -v` deletes them.

---

## Extras worth running

```bash
# A* vs Dijkstra across four grid sizes
python scripts/benchmark_planner.py

# Regenerate the sample terrain (deterministic — same seed, same file)
python scripts/generate_terrain.py --size 512

# Every intermediate value including the full pre-LLM JSON context
python scripts/run_pipeline_demo.py \
    data/sample_terrain/synthetic_rocky_highland_512.png --json
```

---

## Running without Docker

Only needed if Docker won't install. You'll have to supply PostgreSQL yourself.

```bash
# 1. PostgreSQL
brew install postgresql@16 && brew services start postgresql@16
createdb astrasynth
psql astrasynth -c "CREATE USER astra WITH PASSWORD 'astra' SUPERUSER;"

# 2. Backend (terminal 1)
cd ~/BASE/02-projects/AstraSynth/backend
source ../.venv/bin/activate
cp .env.example .env
uvicorn app.main:app --reload

# 3. Frontend (terminal 2)
cd ~/BASE/02-projects/AstraSynth/frontend
npm install
npm run dev
```

Dashboard at http://localhost:5173, API at http://localhost:8000. The Vite dev server proxies `/api` and `/static` to port 8000, so this behaves the same as the Docker setup.

---

## Troubleshooting

**`docker: command not found`**
Docker Desktop isn't running. Launch it from Applications and wait for the menu-bar whale to stop animating.

**`port is already allocated` (5432, 8000, or 5173)**
Something else is using that port — usually a local Postgres on 5432.
`brew services stop postgresql@16`, or change the left-hand number in `docker-compose.yml` (`"5433:5432"`) and restart.

**Backend logs `connection refused` on startup**
Postgres wasn't ready yet. Compose has a healthcheck for this, so it should self-resolve within ~10 seconds. If it doesn't: `docker compose down && docker compose up`.

**Dashboard loads but shows an error banner**
The backend isn't reachable. Check `curl http://localhost:8000/health` and look at the backend logs: `docker compose logs backend --tail 50`.

**"Terrain must be analysed before a path can be planned" (409)**
Click **Analyse terrain** first. The stages are ordered and the API enforces it.

**Planning returns "no traversable path" (422)**
Working as intended — the rover's slope limit or the lethal-hazard threshold walls off every route. Pick a different goal, or a rover with a higher `max_traversable_slope_deg`. The error message tells you how many moves each constraint blocked.

**Report says "Deterministic fallback" and you expected the LLM**
No `ANTHROPIC_API_KEY` reached the container. Export it in the shell *before* `docker compose up`, then `docker compose up --force-recreate backend`. The report is still complete — only the prose differs. The panel shows the exact fallback reason.

**`ModuleNotFoundError: No module named 'app'`**
You're in the wrong directory or the venv isn't active. `cd` into `backend/` for pytest, and into the project root for the `scripts/` commands. Check for `(.venv)` in your prompt.

**Tests report `6 skipped`**
Expected without a database. Start Docker (Step 3), then they run.

---

## What "working fine" looks like

| | |
|---|---|
| `pytest` | 84 passed, 6 skipped (or 90 passed with Docker up) |
| `curl localhost:8000/health` | `{"status":"ok"}` |
| http://localhost:8000/docs | 15 endpoints listed |
| http://localhost:5173 | Dashboard loads, missions list renders |
| Analyse terrain | Heatmap + obstacle circles appear, `crater_field` classification |
| Plan path | Coloured route drawn, ~1300 m, ~186 waypoints |
| Assess risk | MEDIUM / FEASIBLE for Survey-Class |
| Same route, Scout-Class | INFEASIBLE — the feasibility engine is live, not cosmetic |
| Structured context toggle | Every narrative figure present in the JSON |
| `benchmark_planner.py` | A* 18–37% fewer nodes, "costs agree: True" at every size |
