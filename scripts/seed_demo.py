#!/usr/bin/env python3
"""Fill a running AstraSynth API with real runs, so every console page has something to show.

Drives the public HTTP API only, the same calls the frontend makes, against the
synthetic tiles in data/sample_terrain/. Every number it leaves behind was
computed by the API; nothing is written to the database directly.

    python scripts/seed_demo.py                         # API on localhost:8000
    python scripts/seed_demo.py --api http://host:8000  # somewhere else
    python scripts/seed_demo.py --quick                 # fewer trials

Missions are matched by name, so a second run adds runs to the same missions
instead of duplicating them. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TILES = ROOT / "data" / "sample_terrain"

SOURCE = "Synthetic (scripts/generate_terrain.py)"
MISSIONS = [
    ("Crater Field Approach", "synthetic_crater_field_512.png"),
    ("Rocky Highland Transit", "synthetic_rocky_highland_512.png"),
    ("Sandy Plain Survey", "synthetic_sandy_plain_512.png"),
]


class Api:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def call(
        self,
        method: str,
        path: str,
        body: dict | None = None,
        raw: bytes | None = None,
        headers: dict | None = None,
    ):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        for key, value in (headers or {}).items():
            request.add_header(key, value)
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                text = response.read().decode()
                return json.loads(text) if text else None
        except urllib.error.HTTPError as exc:
            raise SystemExit(
                f"{method} {path} -> {exc.code}: {exc.read().decode()[:400]}"
            ) from None

    def upload(self, name: str, source: str, image: Path) -> dict:
        boundary = uuid.uuid4().hex
        parts = []
        for field, value in (("name", name), ("terrain_source", source)):
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"\r\n\r\n{value}\r\n'.encode()
            )
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="terrain_image"; filename="{image.name}"\r\n'
            "Content-Type: image/png\r\n\r\n".encode()
            + image.read_bytes()
            + b"\r\n"
        )
        parts.append(f"--{boundary}--\r\n".encode())
        return self.call(
            "POST",
            "/missions",
            raw=b"".join(parts),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )


def corners(api: Api, mission_id: str, rover_id: str) -> tuple[dict, dict, dict]:
    """The start and goal mission control defaults to, 5% in from opposite corners."""
    grid = api.call(
        "GET", f"/missions/{mission_id}/terrain-grid?max_dim=128&rover_config_id={rover_id}"
    )
    edge = max(2, round(grid["rows"] * 0.05))

    def px(cells: float) -> int:
        return round(cells * grid["pixel_scale"])

    start = {"x": px(edge), "y": px(edge)}
    goal = {"x": px(grid["cols"] - 1 - edge), "y": px(grid["rows"] - 1 - edge)}
    return start, goal, grid


def step(label: str, action, *args):
    started = time.time()
    result = action(*args)
    print(f"  {label:<44} {time.time() - started:6.1f} s")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--quick", action="store_true", help="fewer Monte Carlo trials")
    args = parser.parse_args()
    api = Api(args.api)
    trials = 16 if args.quick else 40

    try:
        api.call("GET", "/health")
    except (OSError, SystemExit) as exc:
        print(f"No API at {args.api}: {exc}", file=sys.stderr)
        return 1

    # The three the API seeds on startup; a custom configuration never shadows them.
    rovers = {r["name"]: r["id"] for r in api.call("GET", "/rover-configs")}
    try:
        scout, survey, heavy = (
            rovers[n] for n in ("Scout-Class (light)", "Survey-Class (medium)", "Heavy Lab-Class")
        )
    except KeyError as missing:
        print(
            f"Seeded rover configuration {missing} not found; start the API once to seed it.",
            file=sys.stderr,
        )
        return 1
    existing = {m["name"]: m for m in api.call("GET", "/missions?limit=200")}

    for index, (name, tile) in enumerate(MISSIONS):
        print(name)
        mission = existing.get(name) or step(
            "create mission", api.upload, name, SOURCE, TILES / tile
        )
        mid = mission["id"]
        if mission["status"] == "PENDING":
            step("analyse terrain", api.call, "POST", f"/missions/{mid}/analyze-terrain")
        start, goal, grid = corners(api, mid, survey)

        for rover, seed, unmapped in (
            (survey, 1 + index, 6),
            (scout, 11 + index, 6),
            (heavy, 21 + index, 24),
        ):
            body = {
                "start": start,
                "goal": goal,
                "rover_config_id": rover,
                "seed": seed,
                "unmapped_obstacles": unmapped,
            }
            run = step(
                f"traverse seed {seed}, {unmapped} unmapped obstacles",
                api.call,
                "POST",
                f"/missions/{mid}/simulate-traverse",
                body,
            )
            print(
                f"    -> {run['status']} {run['failure_mode'] or ''} {run['distance_m']:.0f} m, {run['replans']} replans"
            )

        route = {"start": start, "goal": goal, "rover_config_id": survey}
        step("route study", api.call, "POST", f"/missions/{mid}/route-study", route)
        study = {**route, "trials": trials, "seed": 7 + index}
        step(
            f"robustness study, {trials} trials",
            api.call,
            "POST",
            f"/missions/{mid}/monte-carlo",
            study,
        )

        if index == 0:
            mid_x = round(grid["cols"] / 2 * grid["pixel_scale"])
            mid_y = round(grid["rows"] / 2 * grid["pixel_scale"])
            far_x, far_y = goal["x"], goal["y"]
            assignments = [
                {"label": "SURVEY", "rover_config_id": survey, "start": start, "goal": goal},
                {
                    "label": "SCOUT",
                    "rover_config_id": scout,
                    "start": {"x": far_x, "y": start["y"]},
                    "goal": {"x": start["x"], "y": far_y},
                },
                {
                    "label": "HEAVY",
                    "rover_config_id": heavy,
                    "start": {"x": start["x"], "y": mid_y},
                    "goal": {"x": far_x, "y": mid_y},
                },
            ]
            step(
                "fleet deconfliction, 3 rovers",
                api.call,
                "POST",
                f"/missions/{mid}/fleet-plan",
                {"assignments": assignments},
            )

            targets = api.call("GET", f"/missions/{mid}/science-targets")
            if not targets:
                for label, x, y, value, instrument in (
                    ("Ejecta block", mid_x, start["y"] + (mid_y - start["y"]) // 2, 0.8, "camera"),
                    (
                        "Crater rim outcrop",
                        mid_x + (far_x - mid_x) // 2,
                        mid_y,
                        0.9,
                        "spectrometer",
                    ),
                    (
                        "Layered deposit",
                        start["x"] + (mid_x - start["x"]) // 2,
                        mid_y + (far_y - mid_y) // 2,
                        0.6,
                        None,
                    ),
                ):
                    api.call(
                        "POST",
                        f"/missions/{mid}/science-targets",
                        {
                            "label": label,
                            "x": x,
                            "y": y,
                            "value": value,
                            "priority": "high" if value > 0.75 else "medium",
                            "required_instrument": instrument,
                        },
                    )
            tour = {
                "start": start,
                "rover_config_id": survey,
                "instruments": ["camera", "spectrometer"],
            }
            step("science tour", api.call, "POST", f"/missions/{mid}/science-tour", tour)

            job = api.call(
                "POST",
                f"/missions/{mid}/monte-carlo/jobs",
                {
                    "start": start,
                    "goal": goal,
                    "rover_config_id": survey,
                    "trials": 120 if args.quick else 300,
                    "seed": 42,
                },
            )
            print(
                f"  queued robustness job {job['id'][:8]} ({job['status']}); a worker picks it up"
            )

    print("\nDone. Open http://localhost:5173 to see it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
