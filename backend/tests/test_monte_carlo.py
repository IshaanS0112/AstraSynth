"""Monte Carlo mission robustness.

A stochastic study whose numbers cannot be reproduced is an anecdote. The seed
discipline is therefore tested harder than the statistics: identical seeds must
give identical results, and trial *k* must be independent of how many trials
were requested, so a study can be extended without invalidating what came before.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.simulation import monte_carlo
from app.services.simulation.sensors import RoverSensor


def setup(capacity: float = 0.6):
    orbital = np.random.default_rng(1).random((40, 40)) * 0.35
    elevation = np.random.default_rng(2).random((40, 40)) * 0.4
    template = PlanningGrid(
        orbital.copy(), elevation, 3.0, RoverSpec(capacity, 25.0, 0.003), max_hazard=0.85
    )
    return orbital, elevation, template


def run(trials: int, seed: int, capacity: float = 0.6, workers: int | None = 1, **kwargs):
    orbital, elevation, template = setup(capacity)
    return monte_carlo.run(
        orbital_hazard=orbital,
        elevation=elevation,
        start=(1, 1),
        goal=(38, 38),
        grid_template=template,
        sensor=RoverSensor(range_m=10.0),
        trials=trials,
        seed=seed,
        workers=workers,
        **kwargs,
    )


class TestReproducibility:
    def test_the_same_seed_gives_identical_results(self):
        assert run(12, seed=42).as_dict() == run(12, seed=42).as_dict()

    def test_a_different_seed_gives_different_results(self):
        assert run(12, seed=42).as_dict() != run(12, seed=43).as_dict()

    def test_trial_k_does_not_depend_on_the_trial_count(self):
        """Extending a study must not renumber the trials already run."""
        short = run(6, seed=7)
        long = run(24, seed=7)
        assert [t.energy_kwh for t in short.trials] == [t.energy_kwh for t in long.trials[:6]]
        assert [t.succeeded for t in short.trials] == [t.succeeded for t in long.trials[:6]]


class TestParallelism:
    """Distributing the trials must change the wall time and nothing else."""

    def test_parallel_and_serial_agree_exactly(self):
        serial = run(12, seed=7, workers=1)
        parallel = run(12, seed=7, workers=4)

        assert serial.workers == 1
        assert parallel.workers == 4
        assert [t.energy_kwh for t in serial.trials] == [t.energy_kwh for t in parallel.trials]
        assert [t.succeeded for t in serial.trials] == [t.succeeded for t in parallel.trials]
        assert serial.success_probability == parallel.success_probability

    def test_the_worker_count_does_not_reorder_the_trials(self):
        """pool.map preserves order; a switch to as_completed would not."""
        for workers in (1, 2, 3):
            report = run(9, seed=5, workers=workers)
            assert [t.index for t in report.trials] == list(range(9))

    def test_small_studies_stay_in_process(self):
        """A pool costs more to start than four trials cost to run."""
        assert monte_carlo.resolve_workers(4, None) == 1
        assert monte_carlo.resolve_workers(64, None) > 1

    def test_workers_never_exceeds_the_trial_count(self):
        assert monte_carlo.resolve_workers(3, 16) == 3


class TestStatistics:
    def test_a_tight_battery_produces_a_mix_of_outcomes(self):
        report = run(30, seed=42, capacity=0.6)
        assert 0.0 < report.success_probability < 1.0
        assert report.failure_histogram()["LOW_BATTERY"] > 0

    def test_a_generous_battery_succeeds_every_time(self):
        report = run(20, seed=42, capacity=3.0)
        assert report.success_probability == 1.0
        assert report.failure_histogram() == {}

    def test_percentiles_are_ordered_and_cover_successes_only(self):
        report = run(30, seed=42, capacity=0.6)
        energy = report.percentiles("energy_kwh")
        assert energy["min"] <= energy["p10"] <= energy["p50"] <= energy["p90"] <= energy["max"]
        successes = [t for t in report.trials if t.succeeded]
        assert len(successes) < len(report.trials)
        # percentiles round to 6 dp, so compare at that scale rather than exactly.
        assert energy["max"] == pytest.approx(max(t.energy_kwh for t in successes), abs=1e-6)

    def test_percentiles_are_empty_when_nothing_succeeded(self):
        report = run(6, seed=42, capacity=0.02)
        assert report.success_probability == 0.0
        assert report.percentiles("energy_kwh") == {}

    def test_the_energy_factor_is_multiplicative_and_positive(self):
        """A normal draw would eventually produce a rover that generates power."""
        report = run(30, seed=42)
        factors = [t.energy_factor for t in report.trials]
        assert all(f > 0 for f in factors)
        assert np.median(factors) == pytest.approx(1.0, abs=0.15)


class TestInputHandling:
    def test_zero_trials_is_refused(self):
        with pytest.raises(ValueError, match="positive"):
            run(0, seed=1)
