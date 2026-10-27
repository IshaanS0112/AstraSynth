"""The layered costmap: Nav2's inflation, a rover footprint, and step obstacles."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.planning import astar
from app.services.planning.costmap import (
    NAV2_BAND_PEAK_FRACTION,
    FootprintParams,
    compose,
    distance_to_obstacles_m,
    inflation_cost,
)
from app.services.planning.dstar_lite import DStarLite
from app.services.planning.grid import PlanningGrid, RoverSpec
from tests.test_compiled_graph import adversarial_terrain, assert_matches_model

MAX_HAZARD = 0.85
CELL_M = 2.0


def rover(**footprint) -> RoverSpec:
    return RoverSpec(6.0, 25.0, 0.003, **footprint)


def board(hazard, elevation, subcell_obstacles=None, **footprint) -> PlanningGrid:
    return PlanningGrid(
        hazard,
        elevation,
        CELL_M,
        rover(**footprint),
        max_hazard=MAX_HAZARD,
        subcell_obstacles=subcell_obstacles,
    )


def single_obstacle(size: int = 21):
    hazard = np.zeros((size, size))
    hazard[size // 2, size // 2] = 1.0
    return hazard, np.zeros((size, size))


class TestAPointRoverIsUnchanged:
    def test_the_costmap_is_the_hazard_array_itself(self):
        """Not equal to it: the same object. That is the bit-identical guarantee."""
        hazard, elevation = adversarial_terrain()
        grid = board(hazard, elevation)
        assert grid.costmap is grid.hazard
        assert grid.layers is None

    def test_an_explicit_zero_footprint_is_still_a_point_rover(self):
        hazard, elevation = adversarial_terrain()
        grid = board(hazard, elevation, footprint_radius_m=0.0, clearance_radius_m=0.0)
        assert grid.costmap is grid.hazard


class TestTheInflationCurve:
    def test_it_matches_nav2_compute_cost(self):
        params = FootprintParams(inscribed_radius_m=1.0, inflation_radius_m=10.0)
        k = params.decay_per_m()
        distances = np.array([0.0, 0.5, 1.0, 1.5, 4.0, 9.9, 10.0, 10.1, 50.0])
        got = inflation_cost(distances, params, MAX_HAZARD)

        peak = MAX_HAZARD * NAV2_BAND_PEAK_FRACTION
        for d, value in zip(distances, got, strict=True):
            if d <= 1.0:
                assert value == MAX_HAZARD, f"d={d} is inside the footprint"
            elif d <= 10.0:
                assert value == pytest.approx(peak * math.exp(-k * (d - 1.0)))
            else:
                assert value == 0.0, f"d={d} is past the clearance radius"

    def test_it_never_rises_with_distance(self):
        params = FootprintParams(inscribed_radius_m=2.0, inflation_radius_m=20.0)
        distances = np.linspace(0.0, 25.0, 501)
        values = inflation_cost(distances, params, MAX_HAZARD)
        assert np.all(np.diff(values) <= 1e-12)

    def test_the_band_stays_below_lethal(self):
        """Inflation shapes a route; it never on its own makes ground undrivable."""
        params = FootprintParams(inscribed_radius_m=0.0, inflation_radius_m=30.0)
        values = inflation_cost(np.linspace(0.01, 30.0, 300), params, MAX_HAZARD)
        assert values.max() < MAX_HAZARD

    def test_the_default_decay_is_scaled_to_the_band(self):
        """Nav2's per-metre default would vanish in one planetary cell."""
        params = FootprintParams(inscribed_radius_m=1.0, inflation_radius_m=16.0)
        assert params.decay_per_m() == pytest.approx(3.0 / 15.0)
        explicit = FootprintParams(inflation_radius_m=16.0, cost_scaling_factor=0.7)
        assert explicit.decay_per_m() == 0.7

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"inscribed_radius_m": -1.0},
            {"inscribed_radius_m": 5.0, "inflation_radius_m": 2.0},
            {"inflation_radius_m": 5.0, "cost_scaling_factor": 0.0},
        ],
    )
    def test_nonsense_parameters_are_refused(self, kwargs):
        with pytest.raises(ValueError):
            FootprintParams(**kwargs)


class TestComposition:
    def test_distance_is_exact_euclidean_in_metres(self):
        hazard, _ = single_obstacle(size=11)
        obstacle = hazard >= MAX_HAZARD
        distance = distance_to_obstacles_m(obstacle, CELL_M)
        assert distance[5, 5] == 0.0
        assert distance[5, 8] == pytest.approx(3 * CELL_M)
        assert distance[8, 9] == pytest.approx(5 * CELL_M)  # a 3-4-5 triangle

    def test_no_obstacles_means_no_inflation(self):
        hazard = np.full((9, 9), 0.2)
        layers = compose(hazard, MAX_HAZARD, CELL_M, FootprintParams(1.0, 10.0))
        assert np.isinf(layers.distance_m).all()
        assert np.array_equal(layers.master, hazard)

    def test_no_layer_ever_lowers_a_cost(self):
        """Combination is by maximum, as Nav2's updateWithMax."""
        hazard, _ = adversarial_terrain()
        layers = compose(hazard, MAX_HAZARD, CELL_M, FootprintParams(2.5, 12.0))
        assert np.all(layers.master >= hazard)

    def test_the_footprint_makes_the_neighbourhood_lethal(self):
        hazard, _ = single_obstacle()
        layers = compose(hazard, MAX_HAZARD, CELL_M, FootprintParams(CELL_M * 1.5, 0.0))
        lethal = layers.lethal
        assert lethal[10, 10]
        assert lethal[10, 11] and lethal[11, 11], "within 1.5 cells of the obstacle"
        assert not lethal[10, 12], "two cells away is outside the footprint"

    def test_a_subcell_obstacle_is_an_obstacle_whatever_the_mean(self):
        hazard = np.full((9, 9), 0.2)
        hidden = np.zeros((9, 9), dtype=bool)
        hidden[4, 4] = True
        layers = compose(hazard, MAX_HAZARD, CELL_M, FootprintParams(), hidden)
        assert layers.obstacle[4, 4]
        assert layers.lethal[4, 4]
        assert layers.obstacle.sum() == 1
        assert layers.master[4, 4] >= MAX_HAZARD
        assert hazard[4, 4] < MAX_HAZARD, "the averaged layer alone would have missed it"


class TestSubcellObstacles:
    """grid_map's lesson: each layer takes the reduction that fits it."""

    def test_an_empty_mask_keeps_a_point_rover_bit_identical(self):
        hazard, elevation = adversarial_terrain()
        grid = board(hazard, elevation, np.zeros(hazard.shape, dtype=bool))
        assert grid.costmap is grid.hazard

    def test_a_ridge_averaged_away_still_blocks_the_route(self):
        """The defect this layer exists for, end to end through the real downsampler."""
        from app.services.hazard_mapper import downsample_for_planning, max_pool_for_planning

        full = np.full((512, 512), 0.10, dtype=np.float32)
        full[:, 250] = 0.95  # one pixel wide, the whole height of the tile
        mean, _ = downsample_for_planning(full, 192)
        hidden = max_pool_for_planning(full >= MAX_HAZARD, 192)
        assert not (mean >= MAX_HAZARD).any(), "averaging erases the ridge entirely"

        elevation = np.zeros(mean.shape)
        start, goal = (96, 20), (96, 170)

        blind = astar.search(board(mean, elevation), start, goal)
        assert blind.found, "without the layer the planner drives straight across"

        seeing = astar.search(board(mean, elevation, hidden), start, goal)
        assert not seeing.found, "with it, a wall the full height of the map is a wall"

    def test_the_max_sees_every_pixel_the_mean_weighed(self):
        """At 512 -> 192 a boundary pixel counts towards two cells; both must see it.

        Disjoint pooling windows give each such pixel to one cell only, so the
        other cell's mean includes a lethal pixel its max never looked at - 285
        of 766 cells on this mask.
        """
        from app.services.hazard_mapper import downsample_for_planning, max_pool_for_planning

        lethal = np.random.default_rng(3).random((512, 512)) > 0.998
        mean, _ = downsample_for_planning(lethal.astype(np.float32), 192)
        pooled = max_pool_for_planning(lethal, 192)
        assert pooled.shape == mean.shape
        assert pooled[mean > 1e-6].all()

    def test_the_mask_must_match_the_grid(self):
        hazard, elevation = adversarial_terrain(size=10)
        with pytest.raises(ValueError):
            board(hazard, elevation, np.zeros((3, 3), dtype=bool))


class TestPlanningWithAFootprint:
    def test_no_route_enters_the_footprint_of_an_obstacle(self):
        hazard, elevation = adversarial_terrain(size=40, seed=5)
        footprint = CELL_M * 1.2
        grid = board(hazard, elevation, footprint_radius_m=footprint)
        result = astar.search(grid, (2, 2), (37, 37))
        distance = grid.layers.distance_m
        for cell in result.cells[1:]:
            assert distance[cell] > footprint, f"{cell} is {distance[cell]:.2f} m from an obstacle"

    def test_clearance_moves_the_route_away_from_hazards(self):
        """A wall with a gap: the point rover skims the wall, the inflated one keeps off it."""
        size = 31
        hazard = np.zeros((size, size))
        hazard[15, :] = 1.0
        hazard[15, 13:18] = 0.0  # a five-cell gap
        hazard[:, 20] = 1.0
        hazard[10:31, 20] = 0.0
        elevation = np.zeros((size, size))
        start, goal = (2, 15), (28, 15)

        point = astar.search(board(hazard, elevation), start, goal)
        careful_grid = board(hazard, elevation, clearance_radius_m=CELL_M * 4)
        careful = astar.search(careful_grid, start, goal)

        distance = careful_grid.layers.distance_m

        def mean_clearance(cells):
            return float(np.mean([distance[c] for c in cells]))

        assert mean_clearance(careful.cells) >= mean_clearance(point.cells)
        assert min(distance[c] for c in careful.cells) >= min(distance[c] for c in point.cells)

    def test_the_route_is_never_cheaper_with_a_footprint(self):
        """The costmap only adds cost, so the optimum can only rise or hold."""
        hazard, elevation = adversarial_terrain(size=30, seed=12)
        point = astar.search(board(hazard, elevation), (1, 1), (28, 28))
        careful = astar.search(board(hazard, elevation, clearance_radius_m=10.0), (1, 1), (28, 28))
        assert careful.total_cost >= point.total_cost - 1e-9

    def test_the_audit_trail_records_the_costmap(self):
        hazard, elevation = adversarial_terrain(size=20)
        grid = board(hazard, elevation, footprint_radius_m=1.5, clearance_radius_m=9.0)
        described = grid.cost_model_description()["costmap"]
        assert described["layers"] == ["hazard", "obstacle", "inflation"]
        assert described["inscribed_radius_m"] == 1.5
        assert described["inflation_radius_m"] == 9.0
        assert described["lethal_cells"] >= int((hazard >= MAX_HAZARD).sum())

    def test_reported_hazard_is_still_the_terrain(self):
        """Clearance is a planning preference, not a property of the ground."""
        hazard, elevation = single_obstacle()
        grid = board(hazard, elevation, clearance_radius_m=10.0)
        assert grid.hazard[10, 12] == 0.0
        assert grid.costmap[10, 12] > 0.0


class TestIncrementalReinflation:
    def test_a_new_obstacle_reinflates_its_neighbourhood(self):
        hazard, elevation = adversarial_terrain(seed=8)
        grid = board(hazard, elevation, clearance_radius_m=8.0)
        grid.compiled()

        changed = grid.apply_hazard_update({(10, 10): 0.99})
        assert (10, 10) in changed
        assert len(changed) > 1, "the inflation band around a new obstacle moves too"
        assert_matches_model(grid, grid.compiled())

    def test_the_patched_graph_equals_a_fresh_one(self):
        hazard, elevation = adversarial_terrain(seed=21)
        live = board(hazard.copy(), elevation, footprint_radius_m=2.5, clearance_radius_m=9.0)
        live.compiled()
        live.apply_hazard_update({(7, 7): 0.99, (20, 25): 0.99, (30, 5): 0.0})

        fresh = board(live.hazard.copy(), elevation, footprint_radius_m=2.5, clearance_radius_m=9.0)
        assert np.array_equal(live.costmap, fresh.costmap)
        assert live.compiled().cost == fresh.compiled().cost

    def test_dstar_lite_still_matches_a_fresh_search(self):
        """The incremental planner's whole claim, now with re-inflation underneath."""
        rng = np.random.default_rng(17)
        size = 32
        hazard = rng.random((size, size)) * 0.6  # drivable everywhere to begin with
        hazard[8, 4:24] = 1.0  # one wall to route around
        elevation = rng.random((size, size)) * 0.5
        start, goal = (1, 1), (30, 30)
        params = {"footprint_radius_m": 2.0, "clearance_radius_m": 8.0}

        live = board(hazard.copy(), elevation, **params)
        planner = DStarLite(live, start, goal)
        assert planner.reachable

        # Obstacles discovered right across the current best route.
        route = planner.extract_path()
        blocked = {route[len(route) // 2]: 1.0, route[len(route) // 2 + 1]: 1.0}
        planner.apply_updates(blocked)

        fresh = astar.search(board(live.hazard.copy(), elevation, **params), start, goal)
        assert fresh.found == planner.reachable
        if fresh.found:
            assert planner.cost_to_goal() == pytest.approx(fresh.total_cost, rel=1e-9)

    def test_an_unchanged_reading_moves_nothing(self):
        hazard, elevation = adversarial_terrain(seed=4)
        grid = board(hazard, elevation, clearance_radius_m=8.0)
        assert grid.apply_hazard_update({(3, 3): float(hazard[3, 3])}) == []
