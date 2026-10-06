"""D* Lite incremental replanning."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.planning import astar
from app.services.planning.dstar_lite import DStarLite
from app.services.planning.grid import PlanningGrid, RoverSpec

ROVER = RoverSpec(6.0, 25.0, 0.003)


def make_grid(hazard, elevation=None, **kwargs) -> PlanningGrid:
    elevation = np.zeros_like(hazard) if elevation is None else elevation
    return PlanningGrid(
        hazard.copy(), elevation, kwargs.pop("meters_per_cell", 2.0), ROVER, **kwargs
    )


def random_terrain(seed: int, size: int = 30):
    rng = np.random.default_rng(seed)
    return (rng.random((size, size)) * 0.6), (rng.random((size, size)) * 0.5)


class TestMatchesAStar:
    def test_initial_plan_equals_astar(self):
        hazard, elevation = random_terrain(4)
        planner = DStarLite(make_grid(hazard, elevation, max_hazard=0.85), (1, 1), (28, 28))
        reference = astar.search(make_grid(hazard, elevation, max_hazard=0.85), (1, 1), (28, 28))

        assert planner.cost_to_goal() == pytest.approx(reference.total_cost, rel=1e-9)
        assert planner.extract_path() == reference.cells

    @pytest.mark.parametrize("seed", [1, 2, 3, 5, 8])
    def test_repaired_plan_equals_a_fresh_astar(self, seed):
        """The load-bearing test: incremental repair must be exact, not close."""
        hazard, elevation = random_terrain(seed)
        rng = np.random.default_rng(seed + 100)

        board = make_grid(hazard, elevation, max_hazard=0.85)
        planner = DStarLite(board, (1, 1), (28, 28))

        updated = hazard.copy()
        for _ in range(6):
            row = int(rng.integers(2, 27))
            col = int(rng.integers(2, 27))
            updated[row : row + 3, col : col + 3] = 0.99
            planner.apply_updates(
                {
                    (r, c): 0.99
                    for r in range(row, min(row + 3, 30))
                    for c in range(col, min(col + 3, 30))
                }
            )

        reference = astar.search(make_grid(updated, elevation, max_hazard=0.85), (1, 1), (28, 28))
        if reference.found:
            assert planner.reachable
            assert planner.cost_to_goal() == pytest.approx(reference.total_cost, rel=1e-9)
        else:
            assert not planner.reachable

    def test_remains_exact_after_the_rover_moves(self):
        """k_m bookkeeping: keys stay comparable once the start has shifted."""
        hazard, elevation = random_terrain(11)
        board = make_grid(hazard, elevation, max_hazard=0.85)
        planner = DStarLite(board, (1, 1), (28, 28))

        for _ in range(8):
            step = planner.next_step()
            assert step is not None
            planner.move_to(step)

        planner.apply_updates({(20, c): 0.99 for c in range(0, 25)})
        updated = hazard.copy()
        updated[20, 0:25] = 0.99
        reference = astar.search(
            make_grid(updated, elevation, max_hazard=0.85), planner.start, (28, 28)
        )

        assert planner.cost_to_goal() == pytest.approx(reference.total_cost, rel=1e-9)


class TestIncrementality:
    def test_updating_with_known_values_triggers_no_repair(self):
        """ "Incremental" has to mean something: no change, no work."""
        hazard, elevation = random_terrain(7)
        planner = DStarLite(make_grid(hazard, elevation, max_hazard=0.85), (1, 1), (28, 28))

        before = planner.stats.replan_count
        assert planner.apply_updates({(5, 5): float(hazard[5, 5])}) is None
        assert planner.stats.replan_count == before

    def test_repair_is_cheaper_than_replanning_from_scratch(self):
        """The reason D* Lite exists, measured on the case it is for."""
        rng = np.random.default_rng(4)
        hazard = rng.random((60, 60)) * 0.5
        elevation = rng.random((60, 60)) * 0.5
        truth = hazard.copy()
        for _ in range(25):
            row, col = int(rng.integers(5, 55)), int(rng.integers(5, 55))
            truth[row : row + 2, col : col + 2] = 0.99

        board = make_grid(hazard, elevation, max_hazard=0.85)
        planner = DStarLite(board, (1, 1), (58, 58))

        repair_total = 0
        fresh_total = 0
        position = (1, 1)

        for _ in range(300):
            if position == (58, 58):
                break
            window = {
                (position[0] + dr, position[1] + dc): float(
                    truth[position[0] + dr, position[1] + dc]
                )
                for dr in range(-3, 4)
                for dc in range(-3, 4)
                if 0 <= position[0] + dr < 60 and 0 <= position[1] + dc < 60
            }
            record = planner.apply_updates(window)
            if record is not None:
                repair_total += record.vertices_expanded
                fresh = astar.search(
                    make_grid(planner.grid.hazard, elevation, max_hazard=0.85),
                    position,
                    (58, 58),
                )
                fresh_total += fresh.nodes_expanded

            step = planner.next_step()
            assert step is not None, "the rover became stuck, which this terrain should not do"
            planner.move_to(step)
            position = step

        assert position == (58, 58)
        assert planner.stats.replan_count > 0
        assert repair_total < fresh_total / 10, (
            f"expected an order-of-magnitude saving, got {repair_total} repair "
            f"expansions against {fresh_total} from-scratch expansions"
        )


class TestFailureHandling:
    def test_reports_unreachable_rather_than_returning_a_bad_route(self):
        hazard = np.zeros((21, 21))
        hazard[10, :] = 0.99
        planner = DStarLite(make_grid(hazard, max_hazard=0.85), (2, 5), (18, 5))

        assert not planner.reachable
        assert planner.extract_path() == []
        assert planner.next_step() is None

    def test_becomes_unreachable_when_the_last_corridor_closes(self):
        hazard = np.zeros((21, 21))
        hazard[10, :18] = 0.99  # gap on the right
        board = make_grid(hazard, max_hazard=0.85)
        planner = DStarLite(board, (2, 5), (18, 5))
        assert planner.reachable

        planner.apply_updates({(10, col): 0.99 for col in range(18, 21)})
        assert not planner.reachable
        assert planner.next_step() is None
