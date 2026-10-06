# V3 audit and roadmap

Written before the V3 work started (the audit) and updated as it landed (the
status table). It exists so the gap between what this repo claims and what it
contains is written down by the person who knows it best, rather than discovered
by a reader.

---

## Audit: what was actually here

The V3 brief described the starting point as a system with "D* Lite, Theta*, CBS
and a CNN classifier". **That was not the case.** The repository at
`49827b1` contained:

- `terrain_analyzer.py` — Sobel slope, adaptive-Canny obstacles, box-filter
  roughness, rule-based classification
- `hazard_mapper.py` — a three-term weighted hazard score
- `path_planner.py` — **A\*** (and Dijkstra, as the same search with the
  heuristic zeroed). Nothing else.
- `risk_engine.py`, `report_generator.py`, `mission_pipeline.py`
- A React/TypeScript dashboard, 5 API routers, 90 tests, CI across two Python
  versions with a real PostgreSQL service

`grep -ri "d\* lite\|theta\|CBS\|CNN"` over the codebase matched only two files:
`README.md` and `docs/architecture.md`, in both cases in the **roadmap**, as
unchecked boxes and explicit "documented V2" notes. The V1 authors were honest;
the brief's premise was mistaken.

That changed the plan. There was no V2 to extend — the correct move was to build
those planners properly against the V1 foundation, which was well-structured
enough to extend rather than replace.

### What the V1 code got right, and was kept

- **Deterministic computation, LLM confined to narration.** Every number exists
  in `structured_context` before any model call. Untouched.
- **Auditability.** Every result carries the parameters that produced it. Every
  new module follows the same rule.
- **Honest scope statements.** `docs/architecture.md` already separated real from
  simulated and listed bugs found. Both were extended, not replaced.
- **Layering.** Services never import routers; enums live outside the models
  package so the computation layer never pulls in SQLAlchemy.

### What had to change structurally

The cost function was inlined in A*'s search loop. With one planner that is fine.
With four it is fatal: two planners with two copies of the cost function cannot
be compared, because any difference in result is ambiguous between "different
search strategy" and "different graph".

So the graph moved into `planning/grid.py` and the searches moved beside it. The
V1 planner was refactored onto the shared model and verified to be
**bit-identical**: same routes, same costs, same metadata keys, same benchmark
numbers (3,273 / 13,331 / 24,431 / 41,280 node expansions, unchanged).

---

## Status

| # | Area | State |
|---|---|---|
| 1 | Shared cost model (`PlanningGrid`) | **Done** |
| 2 | Structured failure modes | **Done** |
| 3 | Hazard uncertainty, propagated | **Done** |
| 4 | Belief state, Bayesian updates | **Done** |
| 5 | Sensor model: range, noise, occlusion | **Done** |
| 6 | Theta* any-angle planning | **Done** |
| 7 | D* Lite incremental replanning | **Done** |
| 8 | CBS multi-rover, heterogeneous | **Done** |
| 9 | Pareto front / multi-objective | **Done** |
| 10 | Communication-aware evaluation | **Done** |
| 11 | Science target selection and tours | **Done** |
| 12 | Mission timeline | **Done** |
| 13 | Traverse simulation, event log | **Done** |
| 14 | Monte Carlo robustness | **Done** |
| 15 | Rover digital twin (payload, speed) | **Partial** — energy and duration model; no thermal, no slip model |
| 16 | Benchmarks and validation | **Done** — `scripts/benchmark_v3.py` |
| 17 | HTTP API over the V3 engines | **Done** |
| 18 | Persistence for science targets, traverses, experiments | **Done** |
| 19 | Mission-control frontend | **Done** |
| 20 | 3-D terrain visualisation with layer toggles | **Done** |
| 21 | Scenario building and experiment lab | **Done** |
| 22 | Active exploration (information gain) | **Not started** — abstraction exists, policy does not |
| 23 | Background job queue for long studies | **Not started** — studies run synchronously and are capped |
| 24 | Live telemetry transport (SSE/WebSocket) | **Not needed yet** — see below |
| 25 | CNN terrain classifier vs the rule-based one | **Not started** |

238 tests, zero skipped, across the analysis engines and the full HTTP surface.

---

## What Phases 17–21 turned out to be

**The API is thin, and stayed thin.** `services/autonomy.py` holds the ordering
rules and the persistence; the routers validate and serialise. The one thing this
layer had to decide for itself is where ground truth comes from: every engine that
*executes* rather than *plans* needs terrain to execute against, and a real mission
does not have one — that is the entire point of the belief model. So truth is
synthesised from a recorded seed, and the response says so in
`parameters.truth_model` rather than letting a reader assume the terrain was
measured.

**No migration tool yet, deliberately.** The three new tables are *new*, not
alterations, so V1's `create_all` still covers the schema and Alembic would be
ceremony. The note in `architecture.md` stands: the first column that changes shape
is what buys a migration tool.

**Long studies are capped rather than queued.** Monte Carlo runs on the request
thread, so `schemas/v3.py` bounds the trial count and CBS carries a wall-clock
budget as well as a node budget. Accepting a thousand-trial study synchronously
would trade a clear 422 for a silent gateway timeout. A job queue is Phase 23, and
until it exists the cap is the honest interface.

**Telemetry did not need a transport.** The traverse completes on the server and
returns a full run — the executed path plus a timestamped event log — so the UI
replays rather than streams. Server-sent events buy nothing until a mission runs
longer than a request, which is the same condition that buys the job queue.

**Fleet coordination runs on a coarser grid than navigation.** The low-level CBS
state space is cells × ticks, so halving the resolution cuts it by roughly eight.
Nothing that matters is lost: the coordination question is who crosses the middle
first, not which rock to pass on the left, and the second is answered by D* Lite at
full resolution once each rover is driving its own leg.

**The renderer is not a React component.** `three/TerrainScene.ts` is a plain class
with four setters. See [`frontend.md`](frontend.md) for why, and for what CI checks
versus what `scripts/check_mission_control.mjs` checks.

---

## Why the backend went first

The frontend redesign was the larger piece by wall-clock time and the smaller
piece by risk. A 3-D mission-control interface built over engines that don't exist
is a mock; built over engines that do, it is a view.

The dependency was concrete. Everything the UI shows — hazard layers, uncertainty
layers, repair events, a Pareto scatter linked to the map, per-rover telemetry — is
a *view over data structures*. Building against `TraverseResult`, `MissionEvent`,
`CBSSolution`, the sweep dictionary and `Timeline` once they were settled was
straightforward. Building against them while they moved would have been rework.

---

## What is still missing

**Active exploration.** Choosing to drive somewhere purely to reduce uncertainty,
rather than because it is on the way. The belief machinery supports it —
`planning_hazard(k)` already prices unknown ground — but there is no policy that
values information for its own sake.

**A job queue.** Until one exists, a study has to finish inside a request, which is
why the trial count is capped and CBS has a wall-clock budget.

**Communications in the UI.** The engine computes coverage, blackout fraction and
longest blackout; the 3-D view does not yet draw a coverage layer or a relay
marker.

**A CNN terrain classifier** compared head-to-head against the rule-based one.
Still blocked on the same thing as in V1: there is no labelled planetary terrain
set here to train on, and the interesting output would be the comparison.

---

## Claims this repo is careful not to make

- Not flight software, not NASA-affiliated, no flight heritage.
- Terrain is synthetic and labelled as such. Classification accuracy is
  self-consistency against terrain generated to be those types, **not** validation
  against labelled planetary data, which this repo does not have.
- Rover parameters are illustrative planning values of the right order of
  magnitude, not manufacturer specifications.
- Theta* is not optimal. CBS is optimal for sum-of-costs *within its node budget*
  and reports failure rather than returning a route it cannot vouch for.
- The Pareto front is the convex hull of the true front, not the whole of it.
- Every benchmark number in these docs was produced by the scripts in `scripts/`
  on the machine that wrote them. None is quoted from elsewhere.
