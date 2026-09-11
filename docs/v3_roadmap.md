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
| 16 | Benchmarks and validation | **Done** — `scripts/benchmark_v3.py`, 209 tests |
| 17 | **API surface for the new engines** | **Not started** |
| 18 | **Persistence for missions/experiments** | **Not started** |
| 19 | **Mission-control frontend redesign** | **Not started** |
| 20 | **3D terrain visualisation** | **Not started** |
| 21 | **Scenario builder, experiment lab UI** | **Not started** |
| 22 | Active exploration (information gain) | **Not started** — abstraction exists, policy does not |

Items 17–21 are the honest gap. Everything computed by items 1–16 is reachable
today from Python and from `scripts/run_mission_demo.py`; none of it is reachable
from the browser. The V1 dashboard still works and still shows V1 results.

---

## Why the backend went first

The frontend redesign is the larger piece of work by wall-clock time and the
smaller piece by risk. A 3D mission-control interface built over engines that
don't exist yet is a mock; built over engines that do, it is a view. Doing it in
the other order would have produced screenshots and no substance.

There is also a concrete dependency: the UI the brief describes — hazard layers,
uncertainty layers, replan animation, Pareto scatter linked to the 3D map,
per-rover telemetry, a mission event stream — is a *view over data structures*.
Those structures now exist and are stable (`TraverseResult`, `MissionEvent`,
`CBSSolution`, the Pareto sweep dictionary, `Timeline`). Building the API and the
UI against settled shapes is straightforward; building them against shapes still
in flux is rework.

---

## What comes next, in order

**Phase A — API surface.** Endpoints over the existing engines:
`POST /missions/{id}/plan-multi-objective`, `POST /missions/{id}/simulate-traverse`,
`GET /missions/{id}/events`, `POST /missions/{id}/deconflict`,
`POST /experiments` (Monte Carlo). Persistence for `Mission`, `ScienceTarget`,
`Experiment`. Alembic, since the schema stops being append-only here.

**Phase B — telemetry transport.** The event log is already timestamped and
typed; server-sent events over `GET /missions/{id}/events` is enough. Sending the
full terrain grid through React state on every tick is the thing to avoid —
terrain is static and cached, only rover state is dynamic.

**Phase C — 3D terrain view.** One renderer, not several. The existing frontend
is React + TypeScript with no 3D dependency, so this is an addition rather than a
migration. The renderer must stay separate from planning logic.

**Phase D — mission-control layout.** 3D view dominant, telemetry and event log
in drill-down panels, layer toggles for elevation / slope / hazard / uncertainty
/ communications / science / planned route / executed route.

**Phase E — replan visualisation.** The hero interaction: obstacle detected →
belief updated → affected region highlighted → old route fades → new route
appears, with the repair's own numbers beside it. Every one of those quantities is
already in `MissionEvent.detail`.

**Phase F — scenario builder and experiment lab.** Both are forms over existing
dataclasses; neither needs new engine work.

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
