"""Route and mission planning.

``grid.py`` holds the cost model; every module beside it holds one search
strategy over that model.

* ``astar``       - A*/Dijkstra, optimal, the baseline everything is measured against
* ``theta_star``  - any-angle, shorter and straighter, explicitly not optimal
* ``dstar_lite``  - incremental repair when the belief map changes mid-traverse
* ``cbs``         - conflict-based search for a heterogeneous multi-rover fleet
* ``pareto``      - the trade-off surface between distance, energy and risk
* ``outcome``     - why a plan failed, in a form a caller can act on
"""

from app.services.planning.grid import (
    Cell,
    EdgeBlock,
    PathNotFoundError,
    PlannedPath,
    PlanningGrid,
    RoverSpec,
    Waypoint,
    energy_factor,
)
from app.services.planning.outcome import FailureMode, PlanningOutcome

__all__ = [
    "Cell",
    "EdgeBlock",
    "FailureMode",
    "PathNotFoundError",
    "PlannedPath",
    "PlanningGrid",
    "PlanningOutcome",
    "RoverSpec",
    "Waypoint",
    "energy_factor",
]
