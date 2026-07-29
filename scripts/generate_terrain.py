#!/usr/bin/env python3
"""Synthetic planetary terrain generator.

Real Mars DEM tiles are large and require a network fetch, so the repository
ships a generator instead: it produces terrain with the statistical character
the analysis pipeline is designed for (fractal roughness plus discrete impact
craters) so the whole system can be exercised offline, in CI, and in tests.

This is a *substitute for the data*, not a substitute for the algorithms - the
CV pipeline that runs on this output is the same code that runs on a real USGS
tile. See ``scripts/download_usgs_terrain.py`` for the real thing.

Usage::

    python scripts/generate_terrain.py --preset crater_field --out data/sample_terrain/
    python scripts/generate_terrain.py --preset all --size 640
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

try:
    import cv2
except ImportError as exc:  # pragma: no cover
    raise SystemExit("opencv-python-headless is required: pip install opencv-python-headless") from exc


def diamond_square(size: int, roughness: float, rng: np.random.Generator) -> np.ndarray:
    """Fractal heightfield via the diamond-square algorithm.

    Chosen over plain Gaussian noise because real terrain is self-similar
    across scales: large landforms with smaller features riding on them. White
    noise would give the roughness metric nothing meaningful to distinguish.
    """
    n = 1
    while n + 1 < size:
        n *= 2
    grid_size = n + 1
    grid = np.zeros((grid_size, grid_size), dtype=np.float64)

    grid[0, 0] = rng.normal()
    grid[0, -1] = rng.normal()
    grid[-1, 0] = rng.normal()
    grid[-1, -1] = rng.normal()

    step = n
    scale = 1.0
    while step > 1:
        half = step // 2

        # Diamond step: centre of each square from its four corners.
        for y in range(0, grid_size - 1, step):
            for x in range(0, grid_size - 1, step):
                average = (
                    grid[y, x] + grid[y, x + step] + grid[y + step, x] + grid[y + step, x + step]
                ) / 4.0
                grid[y + half, x + half] = average + rng.normal() * scale

        # Square step: edge midpoints from surrounding diamond centres.
        for y in range(0, grid_size, half):
            start = half if (y // half) % 2 == 0 else 0
            for x in range(start, grid_size, step):
                total, count = 0.0, 0
                for dy, dx in ((-half, 0), (half, 0), (0, -half), (0, half)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < grid_size and 0 <= nx < grid_size:
                        total += grid[ny, nx]
                        count += 1
                grid[y, x] = total / count + rng.normal() * scale

        step = half
        scale *= roughness

    return cv2.resize(grid, (size, size), interpolation=cv2.INTER_LINEAR)


def add_craters(
    heightfield: np.ndarray, count: int, rng: np.random.Generator, depth: float = 0.35
) -> np.ndarray:
    """Stamp bowl-shaped depressions with raised rims.

    The raised rim matters: it is the high-gradient ring that Canny actually
    detects. A bowl with no rim produces almost no edge response, and the
    obstacle detector would find nothing.
    """
    size = heightfield.shape[0]
    result = heightfield.copy()
    yy, xx = np.mgrid[0:size, 0:size]

    for _ in range(count):
        radius = rng.uniform(size * 0.03, size * 0.11)
        cx = rng.uniform(radius, size - radius)
        cy = rng.uniform(radius, size - radius)
        distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)

        inside = distance < radius
        bowl = -depth * (1.0 - (distance / radius) ** 2)
        result[inside] += bowl[inside]

        rim = (distance >= radius) & (distance < radius * 1.22)
        rim_profile = depth * 0.55 * np.exp(-((distance - radius) / (radius * 0.1)) ** 2)
        result[rim] += rim_profile[rim]

    return result


def add_rock_field(
    heightfield: np.ndarray, count: int, rng: np.random.Generator
) -> np.ndarray:
    """Small high-frequency bumps - the rock clusters a rover must route around."""
    size = heightfield.shape[0]
    result = heightfield.copy()
    yy, xx = np.mgrid[0:size, 0:size]

    for _ in range(count):
        radius = rng.uniform(size * 0.006, size * 0.022)
        cx = rng.uniform(0, size)
        cy = rng.uniform(0, size)
        distance = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        result += rng.uniform(0.08, 0.3) * np.exp(-((distance / radius) ** 2))

    return result


def normalise_to_u8(heightfield: np.ndarray) -> np.ndarray:
    low, high = float(heightfield.min()), float(heightfield.max())
    if high - low < 1e-9:
        return np.zeros(heightfield.shape, dtype=np.uint8)
    return (((heightfield - low) / (high - low)) * 255).astype(np.uint8)


PRESETS: dict[str, dict] = {
    "sandy_plain": {
        "roughness": 0.38,
        "craters": 1,
        "rocks": 4,
        "smooth": 9,
        "description": "Low relief, few obstacles - should classify as sandy_plain.",
    },
    "rocky_highland": {
        "roughness": 0.62,
        "craters": 3,
        "rocks": 70,
        "smooth": 3,
        "description": "High relief with dense rock scatter - should classify as rocky_highland.",
    },
    "crater_field": {
        "roughness": 0.5,
        "craters": 22,
        "rocks": 25,
        "smooth": 3,
        "description": "Heavy impact cratering - should classify as crater_field.",
    },
}


def generate(preset: str, size: int, seed: int) -> np.ndarray:
    config = PRESETS[preset]
    rng = np.random.default_rng(seed)

    heightfield = diamond_square(size, config["roughness"], rng)
    heightfield = add_craters(heightfield, config["craters"], rng)
    heightfield = add_rock_field(heightfield, config["rocks"], rng)

    smooth = config["smooth"]
    if smooth > 1:
        if smooth % 2 == 0:
            smooth += 1
        heightfield = cv2.GaussianBlur(heightfield, (smooth, smooth), 0)

    return normalise_to_u8(heightfield)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--preset", default="all", choices=[*PRESETS.keys(), "all"], help="Terrain type"
    )
    parser.add_argument("--size", type=int, default=512, help="Output size in pixels")
    parser.add_argument("--seed", type=int, default=42, help="RNG seed (output is reproducible)")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "sample_terrain",
    )
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    presets = list(PRESETS) if args.preset == "all" else [args.preset]

    for index, preset in enumerate(presets):
        image = generate(preset, args.size, args.seed + index)
        destination = args.out / f"synthetic_{preset}_{args.size}.png"
        cv2.imwrite(str(destination), image)
        print(f"{destination}  ({PRESETS[preset]['description']})")


if __name__ == "__main__":
    main()
