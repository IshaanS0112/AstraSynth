# Uncertainty

V1 had one hazard map and every component read it. That is a simulation in which
the rover is omniscient: it plans around a boulder it has no way of having seen.
Every result derived from it — route cost, replan counts, mission success rates —
is optimistic by an unmeasurable amount.

V3 separates three things, and only two of them are ever visible to a planner.

| | What it is | Who holds it |
|---|---|---|
| **Reality** | The ground-truth hazard field | The simulator. A planner never receives it. |
| **Belief** | What the rover currently holds to be true | `BeliefState` — what planners search |
| **Observation** | A noisy sensor reading of reality, at one place and time | `RoverSensor` |

`test_execution.py::test_the_planner_is_never_handed_the_truth` asserts the
separation directly: the template grid is unmodified after a traverse, and every
unobserved cell still holds the orbital prior.

---

## Where hazard uncertainty comes from

Uncertainty is **propagated, not invented**. Hazard is a weighted sum of three
terms, each with an identified error source:

```
σ_hazard = √( Σᵢ (wᵢ · σᵢ)² )
```

| Term | Error source | Derivation |
|---|---|---|
| Slope | DEM quantisation | The DEM is an 8-bit image, so elevation is known to `elevation_range_m / 256` m. A central-difference gradient over a `meters_per_pixel` baseline carries error ≈ `q / mpp`; propagated through `slope = atan(g)` that is `σ_slope = σ_g / (1 + g²)`, normalised by `slope_reference_deg`, scaled by `(1 + roughness)` because a 3×3 Sobel kernel is a plane fit and a plane describes rough ground badly. |
| Obstacle proximity | Canny edge localisation | Canny locates an edge to ≈1 px, so the distance transform carries ≈ `1 px · mpp` of error. The penalty is `1/(1+d)`, derivative `−1/(1+d)²` — so the same positional error matters enormously next to an obstacle and not at all far from one. |
| Roughness | Window sampling | For a sample standard deviation over *n* points, `σ_s ≈ s / √(2(n−1))`. A 9×9 window is 81 samples, so ≈8% of itself. |

**Two stated approximations.** First, the quadrature rule assumes the three
sources are independent; slope and roughness are computed from the same pixels,
so it mildly under-states the total. Second, every component σ is clipped to
0.5 — the maximum standard deviation a `[0,1]`-bounded quantity can have (the
Bernoulli bound). Linear propagation knows nothing about that bound and will
report more where a derivative blows up, right on an obstacle edge. The clip is
the honest statement that linear propagation has stopped being valid there, not
a cosmetic cap.

Measured on the shipped presets:

| Terrain | Mean hazard | Mean σ | p95 σ |
|---|---|---|---|
| `sandy_plain` | 0.0614 | 0.0798 | 0.1008 |
| `crater_field` | 0.2532 | 0.1036 | 0.1683 |

Smooth ground is more certain than cratered ground, which is the direction the
physics requires and a useful sanity check on the derivation.

---

## Belief as a distribution

Each cell carries a mean and a standard deviation. The orbital prior is a wide
distribution centred on the orbital estimate (`prior_sigma = 0.25` by default —
deliberately wide, because that is what makes surveying worth doing); an
observation is a narrow one centred on the noisy reading; the posterior is the
standard product of two Gaussians:

```
1/σ_post² = 1/σ_prior² + 1/σ_obs²
μ_post    = σ_post² · (μ_prior/σ_prior² + z/σ_obs²)
```

Repeated observations of the same cell **tighten** the estimate rather than
overwriting it. That is what makes the second look at a cell cost the planner
nothing: `BeliefState.update` returns only the cells that actually moved, so a
settled belief produces no D* Lite repair. Without it, "incremental replanning"
would be a phrase rather than a property.

---

## Getting uncertainty into the planner

`BeliefState.planning_hazard(k)` returns `μ + k·σ`, clipped to `[0, 1]` — not
`μ`.

A planner given only the mean treats "known 0.4" and "could be anywhere between
0.1 and 0.7" as the same ground, and takes the shortcut through the unsurveyed
region every time. Charging *k* standard deviations makes uncertain ground cost
more than equally-hazardous known ground, so a longer route over surveyed terrain
can win — which is the behaviour a mission planner wants and the entire reason
the uncertainty is modelled.

`k = 0` reproduces V1 exactly, to the bit. The same knob exists on the
full-resolution hazard map as `HazardMap.planning_scores(k)` and in settings as
`uncertainty_planning_weight`.

In the flagship demo the effect is visible in the replan trace: driving the same
leg over the same terrain, the uncertainty-averse surface takes 2 reroutes where
the mean-only surface takes 12. It has already priced in the possibility that the
unsurveyed ground is bad, so discovering that it *is* bad changes its mind less
often.

---

## The sensor

Deliberately not computer vision, and not claimed to be. It is the minimum model
that makes the belief/reality distinction have consequences:

- **Range** — cells beyond `range_m` are not observed at all, so the rover drives
  into surprises rather than around them.
- **Noise** — readings are truth plus Gaussian noise, so a single look does not
  collapse the belief to certainty. A zero-noise sensor is refused, because it
  makes the Bayesian update degenerate.
- **Occlusion** — a cell is observable only if the straight line to it clears the
  intervening terrain. A rover cannot see over a ridge into the valley behind it.

Occlusion is a geometric line-of-sight test: a straight line from
`elevation[a] + sensor_height` to `elevation[b]`, tested against every supercover
cell between them. No atmospheric refraction, no Fresnel zone, no antenna
pattern. The same function serves the communications model, because "can the
rover see that cell" and "can the rover reach that relay" are the same question
asked about different endpoints.

Field of view is 360°: there is no pointing model.

---

## Monte Carlo

A single deterministic route reports the energy it costs *if the terrain is
exactly what the orbital map said and the rover consumes exactly its nominal
rate*. Neither is true, so the interesting question — will this mission finish —
has no answer in a single run.

`simulation/monte_carlo.py` resamples the uncertain quantities per trial: the
truth is perturbed away from the prior, unmapped obstacles are placed, sensor
noise is redrawn, and the per-metre energy draw is scaled by a **lognormal**
factor (multiplicative, so it cannot go negative — a normal draw at a wide enough
σ silently produces rovers that generate power by driving).

Everything is driven from one integer seed through `numpy.random.SeedSequence`,
which spawns independent child streams per trial. Two consequences the tests
assert:

- the same seed gives bit-identical results;
- **trial *k* is independent of the trial count**, so a 100-trial study and the
  first 100 trials of a 1000-trial study agree exactly, and a study can be
  extended without invalidating what came before.

Output is a distribution, and percentiles rather than means:

```
30 trials, seed 42
Mission success probability: 83.3%
Failures by mode: {'LOW_BATTERY': 5}
Energy over successful trials: P10 0.4275 / P50 0.4983 / P90 0.5777 kWh
```

P90 is the number a battery is sized against. The mean is the number that gets
missions stranded.

Percentiles are computed over *successful* trials only; the success probability
is over all of them. Mixing the two would let a study look cheap because its
expensive runs died early.
