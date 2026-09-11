"""Conflict-Based Search for a heterogeneous rover fleet.

Why not just plan each rover separately
---------------------------------------
Because two rovers routed independently through the same corridor will drive
through each other. Planning them jointly in one search is the obvious fix and
the wrong one: the joint state space is the product of the individual ones, so
three rovers on a 192x192 grid is a graph of about 5 x 10^13 states.

CBS (Sharon et al., 2015) is the standard escape. It plans each rover on its own
grid, checks the resulting routes against each other, and when two collide it
splits the problem in two: "rover A may not be there then" and "rover B may not
be there then". Each branch re-plans one rover, single-agent, on its own graph.
The search stays in the space of *constraints*, which is small, instead of the
space of joint positions, which is not.

Heterogeneous by construction
-----------------------------
Each :class:`Agent` carries its own :class:`PlanningGrid`, so each rover brings
its own slope limit, its own hazard tolerance and its own energy model. A Scout
that can cross a 20-degree ridge and a Heavy Lab that cannot are searching
genuinely different graphs over the same terrain, which is the point of having a
fleet rather than three copies of one rover.

Simplifications, stated plainly
-------------------------------
* **Uniform time step.** One grid move takes one tick for every rover.
  Heterogeneous *speed* is therefore modelled in cost, not in duration - a slow
  rover pays more per move but does not occupy a cell for longer. Modelling true
  differential speed needs a continuous-time conflict check, which this does not
  attempt.
* **Optimality.** This is standard CBS with no admissible high-level heuristic,
  so it is optimal for the sum-of-costs objective *within the node budget*. Hit
  the budget and it returns a diagnosed failure rather than a route it cannot
  vouch for.
* **Rovers occupy one cell.** No footprint or turning radius.
"""

from __future__ import annotations

import heapq
import itertools
from dataclasses import dataclass, field
from time import monotonic

from app.services.planning.grid import Cell, PlanningGrid
from app.services.planning.outcome import FailureMode, PlanningOutcome

TimedCell = tuple[Cell, int]


@dataclass(slots=True)
class Agent:
    """One rover, with its own terrain graph."""

    agent_id: str
    grid: PlanningGrid
    start: Cell
    goal: Cell


@dataclass(frozen=True)
class VertexConstraint:
    """``agent_id`` may not be at ``cell`` at ``time`` - or must be, if positive.

    Positive constraints are what make the two children of a split *disjoint*
    (Li et al., 2019). Ordinary CBS branches into "agent A avoids v at t" and
    "agent B avoids v at t", and those two sets of solutions overlap: every
    solution where neither agent goes near v is explored down both branches. On
    open ground, where thousands of equal-cost routes exist, that duplication is
    not a constant factor - it is the difference between terminating and not.
    Splitting into "A is at v at t" and "A is not at v at t" partitions the
    solution space instead, so nothing is explored twice.
    """

    agent_id: str
    cell: Cell
    time: int
    positive: bool = False


@dataclass(frozen=True)
class EdgeConstraint:
    agent_id: str
    frm: Cell
    to: Cell
    time: int  # the tick at which the move *completes*


Constraint = VertexConstraint | EdgeConstraint


@dataclass(slots=True)
class Conflict:
    kind: str  # "vertex" | "edge"
    agents: tuple[str, str]
    time: int
    cell: Cell
    other_cell: Cell | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "agents": list(self.agents),
            "time": self.time,
            "cell": list(self.cell),
            "other_cell": list(self.other_cell) if self.other_cell else None,
        }


@dataclass(slots=True)
class CBSSolution:
    paths: dict[str, list[Cell]]
    costs: dict[str, float]
    high_level_nodes: int
    low_level_calls: int
    # Every conflict the high level branched on. A conflict can appear more than
    # once: two sibling branches inherit the same unresolved collision, and both
    # re-detect it. The list is a search trace, not a set of distinct collisions.
    conflicts_branched: list[Conflict] = field(default_factory=list)

    @property
    def sum_of_costs(self) -> float:
        return sum(self.costs.values())

    @property
    def makespan(self) -> int:
        return max((len(p) - 1 for p in self.paths.values()), default=0)

    def as_dict(self) -> dict:
        return {
            "algorithm": "conflict_based_search",
            "sum_of_costs": round(self.sum_of_costs, 4),
            "makespan": self.makespan,
            "high_level_nodes": self.high_level_nodes,
            "low_level_calls": self.low_level_calls,
            "conflicts_branched": [c.as_dict() for c in self.conflicts_branched],
            "per_agent_cost": {k: round(v, 4) for k, v in self.costs.items()},
            "time_model": "uniform tick per grid move; speed differences priced in cost",
        }


# --- low level: space-time A* ----------------------------------------------


def _wait_cost(grid: PlanningGrid) -> float:
    """Price of holding position for one tick.

    Must be strictly positive or the search can idle for free and never
    terminate. One cell-width of travel is the natural scale: waiting a tick
    costs what creeping forward a cell would, so a rover yields only when
    yielding is genuinely cheaper than going around.
    """
    return grid.meters_per_cell


def conflict_avoidance_table(
    paths: dict[str, list[Cell]], exclude: str, horizon: int
) -> dict[TimedCell, int]:
    """How many other agents occupy each ``(cell, time)`` on the current solution.

    This is the conflict-avoidance table from the original CBS paper, and it is
    not an optimisation - without it this solver does not terminate on open
    terrain. On uniform ground an enormous number of equal-cost routes exist, so
    forbidding an agent one cell at one tick is answered by an equally cheap
    route that collides one cell further along. The constraint tree then wanders
    a plateau of identical-cost nodes forever: measured on a flat 24x24 grid with
    three rovers, 4000 constraint-tree nodes without ever dropping below one
    remaining conflict.

    The table breaks the plateau at the *low* level instead. Among paths of
    equal cost - and only among those - the search prefers the one that runs
    into other agents least. Cost ordering is untouched, so the solution CBS
    returns is still optimal for sum-of-costs; it is simply found.
    """
    table: dict[TimedCell, int] = {}
    for agent_id, path in paths.items():
        if agent_id == exclude:
            continue
        for time in range(horizon):
            cell = _position_at(path, time)
            table[(cell, time)] = table.get((cell, time), 0) + 1
    return table


def plan_with_constraints(
    agent: Agent,
    constraints: frozenset[Constraint],
    max_time: int,
    avoid: dict[TimedCell, int] | None = None,
    adjacency: Adjacency | None = None,
) -> tuple[list[Cell], float, int] | None:
    """Space-time A* for one agent under a set of constraints.

    Returns ``(path, cost, expansions)`` or ``None`` if the agent cannot reach
    its goal within ``max_time`` ticks under these constraints.

    ``avoid`` is an optional conflict-avoidance table. It never makes a path
    legal or illegal - it only orders equal-cost alternatives, preferring the
    one that shares fewest ``(cell, time)`` slots with the rest of the fleet.

    The goal test is not simply "standing on the goal": an agent that parks on
    its goal blocks it forever, so arriving is only legal once no vertex
    constraint on the goal remains in the future.
    """
    vertex_blocked: set[TimedCell] = set()
    edge_blocked: set[tuple[Cell, Cell, int]] = set()
    must_be_at: dict[int, Cell] = {}
    latest_goal_constraint = -1
    for constraint in constraints:
        if isinstance(constraint, VertexConstraint):
            if constraint.agent_id == agent.agent_id:
                if constraint.positive:
                    must_be_at[constraint.time] = constraint.cell
                    continue
                vertex_blocked.add((constraint.cell, constraint.time))
                if constraint.cell == agent.goal:
                    latest_goal_constraint = max(latest_goal_constraint, constraint.time)
            elif constraint.positive:
                # Someone else is required to be there; this agent must not be.
                vertex_blocked.add((constraint.cell, constraint.time))
                if constraint.cell == agent.goal:
                    latest_goal_constraint = max(latest_goal_constraint, constraint.time)
        elif constraint.agent_id == agent.agent_id:
            edge_blocked.add((constraint.frm, constraint.to, constraint.time))

    grid = agent.grid
    table = avoid or {}
    edges = adjacency if adjacency is not None else build_adjacency(grid)

    g_score: dict[TimedCell, float] = {(agent.start, 0): 0.0}
    # Secondary objective, used only to order equal-cost states.
    clashes: dict[TimedCell, int] = {(agent.start, 0): table.get((agent.start, 0), 0)}
    came_from: dict[TimedCell, TimedCell] = {}
    counter = itertools.count()
    open_heap: list[tuple[float, int, int, TimedCell]] = [
        (
            grid.heuristic(agent.start, agent.goal),
            clashes[(agent.start, 0)],
            next(counter),
            (agent.start, 0),
        )
    ]
    closed: set[TimedCell] = set()
    expansions = 0

    while open_heap:
        _, _, _, state = heapq.heappop(open_heap)
        if state in closed:
            continue
        closed.add(state)
        expansions += 1
        cell, time = state

        required_here = must_be_at.get(time)
        if required_here is not None and cell != required_here:
            continue

        if (
            cell == agent.goal
            and time > latest_goal_constraint
            and not any(t > time for t in must_be_at)
        ):
            path = [cell]
            cursor = state
            while cursor != (agent.start, 0):
                cursor = came_from[cursor]
                path.append(cursor[0])
            path.reverse()
            return path, g_score[state], expansions

        if time >= max_time:
            continue

        # The first entry of every adjacency tuple is the wait action; without it
        # a rover can never yield and a head-on swap has no resolution.
        for neighbour, step_cost in edges[cell]:
            next_state = (neighbour, time + 1)
            if next_state in closed:
                continue
            if (neighbour, time + 1) in vertex_blocked:
                continue
            required = must_be_at.get(time + 1)
            if required is not None and neighbour != required:
                continue
            if (cell, neighbour, time + 1) in edge_blocked:
                continue
            tentative = g_score[state] + step_cost
            tentative_clashes = clashes[state] + table.get(next_state, 0)
            known = g_score.get(next_state)
            # Strictly cheaper wins; equally cheap wins only on fewer clashes.
            # ``1e-9`` because two routes of the same length differ in the last
            # bits of a float sum, and an exact comparison would make the
            # tie-break depend on summation order.
            better = known is None or (
                tentative < known - 1e-9
                or (
                    abs(tentative - known) <= 1e-9
                    and tentative_clashes < clashes.get(next_state, 1 << 30)
                )
            )
            if better:
                g_score[next_state] = tentative
                clashes[next_state] = tentative_clashes
                came_from[next_state] = state
                heapq.heappush(
                    open_heap,
                    (
                        tentative + grid.heuristic(neighbour, agent.goal),
                        tentative_clashes,
                        next(counter),
                        next_state,
                    ),
                )

    return None


Adjacency = dict[Cell, tuple[tuple[Cell, float], ...]]


def build_adjacency(grid: PlanningGrid, wait_cost: float | None = None) -> Adjacency:
    """Materialise the whole traversable graph once, as plain Python tuples.

    CBS runs the low-level search thousands of times over a grid that does not
    change, and the inner loop was spending nearly all of its time re-deriving
    the same edges through numpy scalar indexing. Building the adjacency once
    per solve turns that into a dictionary lookup. Measured on the flagship
    demo's 64x64 grid, this is the difference between roughly two seconds per
    constraint-tree node and roughly a tenth of that.

    The wait action is baked in as the first entry of every cell's tuple. Its
    cost is constant per grid, so materialising it here means the inner loop
    allocates nothing at all per expansion.

    Safe precisely because the graph is static for the duration of a solve - the
    belief map is updated between traverse steps, never during deconfliction.
    """
    wait = _wait_cost(grid) if wait_cost is None else wait_cost
    adjacency: Adjacency = {}
    for row in range(grid.rows):
        for col in range(grid.cols):
            cell = (row, col)
            edges: list[tuple[Cell, float]] = [(cell, wait)]
            for neighbour in grid.neighbours(cell):
                cost = grid.edge_cost(cell, neighbour)
                if cost is not None:
                    edges.append((neighbour, cost))
            adjacency[cell] = tuple(edges)
    return adjacency


# --- conflict detection -----------------------------------------------------


def _position_at(path: list[Cell], time: int) -> Cell:
    """Where an agent is at ``time``, holding its goal once it has arrived."""
    return path[time] if time < len(path) else path[-1]


def find_conflicts(paths: dict[str, list[Cell]], limit: int | None = None) -> list[Conflict]:
    """Every vertex and edge conflict between the given routes.

    Used by CBS to pick one to resolve, and by the test suite as an independent
    check on a returned solution - a solver that also grades its own homework is
    not evidence of anything, so the verification path and the solving path
    share only this function.
    """
    conflicts: list[Conflict] = []
    if len(paths) < 2:
        return conflicts

    horizon = max(len(p) for p in paths.values())
    for first, second in itertools.combinations(sorted(paths), 2):
        path_a, path_b = paths[first], paths[second]
        for time in range(horizon):
            cell_a = _position_at(path_a, time)
            cell_b = _position_at(path_b, time)
            if cell_a == cell_b:
                conflicts.append(Conflict("vertex", (first, second), time, cell_a))
                if limit and len(conflicts) >= limit:
                    return conflicts
                continue
            if time == 0:
                continue
            previous_a = _position_at(path_a, time - 1)
            previous_b = _position_at(path_b, time - 1)
            # Swap: each drives into the cell the other just left.
            if previous_a == cell_b and previous_b == cell_a:
                conflicts.append(Conflict("edge", (first, second), time, previous_a, cell_a))
                if limit and len(conflicts) >= limit:
                    return conflicts
    return conflicts


# --- high level -------------------------------------------------------------


@dataclass(order=True)
class _CTNode:
    """A node of the constraint tree.

    Ordered by ``(cost, conflict_count, tie)``. The secondary key is not
    cosmetic. On uniform terrain an enormous number of constraint sets produce
    routes of *identical* sum-of-cost, and a search ordered on cost alone wanders
    through those ties without ever driving the conflict count down - on a flat
    24x24 grid with three rovers it exhausts a 4000-node budget without finding
    the answer. Breaking ties toward fewer remaining conflicts is the standard
    fix (the "fewest conflicts" rule from the ICBS line of work) and finds the
    same optimal-cost solution, because the primary key is untouched.
    """

    cost: float
    conflict_count: int
    tie: int
    constraints: frozenset = field(compare=False)
    paths: dict = field(compare=False)
    costs: dict = field(compare=False)
    conflicts: list = field(compare=False, default_factory=list)


def solve(
    agents: list[Agent],
    max_time: int | None = None,
    max_high_level_nodes: int = 400,
    time_budget_seconds: float | None = 60.0,
) -> PlanningOutcome:
    """Deconflict a fleet. Returns a :class:`PlanningOutcome` wrapping a solution.

    ``max_time`` bounds the low-level search horizon; it defaults to a value
    generous enough that a rover can cross the grid and still wait for the rest
    of the fleet. ``max_high_level_nodes`` bounds the constraint-tree search:
    exceeding it is reported as :class:`FailureMode.CONFLICT_UNRESOLVED`, never
    as a solution.

    ``time_budget_seconds`` is a wall-clock cap, reported as
    :class:`FailureMode.TIMEOUT`. A node budget alone is not enough of a guard:
    node cost grows with fleet size and grid size, so the same 2,000 nodes can
    take a second or twenty minutes. A planner with an operator in front of it
    needs a bound in the unit the operator is actually waiting in. Pass ``None``
    to disable it - appropriate for an offline study, not for anything
    interactive.
    """
    started_at = monotonic()
    if not agents:
        raise ValueError("at least one agent is required")
    ids = [a.agent_id for a in agents]
    if len(set(ids)) != len(ids):
        raise ValueError("agent ids must be unique")

    if max_time is None:
        largest = max(a.grid.rows + a.grid.cols for a in agents)
        max_time = 4 * largest

    low_level_calls = 0
    root_horizon = max_time
    adjacency = {agent.agent_id: build_adjacency(agent.grid) for agent in agents}
    root_paths: dict[str, list[Cell]] = {}
    root_costs: dict[str, float] = {}
    for agent in agents:
        result = plan_with_constraints(
            agent, frozenset(), root_horizon, adjacency=adjacency[agent.agent_id]
        )
        low_level_calls += 1
        if result is None:
            return PlanningOutcome.failed(
                FailureMode.NO_SAFE_PATH,
                f"Rover {agent.agent_id} has no route from {agent.start} to "
                f"{agent.goal} even with the rest of the fleet ignored.",
                agent_id=agent.agent_id,
                start_cell=list(agent.start),
                goal_cell=list(agent.goal),
            )
        path, cost, _ = result
        root_paths[agent.agent_id] = path
        root_costs[agent.agent_id] = cost

    # Tighten the horizon now that the unconstrained lengths are known. The
    # root search needs a generous bound because nothing is known yet; every
    # search after it does not, and the difference is large: the low-level state
    # space is cells x ticks, so a horizon four times longer is four times the
    # search. Slack of a few ticks per agent covers the waiting that
    # deconfliction actually requires - a fleet of n rovers can need at most a
    # bounded number of yields each. This is a practical bound, not a complete
    # one: a pathological instance needing more waiting than the slack allows is
    # reported as unsolved rather than solved incorrectly.
    longest = max(len(path) for path in root_paths.values())
    max_time = min(max_time, longest + 4 * len(agents) + 8)

    by_id = {a.agent_id: a for a in agents}
    tie = itertools.count()
    root_conflicts = find_conflicts(root_paths)
    root = _CTNode(
        sum(root_costs.values()),
        len(root_conflicts),
        next(tie),
        frozenset(),
        root_paths,
        root_costs,
        root_conflicts,
    )
    open_nodes: list[_CTNode] = [root]
    heapq.heapify(open_nodes)

    expanded = 0
    branched: list[Conflict] = []

    while open_nodes:
        if time_budget_seconds is not None and (monotonic() - started_at > time_budget_seconds):
            return PlanningOutcome.failed(
                FailureMode.TIMEOUT,
                f"CBS exceeded its {time_budget_seconds:.0f}s wall-clock budget after "
                f"{expanded} constraint-tree nodes. The fleet is not deconflicted and "
                "no routes are returned.",
                high_level_nodes=expanded,
                low_level_calls=low_level_calls,
                elapsed_seconds=round(monotonic() - started_at, 2),
                agents=ids,
            )
        if expanded >= max_high_level_nodes:
            return PlanningOutcome.failed(
                FailureMode.CONFLICT_UNRESOLVED,
                f"CBS expanded its budget of {max_high_level_nodes} constraint-tree "
                "nodes without deconflicting the fleet. The routes returned by the "
                "last consistent node are not collision-free and are not returned.",
                high_level_nodes=expanded,
                low_level_calls=low_level_calls,
                agents=ids,
            )

        node = heapq.heappop(open_nodes)
        expanded += 1

        conflicts = node.conflicts
        if not conflicts:
            return PlanningOutcome.success(
                None,
                solution=CBSSolution(
                    paths=node.paths,
                    costs=node.costs,
                    high_level_nodes=expanded,
                    low_level_calls=low_level_calls,
                    conflicts_branched=branched,
                ),
            )

        conflict = conflicts[0]
        branched.append(conflict)

        if conflict.kind == "vertex":
            # Disjoint split on the first agent: one child requires it to be
            # there, the other forbids it. The branches partition the solutions.
            chosen = conflict.agents[0]
            branches: list[tuple[str, Constraint]] = [
                (chosen, VertexConstraint(chosen, conflict.cell, conflict.time, positive=True)),
                (chosen, VertexConstraint(chosen, conflict.cell, conflict.time, positive=False)),
            ]
        else:
            # Edge conflicts keep the standard split: each agent is forbidden
            # its own direction of the swap. A positive edge constraint would
            # need its own representation and buys little - a swap has only two
            # participants and no third route through it.
            branches = []
            for agent_id in conflict.agents:
                path = node.paths[agent_id]
                frm = _position_at(path, conflict.time - 1)
                to = _position_at(path, conflict.time)
                branches.append((agent_id, EdgeConstraint(agent_id, frm, to, conflict.time)))

        for agent_id, new_constraint in branches:
            child_constraints = node.constraints | {new_constraint}
            horizon = max(len(p) for p in node.paths.values())
            result = plan_with_constraints(
                by_id[agent_id],
                child_constraints,
                max_time,
                avoid=conflict_avoidance_table(node.paths, agent_id, horizon),
                adjacency=adjacency[agent_id],
            )
            low_level_calls += 1
            if result is None:
                continue  # this branch is infeasible; the sibling may not be

            path, cost, _ = result
            child_paths = dict(node.paths)
            child_costs = dict(node.costs)
            child_paths[agent_id] = path
            child_costs[agent_id] = cost

            # A positive constraint on one agent is a negative constraint on
            # every other, so they all have to be re-planned, not just the one
            # the branch names.
            if isinstance(new_constraint, VertexConstraint) and new_constraint.positive:
                infeasible = False
                for other in agents:
                    if other.agent_id == agent_id:
                        continue
                    replanned = plan_with_constraints(
                        other,
                        child_constraints,
                        max_time,
                        avoid=conflict_avoidance_table(child_paths, other.agent_id, horizon),
                    )
                    low_level_calls += 1
                    if replanned is None:
                        infeasible = True
                        break
                    child_paths[other.agent_id] = replanned[0]
                    child_costs[other.agent_id] = replanned[1]
                if infeasible:
                    continue

            child_conflicts = find_conflicts(child_paths)
            heapq.heappush(
                open_nodes,
                _CTNode(
                    sum(child_costs.values()),
                    len(child_conflicts),
                    next(tie),
                    child_constraints,
                    child_paths,
                    child_costs,
                    child_conflicts,
                ),
            )

    return PlanningOutcome.failed(
        FailureMode.CONFLICT_UNRESOLVED,
        "The constraint tree was exhausted without a collision-free assignment; "
        "with these starts and goals the fleet cannot be deconflicted on this terrain.",
        high_level_nodes=expanded,
        low_level_calls=low_level_calls,
        agents=ids,
    )
