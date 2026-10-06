"""Which science targets to visit, in what order, under what budget."""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from app.services.planning import astar
from app.services.planning.grid import Cell, PlanningGrid
from app.services.planning.outcome import FailureMode, PlanningOutcome


@dataclass(slots=True)
class ScienceTarget:
    """One place worth stopping at."""

    target_id: str
    cell: Cell
    value: float  # 0-1 scientific value; relative within a mission
    observation_seconds: float = 1200.0
    required_instrument: str | None = None
    priority: str = "medium"  # low | medium | high

    def as_dict(self) -> dict:
        return {
            "target_id": self.target_id,
            "cell": list(self.cell),
            "value": self.value,
            "observation_seconds": self.observation_seconds,
            "required_instrument": self.required_instrument,
            "priority": self.priority,
        }


@dataclass(slots=True)
class TourLeg:
    frm: str
    to: str
    cells: list[Cell]
    cost: float
    distance_m: float
    energy_kwh: float
    seconds: float

    def as_dict(self) -> dict:
        return {
            "from": self.frm,
            "to": self.to,
            "cost": round(self.cost, 4),
            "distance_m": round(self.distance_m, 2),
            "energy_kwh": round(self.energy_kwh, 6),
            "seconds": round(self.seconds, 1),
            "waypoints": len(self.cells),
        }


@dataclass(slots=True)
class ScienceTour:
    visited: list[ScienceTarget]
    legs: list[TourLeg]
    skipped: list[dict]
    energy_kwh: float
    distance_m: float
    seconds: float
    science_value: float
    energy_budget_kwh: float
    time_budget_seconds: float
    improvement_passes: int = 0
    extras: dict = field(default_factory=dict)

    @property
    def cells(self) -> list[Cell]:
        """The full drive as one cell sequence, legs concatenated without repeats."""
        route: list[Cell] = []
        for leg in self.legs:
            route.extend(leg.cells if not route else leg.cells[1:])
        return route

    def as_dict(self) -> dict:
        return {
            "method": (
                "greedy value/cost insertion under budget, then 2-opt on the order; "
                "orienteering heuristic, not an exact solver"
            ),
            "visited": [t.as_dict() for t in self.visited],
            "skipped": self.skipped,
            "legs": [leg.as_dict() for leg in self.legs],
            "science_value_collected": round(self.science_value, 4),
            "distance_m": round(self.distance_m, 2),
            "energy_kwh": round(self.energy_kwh, 6),
            "energy_budget_kwh": round(self.energy_budget_kwh, 6),
            "energy_utilisation": (
                round(self.energy_kwh / self.energy_budget_kwh, 4)
                if self.energy_budget_kwh
                else None
            ),
            "seconds": round(self.seconds, 1),
            "time_budget_seconds": self.time_budget_seconds,
            "two_opt_improvements": self.improvement_passes,
            **self.extras,
        }


def _leg(
    grid: PlanningGrid, frm: Cell, to: Cell
) -> tuple[list[Cell], float, float, float, float] | None:
    result = astar.search(grid, frm, to)
    if not result.found:
        return None
    distance = 0.0
    energy = 0.0
    seconds = 0.0
    for previous, cell in itertools.pairwise(result.cells):
        distance += grid.distance_m(previous, cell)
        energy += grid.step_energy_kwh(previous, cell)
        seconds += grid.traverse_seconds(previous, cell)
    return result.cells, result.total_cost, distance, energy, seconds


def plan_science_tour(
    grid: PlanningGrid,
    start: Cell,
    targets: list[ScienceTarget],
    energy_budget_kwh: float | None = None,
    time_budget_seconds: float = 4 * 88775.0,
    instruments: frozenset[str] | None = None,
    return_to_start: bool = False,
) -> PlanningOutcome:
    """Select and order science stops for one rover."""
    if not targets:
        raise ValueError("at least one science target is required")
    budget = energy_budget_kwh if energy_budget_kwh is not None else grid.rover.battery_capacity_kwh
    carried = instruments if instruments is not None else frozenset()

    skipped: list[dict] = []
    eligible: list[ScienceTarget] = []
    for target in targets:
        if target.required_instrument and target.required_instrument not in carried:
            skipped.append(
                {
                    "target_id": target.target_id,
                    "reason": "instrument_not_carried",
                    "detail": f"requires {target.required_instrument}",
                }
            )
            continue
        if grid.is_lethal(target.cell):
            skipped.append(
                {
                    "target_id": target.target_id,
                    "reason": "target_on_lethal_terrain",
                    "detail": f"hazard {float(grid.hazard[target.cell]):.3f} at "
                    f"or above the lethal threshold {grid.max_hazard}",
                }
            )
            continue
        eligible.append(target)

    if not eligible:
        return PlanningOutcome.failed(
            FailureMode.NO_SAFE_PATH,
            "No science target is both reachable by this rover and served by the "
            "instruments it carries.",
            skipped=skipped,
        )

    # --- leg cache: every pair we might need, planned once -------------------
    nodes: dict[str, Cell] = {"START": start}
    for target in eligible:
        nodes[target.target_id] = target.cell

    legs: dict[tuple[str, str], tuple] = {}
    unreachable: set[str] = set()
    for first, second in itertools.permutations(nodes, 2):
        if nodes[first] == nodes[second]:
            continue
        computed = _leg(grid, nodes[first], nodes[second])
        if computed is None:
            if second != "START":
                unreachable.add(second)
            continue
        legs[(first, second)] = computed

    for target_id in sorted(unreachable):
        if not any(key[1] == target_id for key in legs):
            skipped.append(
                {
                    "target_id": target_id,
                    "reason": "unreachable",
                    "detail": "no traversable route from any other tour node",
                }
            )
    eligible = [t for t in eligible if any(key[1] == t.target_id for key in legs)]

    if not eligible:
        return PlanningOutcome.failed(
            FailureMode.NO_SAFE_PATH,
            "Every candidate science target is walled off from the rover's start "
            "position by the slope limit or the lethal-hazard layer.",
            skipped=skipped,
        )

    # --- greedy insertion ---------------------------------------------------
    order: list[str] = []
    remaining = {t.target_id: t for t in eligible}
    remaining_all = {t.target_id: t for t in eligible}

    def tour_metrics(sequence: list[str]) -> tuple[float, float, float, float] | None:
        """(cost, distance, energy, seconds) for START -> sequence [-> START]."""
        chain = ["START", *sequence]
        if return_to_start:
            chain.append("START")
        cost = distance = energy = seconds = 0.0
        for first, second in itertools.pairwise(chain):
            entry = legs.get((first, second))
            if entry is None:
                return None
            _, leg_cost, leg_distance, leg_energy, leg_seconds = entry
            cost += leg_cost
            distance += leg_distance
            energy += leg_energy
            seconds += leg_seconds
        for target_id in sequence:
            seconds += remaining_all[target_id].observation_seconds
        return cost, distance, energy, seconds

    while remaining:
        best_id: str | None = None
        best_ratio = 0.0
        best_metrics: tuple[float, float, float, float] | None = None
        best_order: list[str] = []
        base = tour_metrics(order)
        base_cost = base[0] if base else 0.0

        for target_id, target in remaining.items():
            for position in range(len(order) + 1):
                candidate = [*order[:position], target_id, *order[position:]]
                metrics = tour_metrics(candidate)
                if metrics is None:
                    continue
                cost, _, energy, seconds = metrics
                if energy > budget or seconds > time_budget_seconds:
                    continue
                marginal = max(cost - base_cost, 1e-9)
                ratio = target.value / marginal
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_id = target_id
                    best_metrics = metrics
                    best_order = candidate

        if best_id is None or best_metrics is None:
            break
        order = best_order
        del remaining[best_id]

    for target_id, target in remaining.items():
        skipped.append(
            {
                "target_id": target_id,
                "reason": "budget_exhausted",
                "detail": (
                    f"adding it exceeds the {budget:.3f} kWh energy budget or the "
                    f"{time_budget_seconds:.0f} s time budget"
                ),
                "value": target.value,
            }
        )

    if not order:
        return PlanningOutcome.failed(
            FailureMode.LOW_BATTERY,
            f"No science target fits inside the {budget:.3f} kWh energy budget: "
            "the cheapest single out-and-back already exceeds it.",
            energy_budget_kwh=budget,
            skipped=skipped,
        )

    # --- 2-opt on the order -------------------------------------------------
    improvements = 0
    improved = True
    while improved and len(order) > 2:
        improved = False
        current = tour_metrics(order)
        assert current is not None
        for i in range(len(order) - 1):
            for j in range(i + 1, len(order)):
                candidate = order[:i] + order[i : j + 1][::-1] + order[j + 1 :]
                metrics = tour_metrics(candidate)
                if metrics is None:
                    continue
                if metrics[0] < current[0] - 1e-9 and metrics[2] <= budget:
                    order = candidate
                    current = metrics
                    improvements += 1
                    improved = True
                    break
            if improved:
                break

    final = tour_metrics(order)
    assert final is not None
    _, distance, energy, seconds = final

    chain = ["START", *order]
    if return_to_start:
        chain.append("START")
    tour_legs: list[TourLeg] = []
    for first, second in itertools.pairwise(chain):
        cells, cost, leg_distance, leg_energy, leg_seconds = legs[(first, second)]
        tour_legs.append(TourLeg(first, second, cells, cost, leg_distance, leg_energy, leg_seconds))

    visited = [remaining_all[target_id] for target_id in order]
    tour = ScienceTour(
        visited=visited,
        legs=tour_legs,
        skipped=skipped,
        energy_kwh=energy,
        distance_m=distance,
        seconds=seconds,
        science_value=sum(t.value for t in visited),
        energy_budget_kwh=budget,
        time_budget_seconds=time_budget_seconds,
        improvement_passes=improvements,
        extras={
            "targets_offered": len(targets),
            "targets_visited": len(visited),
            "instruments_carried": sorted(carried),
            "returns_to_start": return_to_start,
        },
    )
    return PlanningOutcome.success(None, tour=tour)
