"""The traverse as a behaviour tree, shaped after Nav2's navigate-with-recovery.

::

    RecoveryNode "NavigateRecovery" (retries = max_recoveries)
    ├── Sequence "NavigateWithReplanning"
    │   ├── ComputePathToGoal     plan, or validate the plan D* Lite is repairing
    │   └── FollowPath            one drive step per tick: observe, repair, move
    └── Sequence "RecoveryActions"
        ├── WouldARecoveryHelp    gate on the error code
        └── RoundRobin "RecoveryCycle"
            ├── ReSense           mast panorama: twice the range, half the noise
            ├── RelaxUncertainty  halve the uncertainty penalty and replan
            └── BackUp            retreat along the driven path for a new vantage

With no failure the tree does exactly what the old loop did, step for step, so a
traverse that never needs a recovery is unchanged. What it adds is the answer to
"blocked - now what?", which the loop could only answer with "abort".

Recovery is gated as Nav2's WouldAPlannerRecoveryHelp is: an exhausted battery or
step budget is final, because nothing a recovery does creates energy or time.
"""

from __future__ import annotations

import dataclasses

from app.services.planning.dstar_lite import DStarLite
from app.services.planning.outcome import FailureMode, PlanningOutcome
from app.services.simulation.behavior_tree import (
    Action,
    Blackboard,
    Condition,
    Node,
    RecoveryNode,
    RoundRobin,
    Sequence,
    Status,
)

# Failures a recovery can plausibly fix: the map was wrong or too pessimistic.
RECOVERABLE = frozenset({FailureMode.NO_SAFE_PATH, FailureMode.ROVER_BLOCKED})

PANORAMA_RANGE_FACTOR = 2.0  # the mast sees further than the drive cameras
PANORAMA_NOISE_FACTOR = 0.5  # and more precisely, standing still
PANORAMA_HEIGHT_FACTOR = 2.0  # from higher up, so over more of the terrain
PANORAMA_SECONDS = 600.0  # stopping to image a full panorama
BACKUP_CELLS = 3
MIN_UNCERTAINTY_WEIGHT = 0.05  # below this, relaxing further changes nothing useful


# --- main branch --------------------------------------------------------------


def compute_path(bb: Blackboard) -> Status:
    if bb.planner is None or bb.needs_fresh_plan:
        first = bb.planner is None
        if not first:
            _retire_planner(bb)
        grid = bb.grid_template.with_hazard(bb.belief.planning_hazard(bb.uncertainty_weight))
        bb.planner = DStarLite(grid, bb.position, bb.goal)
        bb.needs_fresh_plan = False
        bb.log(
            "planner",
            "initial_plan" if first else "replan_from_scratch",
            expansions=bb.planner.stats.initial_expansions,
            reachable=bb.planner.reachable,
            cost=round(bb.planner.cost_to_goal(), 4) if bb.planner.reachable else None,
            uncertainty_weight=bb.uncertainty_weight,
        )
        if first:
            bb.initial_plan = bb.planner.extract_path()

    if bb.planner.reachable:
        return Status.SUCCESS

    if len(bb.executed) == 1:
        # Never moved: the goal was out of reach before the drive began.
        bb.error = FailureMode.NO_SAFE_PATH
        bb.failure = PlanningOutcome.failed(
            FailureMode.NO_SAFE_PATH,
            "The goal is unreachable under the rover's initial belief; the "
            "traverse was never started.",
            start_cell=list(bb.start),
            goal_cell=list(bb.goal),
        )
    else:
        # Out on the terrain and cut off: the same finding FollowPath reports,
        # whichever node happens to notice it first after a recovery.
        bb.error = FailureMode.ROVER_BLOCKED
        bb.failure = PlanningOutcome.failed(
            FailureMode.ROVER_BLOCKED,
            f"The rover reached {bb.position} and no traversable route to {bb.goal} "
            "remains under its current belief.",
            position=list(bb.position),
            goal_cell=list(bb.goal),
            steps_taken=len(bb.executed) - 1,
        )
    return Status.FAILURE


def follow_path(bb: Blackboard) -> Status:
    if bb.follow_ticks >= bb.step_limit:
        bb.error = FailureMode.TIMEOUT
        bb.failure = PlanningOutcome.failed(
            FailureMode.TIMEOUT,
            f"The traverse exceeded its {bb.step_limit}-step budget without reaching the goal.",
            steps_taken=len(bb.executed) - 1,
        )
        return Status.FAILURE
    bb.follow_ticks += 1

    _observe_and_repair(bb)

    if bb.position == bb.goal:
        return Status.SUCCESS

    next_cell = bb.planner.next_step()
    if next_cell is None:
        bb.error = FailureMode.ROVER_BLOCKED
        bb.failure = PlanningOutcome.failed(
            FailureMode.ROVER_BLOCKED,
            f"The rover reached {bb.position} and no traversable route to {bb.goal} "
            "remains under its current belief. Every neighbouring cell is "
            "blocked by the slope limit or the lethal-hazard threshold.",
            position=list(bb.position),
            goal_cell=list(bb.goal),
            steps_taken=len(bb.executed) - 1,
        )
        bb.log("risk", "blocked", position=list(bb.position))
        return Status.FAILURE

    if not _drive(bb, next_cell):
        return Status.FAILURE
    return Status.RUNNING


def _observe_and_repair(bb: Blackboard) -> None:
    observations = bb.sensor.observe(bb.truth_hazard, bb.truth_grid, bb.position, bb.rng)
    changed = bb.belief.update(observations, bb.sensor.noise_sigma)
    if changed:
        _push_to_planner(bb, changed, reason="replan")


def _push_to_planner(bb: Blackboard, cells, reason: str) -> None:
    w = bb.uncertainty_weight
    values = {
        cell: float(min(1.0, max(0.0, bb.belief.mean[cell] + w * bb.belief.sigma[cell])))
        for cell in cells
    }
    planner = bb.planner
    cost_before = planner.cost_to_goal()
    step_before = planner.next_step()
    record = planner.apply_updates(values)
    if record is None:
        return
    cost_after = planner.cost_to_goal()
    # A repair that leaves the route unchanged is a confirmation, not a
    # diversion; only the second kind is worth an operator's attention.
    rerouted = planner.next_step() != step_before
    bb.reroutes += 1 if rerouted else 0
    bb.log(
        "planner",
        reason,
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


def _drive(bb: Blackboard, next_cell) -> bool:
    """Move one cell, charging the TRUE terrain. ``False`` if the battery says no."""
    step_energy = bb.truth_grid.step_energy_kwh(bb.position, next_cell)
    if bb.energy + step_energy > bb.budget:
        bb.error = FailureMode.LOW_BATTERY
        bb.failure = PlanningOutcome.failed(
            FailureMode.LOW_BATTERY,
            f"Battery exhausted after {bb.distance:.1f} m: the next step needs "
            f"{step_energy:.4f} kWh and only "
            f"{bb.budget - bb.energy:.4f} kWh of the {bb.budget:.3f} kWh budget remains.",
            energy_spent_kwh=round(bb.energy, 6),
            energy_budget_kwh=round(bb.budget, 6),
            position=list(bb.position),
            steps_taken=len(bb.executed) - 1,
        )
        bb.log("energy", "battery_exhausted", spent_kwh=round(bb.energy, 6))
        return False

    bb.distance += bb.truth_grid.distance_m(bb.position, next_cell)
    bb.energy += step_energy
    bb.clock += bb.truth_grid.traverse_seconds(bb.position, next_cell)
    bb.planner.move_to(next_cell)
    bb.position = next_cell
    bb.executed.append(next_cell)
    return True


# --- recovery branch ----------------------------------------------------------


def would_a_recovery_help(bb: Blackboard) -> bool:
    return bb.error in RECOVERABLE


def resense(bb: Blackboard) -> Status:
    """Stop and image a full panorama from the mast.

    Pushes the new planning value of every cell it saw, not only cells whose
    mean moved: the point of a panorama is to shrink sigma, and with an
    uncertainty penalty sigma is half of what the planner searches.
    """
    mast = dataclasses.replace(
        bb.sensor,
        range_m=bb.sensor.range_m * PANORAMA_RANGE_FACTOR,
        noise_sigma=bb.sensor.noise_sigma * PANORAMA_NOISE_FACTOR,
        sensor_height_m=bb.sensor.sensor_height_m * PANORAMA_HEIGHT_FACTOR,
    )
    observations = mast.observe(bb.truth_hazard, bb.truth_grid, bb.position, bb.rng)
    bb.belief.update(observations, mast.noise_sigma)
    bb.clock += PANORAMA_SECONDS

    reachable_before = bb.planner.reachable
    trigger_before = len(bb.planner.stats.repairs)
    _push_to_planner(bb, observations.keys(), reason="replan_after_panorama")
    learned = len(bb.planner.stats.repairs) > trigger_before

    _record_recovery(
        bb,
        "ReSense",
        cells_imaged=len(observations),
        changed_the_plan=learned,
        reachable_before=reachable_before,
        reachable_after=bb.planner.reachable,
    )
    # Nothing new learned means nothing for a retry to use: hand over now.
    return Status.SUCCESS if learned else Status.FAILURE


def relax_uncertainty(bb: Blackboard) -> Status:
    if bb.uncertainty_weight < MIN_UNCERTAINTY_WEIGHT:
        return Status.FAILURE
    before = bb.uncertainty_weight
    bb.uncertainty_weight = before / 2.0 if before / 2.0 >= MIN_UNCERTAINTY_WEIGHT else 0.0
    bb.needs_fresh_plan = True
    _record_recovery(
        bb,
        "RelaxUncertainty",
        uncertainty_weight_before=before,
        uncertainty_weight_after=bb.uncertainty_weight,
        note="accepting more risk on unsurveyed ground",
    )
    return Status.SUCCESS


def back_up(bb: Blackboard) -> Status:
    """Retreat along the driven path - a different vantage sees different ground."""
    if len(bb.executed) < 2:
        return Status.FAILURE
    origin = bb.position
    driven_back = 0
    trail = list(reversed(bb.executed[:-1]))
    for cell in trail[:BACKUP_CELLS]:
        if not _drive(bb, cell):
            break
        driven_back += 1
    if driven_back == 0:
        return Status.FAILURE
    _record_recovery(
        bb, "BackUp", cells=driven_back, from_cell=list(origin), to_cell=list(bb.position)
    )
    return Status.SUCCESS


def _record_recovery(bb: Blackboard, name: str, **detail) -> None:
    entry = {"recovery": name, "after_error": bb.error.value if bb.error else None, **detail}
    bb.recoveries.append(entry)
    bb.log("executive", "recovery", **entry)


def _retire_planner(bb: Blackboard) -> None:
    stats = bb.planner.stats
    bb.retired_replans += stats.replan_count
    bb.retired_expansions += stats.repair_expansions


# --- assembly -----------------------------------------------------------------


def build_navigator(max_recoveries: int) -> Node:
    return RecoveryNode(
        "NavigateRecovery",
        max_recoveries,
        Sequence(
            "NavigateWithReplanning",
            Action("ComputePathToGoal", compute_path),
            Action("FollowPath", follow_path),
        ),
        Sequence(
            "RecoveryActions",
            Condition("WouldARecoveryHelp", would_a_recovery_help),
            RoundRobin(
                "RecoveryCycle",
                Action("ReSense", resense),
                Action("RelaxUncertainty", relax_uncertainty),
                Action("BackUp", back_up),
            ),
        ),
    )


def total_replans(bb: Blackboard) -> int:
    return bb.retired_replans + (bb.planner.stats.replan_count if bb.planner else 0)


def total_repair_expansions(bb: Blackboard) -> int:
    return bb.retired_expansions + (bb.planner.stats.repair_expansions if bb.planner else 0)
