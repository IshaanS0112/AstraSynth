"""Shared fixtures.

Every test in this suite runs against the pure analysis functions, with no
database and no network. That is deliberate: the claims this project makes
(hazard is computed, A* is optimal, feasibility is arithmetic, the report
degrades without an API key) are all claims about those functions, so they
should be verifiable by anyone who clones the repo and runs ``pytest``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import Settings  # noqa: E402
from app.services.path_planner import RoverSpec  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def rover() -> RoverSpec:
    """Survey-class reference rover (mirrors the seeded config)."""
    return RoverSpec(
        battery_capacity_kwh=6.0,
        max_traversable_slope_deg=25.0,
        energy_per_meter_kwh=0.0030,
    )


@pytest.fixture
def flat_grid() -> tuple[np.ndarray, np.ndarray]:
    """20x20 zero-hazard, zero-relief terrain. The analytically solvable case."""
    return np.zeros((20, 20), dtype=np.float32), np.zeros((20, 20), dtype=np.float32)


@pytest.fixture
def walled_grid() -> tuple[np.ndarray, np.ndarray]:
    """Flat terrain bisected by a cliff with a single gap at the bottom.

    The cliff is far steeper than any rover's limit, so a correct planner must
    detour through the gap rather than crossing it.
    """
    hazard = np.zeros((20, 20), dtype=np.float32)
    elevation = np.zeros((20, 20), dtype=np.float32)
    elevation[:18, 10] = 500.0  # 500 m step - impassable by any rover
    return hazard, elevation


@pytest.fixture
def synthetic_terrain_path() -> Path:
    """A generated terrain image, created on demand so the repo ships no binaries."""
    root = Path(__file__).resolve().parent.parent.parent
    sys.path.insert(0, str(root / "scripts"))
    import cv2
    from generate_terrain import generate

    destination = Path(__file__).parent / "_fixture_terrain.png"
    if not destination.exists():
        cv2.imwrite(str(destination), generate("crater_field", 256, seed=7))
    return destination
