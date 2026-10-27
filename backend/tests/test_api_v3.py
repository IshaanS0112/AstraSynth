"""API tests for the mission-autonomy endpoints."""

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
    os.environ["STORAGE_DIR"] = str(tmp_path_factory.mktemp("storage_v3"))
    os.environ["ANTHROPIC_API_KEY"] = ""

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

    return cv2.imencode(".png", generate("crater_field", 192, seed=21))[1].tobytes()


@pytest.fixture(scope="module")
def rover_id(client) -> str:
    configs = client.get("/rover-configs").json()
    survey = next(c for c in configs if "Survey" in c["name"])
    return survey["id"]


@pytest.fixture(scope="module")
def mission(client, terrain_bytes) -> dict:
    """An analysed mission. Everything in this module needs one."""
    response = client.post(
        "/missions",
        data={"name": "V3 API mission", "terrain_source": "synthetic"},
        files={"terrain_image": ("terrain.png", terrain_bytes, "image/png")},
    )
    assert response.status_code == 201, response.text
    created = response.json()
    analysed = client.post(f"/missions/{created['id']}/analyze-terrain")
    assert analysed.status_code == 200, analysed.text
    return created


@pytest.fixture(scope="module")
def grid(client, mission) -> dict:
    response = client.get(f"/missions/{mission['id']}/terrain-grid?max_dim=48")
    assert response.status_code == 200, response.text
    return response.json()


def pixel(grid: dict, row: int, col: int) -> dict:
    """A planning cell as the image-pixel point the API expects."""
    scale = grid["pixel_scale"]
    return {"x": int(round(col * scale)), "y": int(round(row * scale))}


def free_pixel(grid: dict, preferred: tuple[int, int]) -> dict:
    """The nearest non-lethal cell, so a test never starts inside a crater."""
    lethal = grid["layers"]["lethal"]
    rows, cols = grid["rows"], grid["cols"]
    for radius in range(max(rows, cols)):
        for d_row in range(-radius, radius + 1):
            for d_col in range(-radius, radius + 1):
                row, col = preferred[0] + d_row, preferred[1] + d_col
                if 0 <= row < rows and 0 <= col < cols and not lethal[row][col]:
                    return pixel(grid, row, col)
    raise AssertionError("the whole grid is lethal")


class TestTerrainGrid:
    def test_every_layer_has_the_declared_shape(self, grid):
        for name, layer in grid["layers"].items():
            assert len(layer) == grid["rows"], name
            assert all(len(row) == grid["cols"] for row in layer), name

    def test_the_expected_layers_are_present(self, grid):
        assert set(grid["layers"]) == {
            "elevation_m",
            "hazard",
            "uncertainty",
            "slope_deg",
            "lethal",
        }

    def test_ranges_bound_the_data_they_describe(self, grid):
        for name in ("elevation_m", "hazard", "uncertainty", "slope_deg"):
            low, high = grid["ranges"][name]
            values = [v for row in grid["layers"][name] for v in row]
            assert low <= min(values) + 1e-6
            assert max(values) <= high + 1e-6

    def test_hazard_and_uncertainty_stay_on_their_scale(self, grid):
        for name in ("hazard", "uncertainty"):
            values = [v for row in grid["layers"][name] for v in row]
            assert min(values) >= 0.0
            assert max(values) <= 1.0

    def test_every_cell_over_the_threshold_is_drawn_lethal(self, grid):
        """One direction only: a sub-cell obstacle makes a cell lethal while its
        mean stays low, so lethal => over-threshold is deliberately not asserted."""
        threshold = grid["lethal_hazard_threshold"]
        for hazard_row, lethal_row in zip(
            grid["layers"]["hazard"], grid["layers"]["lethal"], strict=True
        ):
            for hazard, lethal in zip(hazard_row, lethal_row, strict=True):
                if hazard >= threshold:
                    assert lethal

    def test_resolution_is_capped_by_the_request(self, client, mission):
        response = client.get(f"/missions/{mission['id']}/terrain-grid?max_dim=32")
        payload = response.json()
        assert max(payload["rows"], payload["cols"]) <= 32

    def test_an_unanalysed_mission_is_a_409_not_a_500(self, client, terrain_bytes):
        created = client.post(
            "/missions",
            data={"name": "not analysed"},
            files={"terrain_image": ("t.png", terrain_bytes, "image/png")},
        ).json()
        response = client.get(f"/missions/{created['id']}/terrain-grid")
        assert response.status_code == 409
        assert "analyse" in response.json()["detail"].lower()


@pytest.fixture(scope="module")
def walled_mission(client) -> dict:
    """A two-pixel wall about 40 m tall: lethal, and far narrower than a display cell."""
    import cv2
    import numpy as np

    image = np.tile(np.linspace(90, 140, 192, dtype=np.float32), (192, 1)).astype(np.uint8)
    image[:, 95:97] = 255
    created = client.post(
        "/missions",
        data={"name": "walled", "terrain_source": "synthetic"},
        files={
            "terrain_image": ("wall.png", cv2.imencode(".png", image)[1].tobytes(), "image/png")
        },
    ).json()
    assert client.post(f"/missions/{created['id']}/analyze-terrain").status_code == 200
    return created


@pytest.fixture(scope="module")
def footprint_rover(client) -> dict:
    response = client.post(
        "/rover-configs",
        json={
            "name": "Footprint test rover",
            "battery_capacity_kwh": 6.0,
            "max_traversable_slope_deg": 25.0,
            "energy_per_meter_kwh": 0.003,
            "footprint_radius_m": 1.5,
            "clearance_radius_m": 12.0,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestCostmapLayers:
    def test_a_thin_wall_survives_into_the_display(self, client, walled_mission):
        grid = client.get(f"/missions/{walled_mission['id']}/terrain-grid?max_dim=48").json()
        lethal_rows = [i for i, row in enumerate(grid["layers"]["lethal"]) if any(row)]
        assert len(lethal_rows) >= grid["rows"] - 2, "the wall runs the full height of the map"

        threshold = grid["lethal_hazard_threshold"]
        averaged = [any(h >= threshold for h in row) for row in grid["layers"]["hazard"]]
        assert not all(averaged), "averaging alone would have broken the wall"

    def test_naming_a_rover_adds_its_costmap(self, client, walled_mission, footprint_rover):
        grid = client.get(
            f"/missions/{walled_mission['id']}/terrain-grid"
            f"?max_dim=48&rover_config_id={footprint_rover['id']}"
        ).json()
        assert {"clearance_cost", "costmap"} <= set(grid["layers"])
        assert grid["rover"]["clearance_radius_m"] == 12.0
        assert grid["costmap"]["inflation_radius_m"] == 12.0

        clearance = [v for row in grid["layers"]["clearance_cost"] for v in row]
        assert max(clearance) > 0.0, "the wall is inflated"
        assert min(clearance) >= 0.0 and max(clearance) <= 1.0

        for name in ("clearance_cost", "costmap"):
            low, high = grid["ranges"][name]
            values = [v for row in grid["layers"][name] for v in row]
            assert low <= min(values) + 1e-6 and max(values) <= high + 1e-6

    def test_no_layer_lowers_a_cost(self, client, walled_mission, footprint_rover):
        grid = client.get(
            f"/missions/{walled_mission['id']}/terrain-grid"
            f"?max_dim=48&rover_config_id={footprint_rover['id']}"
        ).json()
        for hazard_row, cost_row in zip(
            grid["layers"]["hazard"], grid["layers"]["costmap"], strict=True
        ):
            for hazard, cost in zip(hazard_row, cost_row, strict=True):
                assert cost >= hazard - 1e-4

    def test_without_a_rover_there_is_no_clearance_layer(self, client, walled_mission):
        grid = client.get(f"/missions/{walled_mission['id']}/terrain-grid?max_dim=48").json()
        assert "clearance_cost" not in grid["layers"]
        assert grid.get("rover") is None

    def test_clearance_must_cover_the_footprint(self, client):
        response = client.post(
            "/rover-configs",
            json={
                "name": "inside out",
                "battery_capacity_kwh": 6.0,
                "max_traversable_slope_deg": 25.0,
                "energy_per_meter_kwh": 0.003,
                "footprint_radius_m": 3.0,
                "clearance_radius_m": 1.0,
            },
        )
        assert response.status_code == 422


class TestScienceTargets:
    def test_create_list_and_delete(self, client, mission, grid):
        point = free_pixel(grid, (5, 5))
        created = client.post(
            f"/missions/{mission['id']}/science-targets",
            json={
                "label": "OUTCROP-A",
                "x": point["x"],
                "y": point["y"],
                "value": 0.9,
                "priority": "high",
                "required_instrument": "spectrometer",
            },
        )
        assert created.status_code == 201, created.text
        target = created.json()
        assert target["label"] == "OUTCROP-A"

        listed = client.get(f"/missions/{mission['id']}/science-targets").json()
        assert target["id"] in {t["id"] for t in listed}

        deleted = client.delete(f"/missions/{mission['id']}/science-targets/{target['id']}")
        assert deleted.status_code == 204
        remaining = client.get(f"/missions/{mission['id']}/science-targets").json()
        assert target["id"] not in {t["id"] for t in remaining}

    def test_out_of_range_value_is_rejected(self, client, mission):
        response = client.post(
            f"/missions/{mission['id']}/science-targets",
            json={"label": "BAD", "x": 1, "y": 1, "value": 4.0},
        )
        assert response.status_code == 422

    def test_a_target_from_another_mission_cannot_be_deleted(
        self, client, mission, grid, terrain_bytes
    ):
        point = free_pixel(grid, (6, 6))
        target = client.post(
            f"/missions/{mission['id']}/science-targets",
            json={"label": "MINE", "x": point["x"], "y": point["y"]},
        ).json()
        other = client.post(
            "/missions",
            data={"name": "other"},
            files={"terrain_image": ("t.png", terrain_bytes, "image/png")},
        ).json()

        response = client.delete(f"/missions/{other['id']}/science-targets/{target['id']}")
        assert response.status_code == 404


@pytest.fixture(scope="module")
def run(client, mission, grid, rover_id) -> dict:
    """One traverse the whole class inspects, so the simulation runs once."""
    response = client.post(
        f"/missions/{mission['id']}/simulate-traverse",
        json={
            "start": free_pixel(grid, (2, 2)),
            "goal": free_pixel(grid, (grid["rows"] - 3, grid["cols"] - 3)),
            "rover_config_id": rover_id,
            "seed": 7,
            "unmapped_obstacles": 5,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


class TestTraverse:
    def test_the_run_records_the_executive_that_drove_it(self, run):
        executive = run["executive"]
        assert executive["tree"]["type"] == "RecoveryNode"
        assert executive["ticks"] > 0
        assert isinstance(executive["recoveries"], list)
        names = [c["name"] for c in executive["tree"]["children"][1]["children"][1]["children"]]
        assert names == ["ReSense", "RelaxUncertainty", "BackUp"]

    def test_the_rover_reaches_the_goal_and_replans_on_the_way(self, run):
        assert run["status"] == "SUCCESS"
        assert run["distance_m"] > 0
        assert run["energy_kwh"] > 0
        assert run["replans"] > 0
        assert run["reroutes"] <= run["replans"]

    def test_waypoints_come_back_in_image_pixels(self, run, grid, mission):
        for point in run["executed_path"]:
            assert 0 <= point["x"] <= grid["cols"] * grid["pixel_scale"] + grid["pixel_scale"]
            assert 0 <= point["y"] <= grid["rows"] * grid["pixel_scale"] + grid["pixel_scale"]
        assert run["executed_path"][0] == run["start_point"]

    def test_the_synthetic_truth_is_declared_not_implied(self, run):
        """The response must not let a reader think the terrain was measured."""
        truth = run["parameters"]["truth_model"]
        assert truth["unmapped_obstacles"] == 5
        assert "synthesised" in truth["note"]
        assert run["parameters"]["seed"] == 7

    def test_the_same_seed_reproduces_the_run(self, client, mission, grid, rover_id, run):
        repeat = client.post(
            f"/missions/{mission['id']}/simulate-traverse",
            json={
                "start": run["start_point"],
                "goal": run["goal_point"],
                "rover_config_id": rover_id,
                "seed": 7,
                "unmapped_obstacles": 5,
            },
        ).json()
        assert repeat["executed_path"] == run["executed_path"]
        assert repeat["energy_kwh"] == run["energy_kwh"]

    def test_a_different_seed_changes_what_the_rover_meets(
        self, client, mission, grid, rover_id, run
    ):
        other = client.post(
            f"/missions/{mission['id']}/simulate-traverse",
            json={
                "start": run["start_point"],
                "goal": run["goal_point"],
                "rover_config_id": rover_id,
                "seed": 99,
                "unmapped_obstacles": 5,
            },
        ).json()
        assert other["parameters"]["seed"] == 99
        assert (other["executed_path"], other["energy_kwh"]) != (
            run["executed_path"],
            run["energy_kwh"],
        )

    def test_an_exhausted_battery_is_a_stored_failure_not_an_error(
        self, client, mission, grid, rover_id
    ):
        response = client.post(
            f"/missions/{mission['id']}/simulate-traverse",
            json={
                "start": free_pixel(grid, (2, 2)),
                "goal": free_pixel(grid, (grid["rows"] - 3, grid["cols"] - 3)),
                "rover_config_id": rover_id,
                "seed": 3,
                "energy_budget_kwh": 0.01,
            },
        )
        assert response.status_code == 200
        run = response.json()
        assert run["status"] == "FAILED"
        assert run["failure_mode"] == "LOW_BATTERY"
        assert run["reason"]

    def test_events_can_be_filtered_server_side(self, client, mission, run):
        all_events = client.get(f"/missions/{mission['id']}/traverses/{run['id']}/events").json()
        planner_only = client.get(
            f"/missions/{mission['id']}/traverses/{run['id']}/events?category=planner"
        ).json()

        assert all_events
        assert 0 < len(planner_only) <= len(all_events)
        assert {e["category"] for e in planner_only} == {"planner"}

    def test_the_list_view_omits_the_heavy_fields(self, client, mission, run):
        listed = client.get(f"/missions/{mission['id']}/traverses").json()
        summary = next(item for item in listed if item["id"] == run["id"])
        assert "executed_path" not in summary
        assert "events" not in summary
        assert summary["replans"] == run["replans"]

    def test_identical_start_and_goal_is_a_422(self, client, mission, grid, rover_id):
        point = free_pixel(grid, (4, 4))
        response = client.post(
            f"/missions/{mission['id']}/simulate-traverse",
            json={"start": point, "goal": point, "rover_config_id": rover_id},
        )
        assert response.status_code == 422

    def test_an_unknown_rover_is_a_409(self, client, mission, grid):
        response = client.post(
            f"/missions/{mission['id']}/simulate-traverse",
            json={
                "start": free_pixel(grid, (2, 2)),
                "goal": free_pixel(grid, (10, 10)),
                "rover_config_id": "00000000-0000-0000-0000-000000000000",
            },
        )
        assert response.status_code == 409


class TestExperiments:
    def test_route_study_returns_a_trade_off_set(self, client, mission, grid, rover_id):
        response = client.post(
            f"/missions/{mission['id']}/route-study",
            json={
                "start": free_pixel(grid, (2, 2)),
                "goal": free_pixel(grid, (grid["rows"] - 3, grid["cols"] - 3)),
                "rover_config_id": rover_id,
            },
        )
        assert response.status_code == 200, response.text
        experiment = response.json()
        assert experiment["kind"] == "route_study"
        assert experiment["code_revision"]

        front = experiment["result"]["front"]
        assert front
        for route in front:
            assert route["waypoints"]
            assert set(route["objectives"]) >= {"distance_m", "energy_kwh", "mean_hazard"}
        assert "convex hull" in experiment["result"]["method"]

    def test_monte_carlo_records_its_seed_and_returns_percentiles(
        self, client, mission, grid, rover_id
    ):
        response = client.post(
            f"/missions/{mission['id']}/monte-carlo",
            json={
                "start": free_pixel(grid, (2, 2)),
                "goal": free_pixel(grid, (grid["rows"] - 3, grid["cols"] - 3)),
                "rover_config_id": rover_id,
                "trials": 4,
                "seed": 11,
            },
        )
        assert response.status_code == 200, response.text
        experiment = response.json()
        assert experiment["seed"] == 11
        assert experiment["result"]["trials"] == 4
        assert 0.0 <= experiment["result"]["success_probability"] <= 1.0

    def test_an_oversized_study_is_rejected_before_any_work(self, client, mission, grid, rover_id):
        response = client.post(
            f"/missions/{mission['id']}/monte-carlo",
            json={
                "start": free_pixel(grid, (2, 2)),
                "goal": free_pixel(grid, (10, 10)),
                "rover_config_id": rover_id,
                "trials": 100000,
            },
        )
        assert response.status_code == 422

    def test_fleet_plan_returns_verified_conflict_free_routes(
        self, client, mission, grid, rover_id
    ):
        response = client.post(
            f"/missions/{mission['id']}/fleet-plan",
            json={
                "assignments": [
                    {
                        "label": "R1",
                        "rover_config_id": rover_id,
                        "start": free_pixel(grid, (2, 2)),
                        "goal": free_pixel(grid, (grid["rows"] - 3, grid["cols"] - 3)),
                    },
                    {
                        "label": "R2",
                        "rover_config_id": rover_id,
                        "start": free_pixel(grid, (grid["rows"] - 3, grid["cols"] - 3)),
                        "goal": free_pixel(grid, (2, 2)),
                    },
                ],
                "time_budget_seconds": 20.0,
            },
        )
        assert response.status_code == 200, response.text
        experiment = response.json()
        assert experiment["kind"] == "fleet_plan"

        if experiment["status"] == "SUCCESS":
            result = experiment["result"]
            assert result["verified_conflict_free"] is True
            assert result["residual_conflicts"] == []
            assert set(result["routes"]) == {"R1", "R2"}
        else:
            # A budget exhaustion is a finding, not an HTTP error - but it must
            # never come back with routes attached.
            assert experiment["failure_mode"] in {"CONFLICT_UNRESOLVED", "TIMEOUT"}
            assert "routes" not in experiment["result"]

    def test_a_science_tour_reports_why_it_skipped_a_target(self, client, mission, grid, rover_id):
        for index, instrument in enumerate([None, "drill"]):
            client.post(
                f"/missions/{mission['id']}/science-targets",
                json={
                    "label": f"TOUR-{index}",
                    **free_pixel(grid, (8 + index * 6, 8 + index * 6)),
                    "value": 0.7,
                    "required_instrument": instrument,
                },
            )

        response = client.post(
            f"/missions/{mission['id']}/science-tour",
            json={
                "start": free_pixel(grid, (2, 2)),
                "rover_config_id": rover_id,
                "instruments": ["camera"],
            },
        )
        assert response.status_code == 200, response.text
        experiment = response.json()
        if experiment["status"] == "SUCCESS":
            skipped = {s["target_id"]: s["reason"] for s in experiment["result"]["skipped"]}
            assert skipped.get("TOUR-1") == "instrument_not_carried"
        else:
            assert experiment["failure_mode"]

    def test_a_tour_without_targets_is_a_409(self, client, terrain_bytes, rover_id):
        created = client.post(
            "/missions",
            data={"name": "no targets"},
            files={"terrain_image": ("t.png", terrain_bytes, "image/png")},
        ).json()
        client.post(f"/missions/{created['id']}/analyze-terrain")

        response = client.post(
            f"/missions/{created['id']}/science-tour",
            json={"start": {"x": 4, "y": 4}, "rover_config_id": rover_id},
        )
        assert response.status_code == 409
        assert "science targets" in response.json()["detail"]

    def test_experiments_are_listed_and_filterable(self, client, mission):
        every = client.get(f"/missions/{mission['id']}/experiments").json()
        assert every
        studies = client.get(f"/missions/{mission['id']}/experiments?kind=route_study").json()
        assert studies
        assert {item["kind"] for item in studies} == {"route_study"}

    def test_an_experiment_from_another_mission_is_a_404(self, client, mission, terrain_bytes):
        experiment_id = client.get(f"/missions/{mission['id']}/experiments").json()[0]["id"]
        other = client.post(
            "/missions",
            data={"name": "other mission"},
            files={"terrain_image": ("t.png", terrain_bytes, "image/png")},
        ).json()

        response = client.get(f"/missions/{other['id']}/experiments/{experiment_id}")
        assert response.status_code == 404
