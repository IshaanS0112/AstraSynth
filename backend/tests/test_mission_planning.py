"""Mission-level planning: communications, science selection, scheduling.

These are the layers above route planning - the ones that decide *what* the
rover should do rather than how it gets there.
"""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from app.services.mission.comms import CommunicationPlan, OrbiterPass, RelayStation
from app.services.mission.scheduling import Activity, ActivityKind, Timeline
from app.services.mission.science import ScienceTarget, plan_science_tour
from app.services.planning.grid import PlanningGrid, RoverSpec
from app.services.planning.outcome import FailureMode

ROVER = RoverSpec(6.0, 25.0, 0.003)


def board(hazard, elevation=None, meters_per_cell=4.0, rover=ROVER) -> PlanningGrid:
    elevation = np.zeros_like(hazard) if elevation is None else elevation
    return PlanningGrid(hazard, elevation, meters_per_cell, rover, max_hazard=0.85)


def ridged_terrain(size: int = 40):
    """Flat ground split by a low ridge: crossable, but it blocks line of sight."""
    elevation = np.zeros((size, size))
    for offset, height in enumerate((3.0, 6.0, 6.0, 3.0)):
        elevation[18 + offset, :] = height
    return np.zeros((size, size)), elevation


class TestGroundRelay:
    def test_a_ridge_casts_a_radio_shadow(self):
        hazard, elevation = ridged_terrain()
        grid = board(hazard, elevation, rover=RoverSpec(6.0, 45.0, 0.003))
        plan = CommunicationPlan(stations=[RelayStation("LANDER", (2, 20), antenna_height_m=2.0)])
        coverage = plan.coverage_mask(grid)

        assert coverage[:16].mean() == pytest.approx(1.0)
        assert coverage[25:].mean() == pytest.approx(0.0)

    def test_a_taller_antenna_sees_over_the_ridge(self):
        hazard, elevation = ridged_terrain()
        grid = board(hazard, elevation, rover=RoverSpec(6.0, 45.0, 0.003))
        tall = CommunicationPlan(stations=[RelayStation("TOWER", (2, 20), antenna_height_m=200.0)])
        assert tall.coverage_mask(grid, rover_antenna_m=50.0)[30:].mean() > 0.5

    def test_range_limits_coverage_independently_of_geometry(self):
        grid = board(np.zeros((40, 40)))
        plan = CommunicationPlan(stations=[RelayStation("SHORT", (20, 20), max_range_m=20.0)])
        coverage = plan.coverage_mask(grid)
        assert coverage[20, 20]
        assert not coverage[0, 0]


class TestOrbiter:
    def test_windows_repeat_on_the_stated_period(self):
        orbiter = OrbiterPass("MRO", period_seconds=3600.0, duration_seconds=600.0)
        assert orbiter.available_at(0.0)
        assert orbiter.available_at(599.0)
        assert not orbiter.available_at(601.0)
        assert orbiter.available_at(3600.0)
        assert orbiter.available_at(7200.0 + 100.0)

    def test_next_window_lands_on_the_following_pass(self):
        orbiter = OrbiterPass("MRO", period_seconds=3600.0, duration_seconds=600.0)
        start, end = orbiter.next_window(1000.0)
        assert (start, end) == (3600.0, 4200.0)

    def test_a_pass_longer_than_its_period_is_refused(self):
        with pytest.raises(ValueError, match="longer than"):
            OrbiterPass("BAD", period_seconds=600.0, duration_seconds=900.0)


class TestRouteEvaluation:
    def test_a_route_into_the_shadow_reports_a_blackout(self):
        hazard, elevation = ridged_terrain()
        grid = board(hazard, elevation, rover=RoverSpec(6.0, 45.0, 0.003))
        plan = CommunicationPlan(stations=[RelayStation("LANDER", (2, 20), antenna_height_m=2.0)])
        route = [(r, 20) for r in range(2, 39)]
        stats = plan.evaluate_route(grid, route)

        assert 0.0 < stats["blackout_fraction"] < 1.0
        assert stats["longest_blackout_seconds"] > 0.0
        assert stats["cells_with_link"] < stats["cells_total"]

    def test_an_orbiter_fills_the_gap_a_relay_cannot(self):
        hazard, elevation = ridged_terrain()
        grid = board(hazard, elevation, rover=RoverSpec(6.0, 45.0, 0.003))
        route = [(r, 20) for r in range(2, 39)]

        relay_only = CommunicationPlan(
            stations=[RelayStation("LANDER", (2, 20), antenna_height_m=2.0)]
        )
        with_orbiter = CommunicationPlan(
            stations=[RelayStation("LANDER", (2, 20), antenna_height_m=2.0)],
            orbiters=[OrbiterPass("MRO", period_seconds=600.0, duration_seconds=300.0)],
        )

        assert (
            with_orbiter.evaluate_route(grid, route)["blackout_fraction"]
            < relay_only.evaluate_route(grid, route)["blackout_fraction"]
        )

    def test_full_coverage_reports_no_blackout(self):
        grid = board(np.zeros((20, 20)))
        plan = CommunicationPlan(stations=[RelayStation("LANDER", (10, 10))])
        stats = plan.evaluate_route(grid, [(10, c) for c in range(20)])
        assert stats["blackout_fraction"] == 0.0
        assert stats["linked_cell_fraction"] == 1.0


class TestScienceTour:
    def scenario(self):
        rng = np.random.default_rng(3)
        grid = board(rng.random((40, 40)) * 0.5)
        targets = [
            ScienceTarget("A", (5, 30), 0.91, required_instrument="spectrometer"),
            ScienceTarget("B", (30, 8), 0.65),
            ScienceTarget("C", (35, 35), 0.80),
            ScienceTarget("D", (20, 20), 0.40),
            ScienceTarget("E", (2, 38), 0.55, required_instrument="drill"),
        ]
        return grid, targets

    def test_targets_needing_an_absent_instrument_are_skipped_with_a_reason(self):
        grid, targets = self.scenario()
        outcome = plan_science_tour(
            grid,
            (1, 1),
            targets,
            energy_budget_kwh=1.2,
            instruments=frozenset({"spectrometer"}),
        )
        tour = outcome.diagnostics["tour"]

        assert "E" not in {t.target_id for t in tour.visited}
        skipped = {s["target_id"]: s["reason"] for s in tour.skipped}
        assert skipped["E"] == "instrument_not_carried"
        assert "A" in {t.target_id for t in tour.visited}

    def test_the_tour_stays_inside_the_energy_budget(self):
        grid, targets = self.scenario()
        outcome = plan_science_tour(
            grid,
            (1, 1),
            targets,
            energy_budget_kwh=1.2,
            instruments=frozenset({"spectrometer"}),
        )
        tour = outcome.diagnostics["tour"]
        assert tour.energy_kwh <= tour.energy_budget_kwh

    def test_a_tighter_budget_collects_less_science(self):
        grid, targets = self.scenario()
        instruments = frozenset({"spectrometer"})
        generous = plan_science_tour(
            grid, (1, 1), targets, energy_budget_kwh=2.0, instruments=instruments
        ).diagnostics["tour"]
        tight = plan_science_tour(
            grid, (1, 1), targets, energy_budget_kwh=0.5, instruments=instruments
        ).diagnostics["tour"]

        assert tight.science_value < generous.science_value
        assert len(tight.visited) < len(generous.visited)
        assert any(s["reason"] == "budget_exhausted" for s in tight.skipped)

    def test_legs_chain_start_to_finish_without_gaps(self):
        grid, targets = self.scenario()
        tour = plan_science_tour(
            grid,
            (1, 1),
            targets,
            energy_budget_kwh=2.0,
            instruments=frozenset({"spectrometer"}),
        ).diagnostics["tour"]

        assert tour.legs[0].cells[0] == (1, 1)
        for earlier, later in itertools.pairwise(tour.legs):
            assert earlier.cells[-1] == later.cells[0]
        assert tour.cells[0] == (1, 1)
        assert tour.cells[-1] == tour.visited[-1].cell

    def test_a_target_on_lethal_ground_is_refused(self):
        hazard = np.zeros((20, 20))
        hazard[10, 10] = 0.99
        grid = board(hazard)
        outcome = plan_science_tour(
            grid, (1, 1), [ScienceTarget("X", (10, 10), 0.9), ScienceTarget("Y", (5, 5), 0.5)]
        )
        tour = outcome.diagnostics["tour"]
        assert {s["target_id"] for s in tour.skipped} == {"X"}
        assert [t.target_id for t in tour.visited] == ["Y"]

    def test_a_budget_that_fits_nothing_reports_low_battery(self):
        grid, targets = self.scenario()
        outcome = plan_science_tour(
            grid,
            (1, 1),
            targets,
            energy_budget_kwh=1e-6,
            instruments=frozenset({"spectrometer"}),
        )
        assert not outcome.succeeded
        assert outcome.failure is FailureMode.LOW_BATTERY

    def test_no_targets_at_all_is_a_programming_error_not_a_mission_outcome(self):
        grid, _ = self.scenario()
        with pytest.raises(ValueError, match="at least one"):
            plan_science_tour(grid, (1, 1), [])


class TestTimeline:
    def test_activities_partition_mission_time(self):
        timeline = Timeline()
        timeline.add(Activity(ActivityKind.TRAVEL, 0.0, 600.0, "to A"))
        timeline.add(Activity(ActivityKind.SCIENCE, 600.0, 1200.0, "observe A"))
        timeline.add(Activity(ActivityKind.COMMUNICATE, 1800.0, 300.0, "MRO pass"))

        assert timeline.end_seconds == 2100.0
        assert timeline.duration_by_kind()["TRAVEL"] == 600.0
        assert timeline.duration_by_kind()["SCIENCE"] == 1200.0

    def test_overlapping_activities_are_refused(self):
        timeline = Timeline()
        timeline.add(Activity(ActivityKind.TRAVEL, 0.0, 600.0, "to A"))
        with pytest.raises(ValueError, match="before the previous one ends"):
            timeline.add(Activity(ActivityKind.SCIENCE, 300.0, 600.0, "observe A"))

    def test_sol_boundaries_are_computed_from_the_martian_day(self):
        timeline = Timeline()
        assert timeline.sol_of(0.0) == 0
        assert timeline.sol_of(88774.0) == 0
        assert timeline.sol_of(88776.0) == 1
