"""The same mission a thousand times, with the noise resampled each run.

Why a single deterministic route is not an answer
-------------------------------------------------
V1 planned one route and reported one energy figure. That figure is the energy
the route costs *if the terrain is exactly what the orbital map said and the
rover consumes exactly its nominal rate*. Neither is true, and the interesting
question - will this mission finish - has no answer in a single run.

So the mission is run repeatedly with the uncertain quantities resampled:

* **terrain** - the truth is perturbed away from the orbital prior, so the rover
  meets ground the map did not show;
* **sensing** - observation noise is redrawn, so the rover learns a slightly
  different version of the world each run;
* **energy** - the per-metre draw is scaled by a lognormal factor, standing in
  for wheel slip, temperature and drivetrain variation.

The output is a distribution: a success probability, and percentiles rather than
means. P90 energy is the number a mission planner actually sizes a battery
against; the mean is the number that gets missions stranded.

Reproducibility
---------------
Everything is driven from one integer seed through ``numpy.random.SeedSequence``,
which spawns independent child streams per trial. Two consequences that the test
suite checks: the same seed gives bit-identical results, and trial *k* is
independent of how many trials were run - so a 100-trial study and the first 100
trials of a 1000-trial study agree exactly, and a run can be extended without
invalidating what came before.

Lognormal, not normal, for the energy factor: a multiplicative perturbation
cannot go negative, and consumption error is naturally multiplicative. A normal
draw at a wide enough sigma silently produces rovers that generate power by
driving.
"""

from __future__ import annotations

import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace

import numpy as np

from app.services.planning.grid import Cell, PlanningGrid
from app.services.simulation.belief import BeliefState
from app.services.simulation.execution import simulate_traverse
from app.services.simulation.sensors import RoverSensor

# Below this many trials the process pool costs more to start than it saves.
PARALLEL_THRESHOLD = 8


@dataclass(slots=True)
class Perturbations:
    """How far reality is allowed to differ from the plan, per trial."""

    terrain_sigma: float = 0.08  # additive hazard noise on the truth field
    obstacle_count: int = 12  # unmapped hazards revealed only by sensing
    obstacle_radius_cells: int = 2
    obstacle_hazard: float = 0.95
    energy_factor_sigma: float = 0.12  # lognormal sigma on per-metre draw
    localisation_sigma_cells: float = 0.0  # reserved; not yet consumed

    def as_dict(self) -> dict:
        return {
            "terrain_sigma": self.terrain_sigma,
            "unmapped_obstacles": self.obstacle_count,
            "obstacle_radius_cells": self.obstacle_radius_cells,
            "obstacle_hazard": self.obstacle_hazard,
            "energy_factor_sigma": self.energy_factor_sigma,
            "energy_factor_distribution": "lognormal(mu=0, sigma=energy_factor_sigma)",
        }


@dataclass(slots=True)
class TrialResult:
    index: int
    succeeded: bool
    failure: str | None
    energy_kwh: float
    distance_m: float
    elapsed_seconds: float
    replans: int
    reroutes: int
    repair_expansions: int
    energy_factor: float


@dataclass(slots=True)
class MonteCarloReport:
    trials: list[TrialResult] = field(default_factory=list)
    seed: int = 0
    perturbations: Perturbations = field(default_factory=Perturbations)
    # Reported so a study says how it was run, not because it changes anything.
    workers: int = 1

    @property
    def success_probability(self) -> float:
        if not self.trials:
            return 0.0
        return sum(1 for t in self.trials if t.succeeded) / len(self.trials)

    def percentiles(self, attribute: str, successes_only: bool = True) -> dict[str, float]:
        source = [t for t in self.trials if t.succeeded] if successes_only else self.trials
        values = [getattr(t, attribute) for t in source]
        if not values:
            return {}
        array = np.asarray(values, dtype=np.float64)
        return {
            "p10": round(float(np.percentile(array, 10)), 6),
            "p50": round(float(np.percentile(array, 50)), 6),
            "p90": round(float(np.percentile(array, 90)), 6),
            "mean": round(float(array.mean()), 6),
            "min": round(float(array.min()), 6),
            "max": round(float(array.max()), 6),
        }

    def failure_histogram(self) -> dict[str, int]:
        histogram: dict[str, int] = {}
        for trial in self.trials:
            if trial.succeeded:
                continue
            histogram[trial.failure or "UNKNOWN"] = histogram.get(trial.failure or "UNKNOWN", 0) + 1
        return dict(sorted(histogram.items(), key=lambda kv: -kv[1]))

    def as_dict(self) -> dict:
        return {
            "trials": len(self.trials),
            "seed": self.seed,
            "workers": self.workers,
            "success_probability": round(self.success_probability, 4),
            "failures_by_mode": self.failure_histogram(),
            "energy_kwh": self.percentiles("energy_kwh"),
            "elapsed_seconds": self.percentiles("elapsed_seconds"),
            "distance_m": self.percentiles("distance_m"),
            "replans": self.percentiles("replans"),
            "reroutes": self.percentiles("reroutes"),
            "perturbations": self.perturbations.as_dict(),
            "note": (
                "percentiles are over successful trials only; the success "
                "probability is over all trials"
            ),
        }


def _sample_truth(
    orbital: np.ndarray, perturbations: Perturbations, rng: np.random.Generator
) -> np.ndarray:
    """One realisation of reality, given the orbital map as the prior mean."""
    truth = np.clip(orbital + rng.normal(0.0, perturbations.terrain_sigma, orbital.shape), 0.0, 1.0)
    rows, cols = orbital.shape
    radius = perturbations.obstacle_radius_cells
    for _ in range(perturbations.obstacle_count):
        row = int(rng.integers(radius, max(radius + 1, rows - radius)))
        col = int(rng.integers(radius, max(radius + 1, cols - radius)))
        truth[max(0, row - radius) : row + radius + 1, max(0, col - radius) : col + radius + 1] = (
            perturbations.obstacle_hazard
        )
    return truth


# --- one trial --------------------------------------------------------------
#
# Module-level, and taking only picklable primitives, because this is what runs
# inside a worker process. Anything captured from an enclosing scope would have
# to survive pickling, and a closure over a PlanningGrid would ship the compiled
# graph to every worker on every trial.

_WORKER_CONTEXT: dict = {}


def _init_worker(context: dict) -> None:
    global _WORKER_CONTEXT
    _WORKER_CONTEXT = context


def _execute_trial(context: dict, index: int, stream: np.random.SeedSequence) -> TrialResult:
    """One realisation of the mission. Pure given ``(context, index, stream)``.

    That purity is the load-bearing property: the seed sequence is spawned per
    trial in the parent, so which worker runs trial *k* - or whether any worker
    does - cannot change its result. ``test_monte_carlo`` asserts that serial
    and parallel runs agree exactly.
    """
    settings: Perturbations = context["perturbations"]
    orbital: np.ndarray = context["orbital"]
    elevation: np.ndarray = context["elevation"]

    rng = np.random.default_rng(stream)
    truth = _sample_truth(orbital, settings, rng)
    energy_factor = float(rng.lognormal(0.0, settings.energy_factor_sigma))

    trial_rover = replace(
        context["rover"],
        energy_per_meter_kwh=context["rover"].energy_per_meter_kwh * energy_factor,
    )
    template = PlanningGrid(
        hazard=orbital.copy(),
        elevation=elevation,
        meters_per_cell=context["meters_per_cell"],
        rover=trial_rover,
        slope_coefficient=context["slope_coefficient"],
        max_hazard=context["max_hazard"],
    )
    belief = BeliefState.from_prior(orbital, prior_sigma=context["prior_sigma"])

    result = simulate_traverse(
        truth_hazard=truth,
        start=context["start"],
        goal=context["goal"],
        belief=belief,
        sensor=context["sensor"],
        grid_template=template,
        rng=rng,
        uncertainty_weight=context["uncertainty_weight"],
    )

    return TrialResult(
        index=index,
        succeeded=result.succeeded,
        failure=result.outcome.failure.value if result.outcome.failure else None,
        energy_kwh=result.energy_kwh,
        distance_m=result.distance_m,
        elapsed_seconds=result.elapsed_seconds,
        replans=result.replans,
        reroutes=result.reroutes,
        repair_expansions=result.repair_expansions,
        energy_factor=energy_factor,
    )


def _worker_trial(job: tuple[int, np.random.SeedSequence]) -> TrialResult:
    index, stream = job
    return _execute_trial(_WORKER_CONTEXT, index, stream)


def _pool_context():
    """A start method that is safe to use from inside a web server.

    ``fork`` is fast but copies a process that may hold locks in other threads -
    and this runs on FastAPI's request threadpool, which is exactly that
    situation. ``forkserver`` forks from a clean single-threaded helper instead,
    so it is safe without paying ``spawn``'s full interpreter restart on every
    worker. Where forkserver is unavailable (macOS ships it, Windows does not)
    ``spawn`` is the correct fallback; ``fork`` deliberately is not.
    """
    available = multiprocessing.get_all_start_methods()
    for method in ("forkserver", "spawn"):
        if method in available:
            return multiprocessing.get_context(method)
    return None


def resolve_workers(trials: int, workers: int | None) -> int:
    """How many processes to use. ``1`` means run in this process."""
    if workers is not None:
        return max(1, min(workers, trials))
    if trials < PARALLEL_THRESHOLD:
        return 1
    return max(1, min(os.cpu_count() or 1, trials))


def run(
    orbital_hazard: np.ndarray,
    elevation: np.ndarray,
    start: Cell,
    goal: Cell,
    grid_template: PlanningGrid,
    sensor: RoverSensor,
    trials: int = 100,
    seed: int = 0,
    perturbations: Perturbations | None = None,
    uncertainty_weight: float = 0.0,
    prior_sigma: float = 0.25,
    workers: int | None = None,
) -> MonteCarloReport:
    """Run the mission ``trials`` times and aggregate.

    Trials are independent by construction, so they are distributed across
    processes when there are enough of them to be worth the pool. ``workers=1``
    forces the serial path; ``None`` picks a sensible number from the trial count
    and the core count. **The result does not depend on which is used** - the
    per-trial seed streams are spawned in the parent before any work is handed
    out, so parallelism changes the wall time and nothing else.

    ``grid_template`` is never mutated: every trial builds its own grid from the
    template's rover and thresholds, so a trial that drives the rover into a
    boulder field cannot contaminate the next one.
    """
    if trials <= 0:
        raise ValueError("trials must be positive")
    settings = perturbations or Perturbations()
    orbital = np.asarray(orbital_hazard, dtype=np.float64)

    context = {
        "orbital": orbital,
        "elevation": np.asarray(elevation, dtype=np.float64),
        "start": start,
        "goal": goal,
        # A copy, so a worker cannot mutate the caller's spec through the pickle.
        "rover": replace(grid_template.rover),
        "meters_per_cell": grid_template.meters_per_cell,
        "slope_coefficient": grid_template.slope_coefficient,
        "max_hazard": grid_template.max_hazard,
        "sensor": sensor,
        "perturbations": settings,
        "uncertainty_weight": uncertainty_weight,
        "prior_sigma": prior_sigma,
    }

    # One child stream per trial, spawned here: trial k is independent of the
    # trial count *and* of the worker count.
    streams = np.random.SeedSequence(seed).spawn(trials)
    report = MonteCarloReport(seed=seed, perturbations=settings)

    worker_count = resolve_workers(trials, workers)
    pool_context = _pool_context() if worker_count > 1 else None

    if worker_count <= 1 or pool_context is None:
        report.trials.extend(
            _execute_trial(context, index, stream) for index, stream in enumerate(streams)
        )
        report.workers = 1
        return report

    jobs = list(enumerate(streams))
    chunk = max(1, len(jobs) // (worker_count * 4))
    with ProcessPoolExecutor(
        max_workers=worker_count,
        mp_context=pool_context,
        initializer=_init_worker,
        initargs=(context,),
    ) as pool:
        report.trials.extend(pool.map(_worker_trial, jobs, chunksize=chunk))
    report.workers = worker_count
    return report
