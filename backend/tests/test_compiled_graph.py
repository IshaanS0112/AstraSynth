"""The compiled graph must be the cost model, not an approximation of it."""

from __future__ import annotations

import math

import numpy as np
import pytest

from app.services.planning.compiled import (
    MAX_COMPILED_CELLS,
    CompiledGraph,
    GraphTooLargeError,
)
from app.services.planning.grid import NEIGHBOUR_OFFSETS, EdgeBlock, PlanningGrid, RoverSpec

ROVER = RoverSpec(6.0, 25.0, 0.003)


def adversarial_terrain(size: int = 40, seed: int = 3):
    """Hazard and relief chosen so every rejection path actually fires."""
    rng = np.random.default_rng(seed)
    hazard = rng.random((size, size)) * 0.95
    elevation = rng.random((size, size)) * 3.0
    # A lethal band with a gap, so corner-cutting diagonals exist at its ends.
    hazard[size // 2, : size - 4] = 0.99
    # A cliff, so the slope limit rejects edges the hazard layer allows.
    elevation[:, size // 3] += 40.0
    return hazard, elevation


def grid(hazard, elevation, **kwargs) -> PlanningGrid:
    return PlanningGrid(hazard, elevation, 2.0, ROVER, max_hazard=0.85, **kwargs)


def assert_matches_model(board: PlanningGrid, graph: CompiledGraph) -> None:
    rows, cols = board.shape
    for row in range(rows):
        for col in range(cols):
            index = graph.index((row, col))
            for k, (d_row, d_col) in enumerate(NEIGHBOUR_OFFSETS):
                target = (row + d_row, col + d_col)
                inside = 0 <= target[0] < rows and 0 <= target[1] < cols
                if not inside:
                    assert graph.neighbour[k][index] == -1
                    continue

                assert graph.neighbour[k][index] == graph.index(target)
                cost, reason = board.evaluate_edge((row, col), target)
                compiled = graph.cost[k][index]

                if cost is None:
                    assert math.isinf(compiled), f"{(row, col)}->{target} should be blocked"
                else:
                    assert compiled == pytest.approx(cost, rel=1e-12)
                assert graph.blocked_hazard[k][index] == (reason is EdgeBlock.LETHAL_HAZARD)
                assert graph.blocked_slope[k][index] == (reason is EdgeBlock.SLOPE)


class TestEquivalence:
    def test_every_edge_matches_the_cost_model(self):
        hazard, elevation = adversarial_terrain()
        board = grid(hazard, elevation)
        assert_matches_model(board, board.compiled())

    def test_it_matches_under_reshaped_objective_weights(self):
        """The Pareto sweep plans the same terrain at several weightings."""
        hazard, elevation = adversarial_terrain(seed=11)
        board = grid(hazard, elevation, hazard_weight=4.0, energy_weight=0.0)
        assert_matches_model(board, board.compiled())

    def test_the_terrain_really_does_exercise_every_rejection(self):
        """A conformance test over terrain that rejects nothing proves nothing."""
        hazard, elevation = adversarial_terrain()
        graph = grid(hazard, elevation).compiled()
        hazard_rejections = sum(sum(row) for row in graph.blocked_hazard)
        slope_rejections = sum(sum(row) for row in graph.blocked_slope)
        assert hazard_rejections > 0
        assert slope_rejections > 0

    def test_a_non_square_grid_is_indexed_correctly(self):
        """Row-major flattening is the one place a transpose bug would hide."""
        rng = np.random.default_rng(5)
        hazard = rng.random((23, 41)) * 0.9
        elevation = rng.random((23, 41)) * 2.0
        board = grid(hazard, elevation)
        assert_matches_model(board, board.compiled())


class TestIncrementalMaintenance:
    def test_a_sensor_update_patches_the_graph(self):
        hazard, elevation = adversarial_terrain(seed=8)
        board = grid(hazard, elevation)
        board.compiled()  # compile before the change, as a traverse would

        board.apply_hazard_update({(10, 10): 0.99, (10, 11): 0.99, (11, 10): 0.99})
        assert_matches_model(board, board.compiled())

    def test_clearing_a_hazard_reopens_the_edges(self):
        """Belief moves both ways: a cell can turn out to be safer than feared."""
        hazard, elevation = adversarial_terrain(seed=9)
        hazard[15, 15] = 0.99
        board = grid(hazard, elevation)
        board.compiled()

        board.apply_hazard_update({(15, 15): 0.05})
        assert_matches_model(board, board.compiled())

    def test_a_no_op_update_leaves_the_graph_alone(self):
        hazard, elevation = adversarial_terrain(seed=10)
        board = grid(hazard, elevation)
        graph = board.compiled()
        before = [row[:] for row in graph.cost]

        assert board.apply_hazard_update({(5, 5): float(hazard[5, 5])}) == []
        assert graph.cost == before


class TestFrameConversion:
    def test_index_and_cell_round_trip(self):
        graph = grid(*adversarial_terrain(size=17)).compiled()
        for cell in ((0, 0), (0, 16), (16, 0), (16, 16), (8, 3)):
            assert graph.cell(graph.index(cell)) == cell

    def test_heuristics_match_straight_line_distance(self):
        board = grid(*adversarial_terrain(size=20))
        graph = board.compiled()
        goal = (19, 19)
        heuristics = graph.heuristics_to(graph.index(goal))
        for cell in ((0, 0), (5, 12), (19, 0)):
            assert heuristics[graph.index(cell)] == pytest.approx(board.heuristic(cell, goal))


class TestLimits:
    def test_an_oversized_grid_is_refused_rather_than_swallowing_memory(self):
        side = int(MAX_COMPILED_CELLS**0.5) + 50
        board = PlanningGrid(
            np.zeros((side, side), dtype=np.float32),
            np.zeros((side, side), dtype=np.float32),
            2.0,
            ROVER,
        )
        with pytest.raises(GraphTooLargeError, match="compile limit"):
            board.compiled()
