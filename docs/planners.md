# Planners

Four search strategies over one cost model. The model lives in
[`backend/app/services/planning/grid.py`](../backend/app/services/planning/grid.py);
each planner beside it contains only its search.

That separation is the point. V1 had a single planner with the cost function
inlined in its loop, and the moment a second planner exists that arrangement
stops working — two planners with two copies of the cost function cannot be
compared, because any difference in result is ambiguous between "different
search" and "different graph". A benchmark that reports different node counts
for the same optimal cost is only meaningful if the graph is provably identical.

---

## The shared cost model

For a move from cell `a` to an 8-connected neighbour `b`:

```
cost(a, b) = distance_m(a, b) · (1 + w_h · hazard(b)) · (1 + w_e · k · |rise/run|)
```

`w_h = w_e = 1` by default, which reproduces the V1 function exactly. The weights
exist so the multi-objective sweep can reshape the graph without any planner
knowing it happened.

Two constraint layers, unchanged from V1:

| Layer | Mechanism | Effect |
|---|---|---|
| Cost | `(1 + hazard)`, bounded by 2 | Shapes the route inside drivable ground |
| Lethal | `hazard ≥ max_hazard`, `\|slope\| > rover limit` | Removes edges from the graph |

The cost layer alone is a *preference*, never a *prohibition*: doubling the price
of one cell will never outweigh a fifteen-cell detour. That is why the lethal
layer is not optional.

**Admissibility.** `hazard ≥ 0` and the energy factor is `≥ 1` at non-negative
weights, so every edge satisfies `cost(a, b) ≥ distance_m(a, b)`. Straight-line
distance in metres therefore never overestimates remaining cost: the heuristic
is admissible for A*, Theta* and D* Lite, and consistent, so none of them needs
to re-open a closed node. A negative weight would break the bound, so
`PlanningGrid` refuses to be constructed with one.

**No corner cutting.** A diagonal move additionally requires both cells the
diagonal clips to be non-lethal. See [architecture.md](architecture.md), "Bugs
found", #8.

---

## A* — the baseline

`planning/astar.py`. Optimal under the admissible heuristic. Everything else is
measured against it.

`use_heuristic=False` zeroes the heuristic, reducing the identical search to
Dijkstra. `scripts/benchmark_planner.py` runs both on the same grid and asserts
they agree on optimal cost — the empirical check that the heuristic really is
admissible. If A* ever returned a *cheaper* cost than Dijkstra, the heuristic
would be inadmissible and the path would not be optimal.

| Planning grid | A* nodes | Dijkstra nodes | Reduction | Costs agree |
|---|---|---|---|---|
| 64 × 64 | 3,273 | 3,995 | 18.1% | ✅ |
| 128 × 128 | 13,331 | 16,369 | 18.6% | ✅ |
| 192 × 192 | 24,431 | 36,850 | 33.7% | ✅ |
| 256 × 256 | 41,280 | 65,523 | 37.0% | ✅ |

---

## Theta* — any-angle

`planning/theta_star.py`. A* with one change: when relaxing a neighbour, it first
asks whether that neighbour is visible from the current node's *parent*. If so
the neighbour attaches to the grandparent directly and the intermediate cell is
skipped. Routes become straight segments between corners.

**Theta\* is not optimal and this repo does not claim it is.** Two reasons, both
real here:

1. Theta* is itself an approximation even on a uniform grid — it considers a
   shortcut only to the immediate parent, not to every ancestor.
2. Segment costs come from a *sampled line integral*
   (`PlanningGrid.segment`, 2 samples per cell). A sampled integral is not
   identical to the sum of the grid steps beneath it, so a Theta* cost and an A*
   cost are not two measurements of the same quantity and are never compared.

What *is* comparable, and what the tests and benchmark check, is route shape:
Euclidean length and heading changes. Measured on open ground broken by discrete
rocks — the terrain any-angle planning is for:

| Grid | A* length | Theta* length | Shorter | A* turns | Theta* turns | A* ms | Theta* ms |
|---|---|---|---|---|---|---|---|
| 48 × 48 | 131.0 m | 124.8 m | 4.7% | 17 | 7 | 10 | 147 |
| 64 × 64 | 176.2 m | 168.7 m | 4.3% | 17 | 6 | 12 | 240 |
| 96 × 96 | 269.1 m | 259.5 m | 3.6% | 28 | 18 | 32 | 566 |

Fewer than half the turns, and 10–18× slower: line-of-sight checks are not free.
On a *continuous random cost field* the advantage largely disappears, because
there is no straight line to find — every cell costs something different, so the
cheapest route genuinely wanders. A* remains the default planner.

---

## D* Lite — incremental replanning

`planning/dstar_lite.py`. Searches backwards from the goal, so g-values are costs
*to* the goal and stay valid as the rover moves. When edge costs change it
repairs only the part of the tree those edges could have affected.

Two claims, and how each is checked:

1. **Correct** — after any sequence of moves and map updates, the route it holds
   is the route a fresh A* would compute on the current map, *at the same cost*.
   `test_dstar_lite.py` asserts this on randomised terrain with randomised
   obstacle reveals, and separately after the rover has moved (which is what
   exercises the `k_m` bookkeeping).
2. **Cheaper** — a repair expands fewer vertices than a from-scratch search.

Without (1), (2) is worthless. Measured over a full traverse with a sensor
revealing a 3-cell radius, comparing each repair against an A* run from the
rover's current position on the same updated map:

| Grid | Steps | Repairs | D* Lite vertices | From-scratch A* nodes | Saving | Reroutes |
|---|---|---|---|---|---|---|
| 48 × 48 | 57 | 15 | 232 | 8,209 | 97.2% | 3 |
| 64 × 64 | 68 | 17 | 138 | 16,845 | 99.2% | 2 |
| 96 × 96 | 111 | 19 | 171 | 23,856 | 99.3% | 2 |

**This is the case D\* Lite is for.** On a single *large, global* map change it
has no advantage and can be worse — revealing a 130-cell band in one go costs
more expansions than a fresh A*. The advantage is specifically in small local
surprises discovered as the rover drives, which is what a sensor produces.

Edge costs here are **directed** — `c(u, v)` charges the hazard of `v`, so
`c(u, v) ≠ c(v, u)`. When a cell's hazard changes, it is that cell's
*predecessors* whose `rhs` must be recomputed. Getting this wrong produces a
planner that is right on symmetric terrain and quietly wrong everywhere else.

A repair that leaves the route unchanged is a confirmation, not a diversion. The
event log distinguishes `replans` from `reroutes` for exactly that reason.

---

## CBS — multi-rover coordination

`planning/cbs.py`. Each rover carries its own `PlanningGrid`, so a Scout with a
20° slope limit and a Heavy Lab with 30° search genuinely different graphs over
the same terrain. Conflicts between the resulting routes are resolved by
branching in the space of *constraints*, not in the joint state space (three
rovers on a 192 × 192 grid is ~5 × 10¹³ joint states).

Every solution is verified conflict-free by `find_conflicts` before being
returned, and the test suite re-runs that check independently — a solver that
grades its own homework is not evidence.

Two enhancements that are load-bearing rather than decorative:

- **Conflict-avoidance table.** Among paths of equal cost — and only among those
  — the low-level search prefers the one that shares fewest `(cell, time)` slots
  with the rest of the fleet. Cost ordering is untouched, so optimality holds.
- **Disjoint splitting** (Li et al., 2019). Ordinary CBS branches into "A avoids
  *v* at *t*" and "B avoids *v* at *t*", and those solution sets overlap;
  everything where neither goes near *v* is explored twice. Splitting into "A
  *is* at *v* at *t*" and "A is *not*" partitions the space instead.

Without both, this solver does not terminate on open ground: measured on a flat
24 × 24 grid with three rovers, 4,000 constraint-tree nodes without ever getting
below one remaining conflict. With both:

| Rovers | CT nodes | Low-level searches | Sum of costs | Makespan |
|---|---|---|---|---|
| 2 | 13 | 38 | 102.0 | 20 |
| 3 | 705 | 2,819 | 143.7 | 20 |
| 4 | 674 | 3,361 | 185.8 | 20 |
| 5 | — | — | — | budget of 2,000 CT nodes exhausted |

Node counts are deterministic; wall-clock is not, and varies by several times
with machine load, so it is left to the benchmark's own output rather than
quoted here. The low-level search caches the traversable graph once per solve —
it is static for the duration, and re-deriving it through numpy scalar indexing
was consuming nearly all of the search time on larger grids.

That scenario is deliberately near worst case: flat open ground with every rover
crossing to the opposite corner, so every pair conflicts. CBS is exponential in
the number of conflicts, and five all-crossing rovers on featureless terrain is
what that looks like. On structured terrain — corridors, obstacle fields, rovers
with separated objectives — it resolves in tens of nodes.

**Coordination runs on a coarser grid than navigation**, and that is a design
choice rather than a workaround. The low-level state space is cells × ticks, so
halving the resolution cuts it by roughly eight; `run_mission_demo.py`
deconflicts three rovers on a 32 × 32 grid at 16 m/cell in 83 constraint-tree
nodes, where the same fleet on the 64 × 64 navigation grid exhausts a 45-second
budget after 37. Nothing that matters is lost: the coordination question is
"who goes through the middle first", not "which rock do I pass on the left",
and the second is answered by D* Lite at full resolution once each rover is
driving its own leg.

A further practical bound: the low-level horizon is tightened to the longest
unconstrained path plus a few ticks of slack per rover, once the root search has
measured it. The root needs a generous bound because nothing is known yet; every
search after it does not, and the difference is multiplicative.

**Simplifications, stated plainly.** One grid move takes one tick for every
rover, so heterogeneous *speed* is priced in cost rather than duration; rovers
occupy exactly one cell, with no footprint or turning radius; a rover parked on
its goal continues to block it.

---

## Multi-objective routes and the Pareto front

`planning/pareto.py`. Instead of one "optimal" route — optimal for one weighting
somebody chose — the same planner is run at a spread of weightings and every
resulting route is measured on the *unweighted* model, so routes found at
different weights stay comparable. Routes worse on every objective at once are
discarded.

Measured on a 50 × 50 random field, 9 weightings → 9 distinct routes → 8 on the
front:

| Weighting | Distance | Energy | Mean hazard | Max hazard |
|---|---|---|---|---|
| `w_h=0, w_e=0` | 132.9 m | 0.4138 kWh | 0.273 | 0.536 |
| `w_h=1, w_e=1` (V1 default) | 137.6 m | 0.4274 kWh | 0.173 | 0.461 |
| `w_h=2, w_e=1` | 140.0 m | 0.4354 kWh | 0.160 | 0.385 |
| `w_h=8, w_e=1` | 147.0 m | 0.4596 kWh | 0.135 | 0.380 |

10.6% further for 51% less mean hazard exposure. Which of those a mission should
fly is not a solver decision.

**What this method cannot find.** Weighted-sum scalarisation recovers only the
*convex hull* of the true Pareto front. A route that is Pareto-optimal but sits
in a concavity is unreachable by any choice of weights and will not appear, no
matter how fine the sweep. Recovering those needs ε-constraint or a proper
multi-objective search, which is not implemented. The front returned is a subset
of the true front and is labelled as one.

---

## Failure modes

No planner in this package returns `False`. Failures carry a machine-readable
`FailureMode`, a human sentence, and the measurements behind the verdict:

```
NO_SAFE_PATH  EXCESSIVE_SLOPE  LETHAL_TERRAIN  LOW_BATTERY  ROVER_BLOCKED
START_IS_GOAL  COMMUNICATION_LOSS  SENSOR_UNCERTAINTY  TIMEOUT
CONFLICT_UNRESOLVED  MISSION_ABORT
```

`EXCESSIVE_SLOPE` and `LETHAL_TERRAIN` are separated by which counter dominated
the blocked-edge tally, because they call for different responses: the first
means send a different rover, the second means move the objective.

---

Reproduce every table above:

```bash
python scripts/benchmark_planner.py          # A* vs Dijkstra
python scripts/benchmark_v3.py               # everything else
```
