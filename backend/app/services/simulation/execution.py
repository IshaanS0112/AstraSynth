"""Driving a planned route against terrain the rover cannot fully see."""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.planning.grid import Cell, PlanningGrid
from app.services.planning.outcome import FailureMode, PlanningOutcome
from app.services.simulation.behavior_tree import Blackboard, Status
from app.services.simulation.behavior_tree import run as run_tree
from app.services.simulation.belief import BeliefState
from app.services.simulation.navigator import (
    build_navigator,
    total_repair_expansions,
    total_replans,
)
from app.services.simulation.sensors import RoverSensor

# Nav2's default tree allows six. Three tries each recovery once before a fresh
# failure ends the drive, which keeps a hopeless traverse from wandering.
DEFAULT_MAX_RECOVERIES = 3


@dataclass(slots=True)
class MissionEvent:
    """One entry in the mission event log."""

    t_seconds: float
    category: str  # navigation | planner | energy | risk | executive | system
    kind: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "t_seconds": round(self.t_seconds, 2),
            "category": self.category,
            "kind": self.kind,
            "detail": self.detail,
        }


@dataclass(slots=True)
class TraverseResult:
    outcome: PlanningOutcome
    executed_cells: list[Cell]
    events: list[MissionEvent]
    distance_m: float
    energy_kwh: float
    elapsed_seconds: float
    replans: int
    reroutes: int
    repair_expansions: int
    belief_summary: dict
    initial_plan_cells: list[Cell] = field(default_factory=list)
    executive: dict = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.outcome.succeeded

    def as_dict(self) -> dict:
        return {
            **self.outcome.as_dict(),
            "distance_m": round(self.distance_m, 3),
            "energy_kwh": round(self.energy_kwh, 6),
            "elapsed_seconds": round(self.elapsed_seconds, 1),
            "replans": self.replans,
            "reroutes": self.reroutes,
            "repair_expansions": self.repair_expansions,
            "steps": len(self.executed_cells) - 1 if self.executed_cells else 0,
            "belief": self.belief_summary,
            "executive": self.executive,
            "events": [e.as_dict() for e in self.events],
        }


def simulate_traverse(
    truth_hazard: np.ndarray,
    start: Cell,
    goal: Cell,
    belief: BeliefState,
    sensor: RoverSensor,
    grid_template: PlanningGrid,
    rng: np.random.Generator,
    uncertainty_weight: float = 0.0,
    max_steps: int | None = None,
    energy_budget_kwh: float | None = None,
    max_recoveries: int = DEFAULT_MAX_RECOVERIES,
) -> TraverseResult:
    """Drive from ``start`` to ``goal`` under partial observability.

    The drive is a behaviour tree (see ``navigator.py``). With no failure it is
    step-for-step the loop it replaced; on a recoverable failure it tries up to
    ``max_recoveries`` recoveries before giving up. ``max_recoveries=0`` is the
    old loop exactly.
    """
    rover = grid_template.rover
    step_limit = (
        max_steps if max_steps is not None else 4 * (belief.shape[0] + belief.shape[1]) ** 2
    )
    truth = np.asarray(truth_hazard, dtype=np.float64)

    events: list[MissionEvent] = []
    bb = Blackboard(
        # The planner searches belief. The accountant charges truth.
        truth_hazard=np.asarray(truth_hazard),
        truth_grid=grid_template.with_hazard(truth),
        grid_template=grid_template,
        belief=belief,
        sensor=sensor,
        rng=rng,
        start=start,
        goal=goal,
        uncertainty_weight=uncertainty_weight,
        budget=energy_budget_kwh if energy_budget_kwh is not None else rover.battery_capacity_kwh,
        step_limit=step_limit,
        position=start,
        executed=[start],
        distance=0.0,
        energy=0.0,
        clock=0.0,
        planner=None,
        needs_fresh_plan=False,
        initial_plan=[],
        follow_ticks=0,
        reroutes=0,
        retired_replans=0,
        retired_expansions=0,
        recoveries=[],
        error=None,
        failure=None,
    )

    def log(category: str, kind: str, **detail) -> None:
        events.append(MissionEvent(bb.clock, category, kind, detail))

    bb.log = log
    tree = build_navigator(max_recoveries)
    status = run_tree(tree, bb, max_ticks=step_limit + max_recoveries + 2)

    if status is Status.SUCCESS:
        log("navigation", "goal_reached", distance_m=round(bb.distance, 2))
        outcome = PlanningOutcome.success(
            None,
            distance_m=round(bb.distance, 3),
            energy_kwh=round(bb.energy, 6),
            replans=total_replans(bb),
        )
        if bb.recoveries:
            outcome.diagnostics["recoveries"] = len(bb.recoveries)
    else:
        outcome = bb.failure or PlanningOutcome.failed(
            FailureMode.TIMEOUT,
            "The traverse ended without reaching the goal.",
            steps_taken=len(bb.executed) - 1,
        )
        if bb.recoveries:
            outcome.diagnostics["recoveries_attempted"] = [r["recovery"] for r in bb.recoveries]

    return TraverseResult(
        outcome=outcome,
        executed_cells=bb.executed,
        events=events,
        distance_m=bb.distance,
        energy_kwh=bb.energy,
        elapsed_seconds=bb.clock,
        replans=total_replans(bb),
        reroutes=bb.reroutes,
        repair_expansions=total_repair_expansions(bb),
        belief_summary=belief.summary(),
        initial_plan_cells=bb.initial_plan,
        executive={
            "tree": tree.describe(),
            "ticks": bb.tick,
            "recoveries": bb.recoveries,
            "final_uncertainty_weight": bb.uncertainty_weight,
        },
    )
