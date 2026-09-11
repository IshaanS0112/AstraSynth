"""Driving a planned route against terrain the rover cannot fully see.

This is where the three layers meet. The simulator holds reality; the rover
holds a belief and a sensor; D* Lite repairs the route when the two disagree.
The output is an executed path plus a timestamped event log - the same log the
mission-control event stream renders, and the same one the Monte Carlo evaluator
aggregates over a thousand runs.

The loop, per step:

1. sense - noisy readings of reality within range and line of sight;
2. update - fold them into the belief; get back only what actually changed;
3. repair - hand the changed cells to D* Lite, which fixes the affected part of
   the search tree and nothing else;
4. step - move one cell along the repaired route;
5. account - charge energy and time against the *true* terrain, not the believed
   terrain, because the battery does not care what the rover expected.

Point 5 is the one that makes the simulation worth running. A rover that plans
optimistically and is charged optimistically always succeeds. Charging the truth
is what lets a mission fail for the reason a real one would.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from app.services.planning.dstar_lite import DStarLite
from app.services.planning.grid import Cell, PlanningGrid
from app.services.planning.outcome import FailureMode, PlanningOutcome
from app.services.simulation.belief import BeliefState
from app.services.simulation.sensors import RoverSensor


@dataclass(slots=True)
class MissionEvent:
    """One entry in the mission event log."""

    t_seconds: float
    category: str  # navigation | planner | energy | risk | system
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
) -> TraverseResult:
    """Drive from ``start`` to ``goal`` under partial observability.

    ``grid_template`` supplies the rover, the scale and the constraint
    thresholds; its hazard field is replaced by the rover's *belief*, so nothing
    inside the planner can reach the truth even by accident.

    ``energy_budget_kwh`` defaults to the rover's battery capacity. Exceeding it
    ends the traverse with :class:`FailureMode.LOW_BATTERY` rather than a
    silently over-budget success.
    """
    rover = grid_template.rover
    budget = energy_budget_kwh if energy_budget_kwh is not None else rover.battery_capacity_kwh
    step_limit = (
        max_steps if max_steps is not None else 4 * (belief.shape[0] + belief.shape[1]) ** 2
    )

    # The planner searches belief. The accountant charges truth.
    belief_grid = grid_template.with_hazard(belief.planning_hazard(uncertainty_weight))
    truth_grid = grid_template.with_hazard(np.asarray(truth_hazard, dtype=np.float64))

    events: list[MissionEvent] = []
    clock = 0.0

    def log(category: str, kind: str, **detail) -> None:
        events.append(MissionEvent(clock, category, kind, detail))

    planner = DStarLite(belief_grid, start, goal)
    log(
        "planner",
        "initial_plan",
        expansions=planner.stats.initial_expansions,
        reachable=planner.reachable,
        cost=round(planner.cost_to_goal(), 4) if planner.reachable else None,
        uncertainty_weight=uncertainty_weight,
    )
    initial_plan = planner.extract_path()

    if not planner.reachable:
        return TraverseResult(
            outcome=PlanningOutcome.failed(
                FailureMode.NO_SAFE_PATH,
                "The goal is unreachable under the rover's initial belief; the "
                "traverse was never started.",
                start_cell=list(start),
                goal_cell=list(goal),
            ),
            executed_cells=[start],
            events=events,
            distance_m=0.0,
            energy_kwh=0.0,
            elapsed_seconds=0.0,
            replans=0,
            reroutes=0,
            repair_expansions=0,
            belief_summary=belief.summary(),
        )

    executed: list[Cell] = [start]
    position = start
    distance = 0.0
    energy = 0.0
    replans_materially = 0
    failure: PlanningOutcome | None = None

    for _ in range(step_limit):
        observations = sensor.observe(np.asarray(truth_hazard), truth_grid, position, rng)
        changed = belief.update(observations, sensor.noise_sigma)
        if changed:
            planning_values = {
                cell: float(
                    min(
                        1.0,
                        max(0.0, belief.mean[cell] + uncertainty_weight * belief.sigma[cell]),
                    )
                )
                for cell in changed
            }
            cost_before = planner.cost_to_goal()
            step_before = planner.next_step()
            record = planner.apply_updates(planning_values)
            if record is not None:
                cost_after = planner.cost_to_goal()
                # A repair that leaves the route unchanged is a confirmation, not
                # a diversion. The event log distinguishes the two because an
                # operator watching a hundred repairs scroll past needs to see
                # only the ones that actually moved the rover somewhere else.
                rerouted = planner.next_step() != step_before
                replans_materially += 1 if rerouted else 0
                log(
                    "planner",
                    "replan",
                    trigger_cells=record.trigger_cells,
                    vertices_expanded=record.vertices_expanded,
                    reachable=planner.reachable,
                    rerouted=rerouted,
                    cost_to_goal=(round(cost_after, 4) if planner.reachable else None),
                    cost_delta=(
                        round(cost_after - cost_before, 4)
                        if planner.reachable and cost_before < float("inf")
                        else None
                    ),
                )

        if position == goal:
            break

        next_cell = planner.next_step()
        if next_cell is None:
            failure = PlanningOutcome.failed(
                FailureMode.ROVER_BLOCKED,
                f"The rover reached {position} and no traversable route to {goal} "
                "remains under its current belief. Every neighbouring cell is "
                "blocked by the slope limit or the lethal-hazard threshold.",
                position=list(position),
                goal_cell=list(goal),
                steps_taken=len(executed) - 1,
            )
            log("risk", "blocked", position=list(position))
            break

        step_energy = truth_grid.step_energy_kwh(position, next_cell)
        if energy + step_energy > budget:
            failure = PlanningOutcome.failed(
                FailureMode.LOW_BATTERY,
                f"Battery exhausted after {distance:.1f} m: the next step needs "
                f"{step_energy:.4f} kWh and only "
                f"{budget - energy:.4f} kWh of the {budget:.3f} kWh budget remains.",
                energy_spent_kwh=round(energy, 6),
                energy_budget_kwh=round(budget, 6),
                position=list(position),
                steps_taken=len(executed) - 1,
            )
            log("energy", "battery_exhausted", spent_kwh=round(energy, 6))
            break

        distance += truth_grid.distance_m(position, next_cell)
        energy += step_energy
        clock += truth_grid.traverse_seconds(position, next_cell)
        planner.move_to(next_cell)
        position = next_cell
        executed.append(position)
    else:
        failure = PlanningOutcome.failed(
            FailureMode.TIMEOUT,
            f"The traverse exceeded its {step_limit}-step budget without reaching the goal.",
            steps_taken=len(executed) - 1,
        )

    if failure is None and position != goal:
        failure = PlanningOutcome.failed(
            FailureMode.TIMEOUT,
            "The traverse ended without reaching the goal.",
            steps_taken=len(executed) - 1,
        )

    if failure is None:
        log("navigation", "goal_reached", distance_m=round(distance, 2))

    outcome = failure or PlanningOutcome.success(
        None,
        distance_m=round(distance, 3),
        energy_kwh=round(energy, 6),
        replans=planner.stats.replan_count,
    )

    return TraverseResult(
        outcome=outcome,
        executed_cells=executed,
        events=events,
        distance_m=distance,
        energy_kwh=energy,
        elapsed_seconds=clock,
        replans=planner.stats.replan_count,
        reroutes=replans_materially,
        repair_expansions=planner.stats.repair_expansions,
        belief_summary=belief.summary(),
        initial_plan_cells=initial_plan,
    )
