"""End-to-end API tests against a live PostgreSQL instance.

These are skipped unless a database is reachable, because the models use
PostgreSQL-specific column types (``JSONB``, native ``UUID``) that SQLite cannot
emulate. Swapping to a generic JSON column purely so the tests could run
in-memory would mean the suite exercised a schema the application never uses -
worse than skipping honestly.

Run them with the compose stack up::

    docker compose up -d db
    cd backend && pytest tests/test_api.py -v

The pure-computation suites (planner, risk, hazard, report) have no such
dependency and run everywhere.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg2://astra:astra@localhost:5432/astrasynth"
)


def _database_available() -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(), reason=f"PostgreSQL not reachable at {DATABASE_URL}"
)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ["STORAGE_DIR"] = str(tmp_path_factory.mktemp("storage"))
    os.environ["ANTHROPIC_API_KEY"] = ""  # force the deterministic fallback

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def terrain_bytes() -> bytes:
    import cv2

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
    from generate_terrain import generate

    return cv2.imencode(".png", generate("crater_field", 256, seed=99))[1].tobytes()


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_rover_configs_are_seeded(client):
    configs = client.get("/rover-configs").json()
    assert len(configs) >= 3
    assert all(config["battery_capacity_kwh"] > 0 for config in configs)


def test_rejects_a_non_image_upload(client):
    response = client.post(
        "/missions",
        data={"name": "bad"},
        files={"terrain_image": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
    )
    assert response.status_code == 415


def test_full_mission_pipeline(client, terrain_bytes):
    """Create -> analyse -> plan -> assess -> report, in order."""
    created = client.post(
        "/missions",
        data={"name": "Integration traverse", "terrain_source": "synthetic"},
        files={"terrain_image": ("terrain.png", terrain_bytes, "image/png")},
    )
    assert created.status_code == 201
    mission = created.json()
    mission_id = mission["id"]
    assert mission["status"] == "PENDING"
    assert mission["terrain_image_url"].startswith("/static/")
    assert "terrain_image_path" not in mission  # server paths must not leak

    # Planning before analysis is a conflict, not a crash.
    rover_id = client.get("/rover-configs").json()[1]["id"]
    premature = client.post(
        f"/missions/{mission_id}/plan-path",
        json={"start": {"x": 10, "y": 10}, "end": {"x": 240, "y": 240}, "rover_config_id": rover_id},
    )
    assert premature.status_code == 409

    analysis = client.post(f"/missions/{mission_id}/analyze-terrain")
    assert analysis.status_code == 200
    analysis_body = analysis.json()
    assert analysis_body["terrain_classification"] in {
        "rocky_highland", "sandy_plain", "crater_field"
    }
    assert analysis_body["hazard_heatmap_url"].startswith("/static/")
    assert "arrays_path" not in analysis_body["analysis_metadata"]  # internal path stripped

    path = client.post(
        f"/missions/{mission_id}/plan-path",
        json={"start": {"x": 10, "y": 10}, "end": {"x": 240, "y": 240}, "rover_config_id": rover_id},
    )
    assert path.status_code == 200
    path_body = path.json()
    assert path_body["algorithm_used"] == "A_star"
    assert len(path_body["waypoints"]) > 2
    assert path_body["total_distance_m"] > 0
    assert path_body["planner_metadata"]["heuristic_admissible"] is True

    risk = client.post(f"/missions/{mission_id}/assess-risk", json={})
    assert risk.status_code == 200
    risk_body = risk.json()
    assert risk_body["risk_score"] in {"LOW", "MEDIUM", "HIGH"}
    assert risk_body["feasibility"] in {"FEASIBLE", "FEASIBLE_WITH_MARGIN", "INFEASIBLE"}
    assert risk_body["ai_narrative"] is None  # no model call at this stage
    assert "calculation_basis" in risk_body["structured_context"]

    report = client.post(f"/missions/{mission_id}/generate-report")
    assert report.status_code == 200
    report_body = report.json()
    # No API key is configured, so this must be the fallback - and must still
    # be a complete report rather than an error.
    assert report_body["narrative_source"] == "template_fallback"
    assert report_body["ai_narrative"]["summary"]
    assert report_body["ai_narrative"]["recommendation"]
    assert report_body["structured_context"] == risk_body["structured_context"]

    assert client.get(f"/missions/{mission_id}").json()["status"] == "REPORT_GENERATED"


def test_unreachable_goal_returns_422_not_500(client, terrain_bytes):
    """A rover that cannot climb anything must fail cleanly."""
    mission_id = client.post(
        "/missions",
        data={"name": "Impossible traverse"},
        files={"terrain_image": ("terrain.png", terrain_bytes, "image/png")},
    ).json()["id"]
    client.post(f"/missions/{mission_id}/analyze-terrain")

    strict_rover = client.post(
        "/rover-configs",
        json={
            "name": "Test brittle rover",
            "battery_capacity_kwh": 5.0,
            "max_traversable_slope_deg": 0.05,  # effectively cannot climb
            "energy_per_meter_kwh": 0.003,
        },
    ).json()

    response = client.post(
        f"/missions/{mission_id}/plan-path",
        json={
            "start": {"x": 10, "y": 10},
            "end": {"x": 240, "y": 240},
            "rover_config_id": strict_rover["id"],
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "no_traversable_path"


def test_unknown_mission_returns_404(client):
    assert client.get("/missions/00000000-0000-0000-0000-000000000000").status_code == 404
