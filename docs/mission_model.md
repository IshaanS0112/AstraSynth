# Mission model

Route planning answers "how do I get from here to there". These are the layers
above it — the ones that decide what the rover should do, in what order, and
whether it can talk to anyone while doing it.

---

## Science target selection

`services/mission/science.py`

Once a mission has objectives rather than a destination, "plan a route" stops
being the question. Six candidate outcrops, a battery that pays for four of them,
instruments only some rovers carry, and a communication window to catch: what the
mission needs is a *subset* and an *order*.

That is the orienteering problem — a travelling salesman who need not visit
everything, collects a prize per stop, and works under a budget. It is NP-hard,
and this module does not pretend to solve it exactly.

What it does, in order:

1. **Reachability.** A* between every pair of tour nodes on the real hazard grid
   with the real rover. Targets with no feasible route are dropped *here*, with
   the reason recorded — never silently skipped.
2. **Greedy insertion.** Repeatedly add the unvisited target with the best
   `value / marginal cost` ratio, provided the tour still fits the energy and
   time budgets.
3. **2-opt.** Repeatedly reverse a sub-tour where doing so shortens the route.
   Improves the *order*, never the *selection*, so it cannot break a budget that
   step 2 satisfied.

Steps 2 and 3 are heuristics. On single-digit target counts the optimal tour is
usually recovered, but that is an observation about small instances, not a
guarantee, and the returned `method` string says so.

Legs are ranked by planner *cost*, which already carries hazard and incline, so a
target that is close in metres but across a scarp does not look cheap. Energy and
time are measured separately on the chosen legs, because those are the quantities
the budgets are denominated in.

**Skip reasons are first-class**, because "we didn't go there" is a mission
finding:

| Reason | Meaning |
|---|---|
| `instrument_not_carried` | This rover doesn't have what the target requires |
| `target_on_lethal_terrain` | The target itself sits above the lethal-hazard threshold |
| `unreachable` | No traversable route from any other tour node |
| `budget_exhausted` | Adding it exceeds the energy or time budget |

Instrument gating is what makes a differently-equipped fleet mean something at
the mission layer rather than only at the traversability layer.

---

## Communications

`services/mission/comms.py`

A planetary rover does not have a continuous link. A route that is optimal on
distance and energy but spends its whole length in a radio shadow is not a route
a mission would fly, and a planner that cannot see that constraint will produce
one every time.

Two independent mechanisms, kept separate because they fail differently:

- **Ground relay** — a fixed station. Availability is purely geometric: line of
  sight over the terrain, within a range limit. Position-dependent,
  time-independent.
- **Orbiter pass** — a satellite on a repeating schedule. Purely temporal.
  Time-dependent, position-independent.

A rover has a link when *either* is available.

Line of sight uses `PlanningGrid.elevation_line_of_sight` — the same geometric
test the sensor model uses for occlusion. Measured on a ridge crossing a 40 × 40
map with a 2 m lander antenna: 100% coverage north of the ridge, 0% south, and a
route crossing it holds a link for 64.9% of its cells with a longest blackout of
24.5 minutes over a 1.1-hour traverse.

`longest_blackout_seconds` matters more than the fraction. A mission rule is
normally "never out of contact for more than N hours", and a route can satisfy an
80%-coverage target while violating that inside one long shadow.

**Simplification.** The orbiter model has no per-pass geometry — an overhead pass
is treated as visible from everywhere below. Modelling per-pass elevation angles
is future work and is not claimed here.

---

## Scheduling

`services/mission/scheduling.py`

A route says where. A schedule says when, and it is the schedule that exposes
what a route hides: that the rover arrives twenty minutes after the
communication window closed, or that two science stops push the traverse past the
end of the sol.

Activities are contiguous and non-overlapping *by construction* — `Timeline.add`
rejects anything starting before the previous activity ends — so the timeline is
a partition of mission time and any gap is a bug rather than an omission.

```
TRAVEL   SCIENCE   COMMUNICATE   CHARGE   WAIT   REPLAN
```

Sol boundaries use the Martian solar day, 88,775 s.

---

## Failure modes

Nothing in this system returns `False`. Every refusal carries a machine-readable
`FailureMode`, a human sentence, and a `diagnostics` dictionary with the
measurements behind the verdict — because "no safe path" and "battery cannot pay
for the safe path that exists" are different operational situations needing
different responses, and the type system should say so.

| Mode | Raised when |
|---|---|
| `NO_SAFE_PATH` | Graph search exhausted; goal unreachable |
| `EXCESSIVE_SLOPE` | Corridors closed mainly by the rover's slope limit — *send a different rover* |
| `LETHAL_TERRAIN` | Corridors closed mainly by the hazard layer — *move the objective* |
| `LOW_BATTERY` | A route exists; the energy does not |
| `ROVER_BLOCKED` | The rover is standing somewhere it cannot leave toward the goal |
| `TIMEOUT` | Search or traverse exceeded its budget |
| `CONFLICT_UNRESOLVED` | CBS could not deconflict the fleet within its node budget |
| `START_IS_GOAL`, `COMMUNICATION_LOSS`, `SENSOR_UNCERTAINTY`, `MISSION_ABORT` | Declared; see `planning/outcome.py` |

`EXCESSIVE_SLOPE` versus `LETHAL_TERRAIN` is decided by which blocked-edge
counter dominated — a corridor closed by 40,000 slope rejections and zero hazard
rejections is a rover problem; the reverse is a terrain problem.

---

## Rover model

`RoverSpec` carries more than V1's three numbers:

| Field | Effect |
|---|---|
| `battery_capacity_kwh` | Feasibility verdict, traverse energy budget |
| `max_traversable_slope_deg` | Hard edge removal |
| `energy_per_meter_kwh` | Base energy rate |
| `mass_kg`, `payload_kg` | `payload_factor = (mass + payload) / mass`, a multiplier on energy per metre — rolling resistance is proportional to normal force. First-order only: no drivetrain efficiency curve, no soil mechanics. |
| `nominal_speed_ms`, `roughness_speed_penalty` | Traverse duration, derated by hazard as a roughness proxy |

Payload is **opt-in to the bit**: the defaults give `payload_factor == 1.0`
exactly, so every V1 call site reproduces V1 energy numbers unchanged. Payload
scales energy but never search cost, so a laden and an unladen rover plan the
same route and are charged differently for it — which is correct, and is asserted
in `test_planning_grid.py`.

---

## What is deliberately not modelled

Named here rather than left for a reader to discover:

- Thermal state, dust accumulation, solar-array power generation
- Wheel slip as a function of soil (the Monte Carlo energy factor is a
  stand-in, not a terramechanics model)
- Localisation drift (`Perturbations.localisation_sigma_cells` is declared and
  not yet consumed)
- Per-pass orbiter geometry
- Turning radius, rover footprint, differential speed in the multi-agent time model
- Active exploration — choosing to move somewhere purely to reduce uncertainty.
  The belief machinery to support it exists; the policy does not.
