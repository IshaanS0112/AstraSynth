"""Build every 3-D asset the app ships, with Blender as a Python module.

    pip install -r assets/blender/requirements.txt
    python assets/blender/build.py                 # everything
    python assets/blender/build.py rover studio    # just these steps
    python assets/blender/build.py --quick         # low samples, for iterating

Outputs are written into the repo, so a build is a diff you can review:
frontend/public/3d/ for what the app loads, docs/images/renders/ for stills.
Stills are rendered losslessly to a temporary directory and committed as WebP
(which keeps the studio shots' transparency) or JPEG, so the repo stays light.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import kit  # noqa: E402

REPO = HERE.parent.parent
PUBLIC = REPO / "frontend" / "public" / "3d"
RENDERS = REPO / "docs" / "images" / "renders"


def step_rover(quick: bool) -> dict:
    import rover

    kit.reset()
    objects = rover.build()
    tris = kit.triangle_count(objects)
    PUBLIC.mkdir(parents=True, exist_ok=True)
    out = PUBLIC / "rover.glb"
    kit.export_glb(str(out), objects)
    return {"rover.glb": {"bytes": out.stat().st_size, "triangles": tris}}


def web_copy(source: Path, target: Path, width: int, quality: int) -> int:
    """A compressed copy of a still for the app: WebP keeps the alpha, JPEG does not need it."""
    import bpy

    image = bpy.data.images.load(str(source))
    w, h = image.size
    image.scale(width, round(h * width / w))
    image.file_format = "WEBP" if target.suffix == ".webp" else "JPEG"
    image.save(filepath=str(target), quality=quality)
    bpy.data.images.remove(image)
    return target.stat().st_size


def step_studio(quick: bool) -> dict:
    import rover
    import studio

    RENDERS.mkdir(parents=True, exist_ok=True)
    results = {}
    shots = {
        "rover-studio": ((4.6, -4.3, 2.3), (0.1, 0, 0.85), 50),
        "rover-profile": ((0.3, -7.6, 1.35), (0.05, 0, 1.08), 50),
    }
    width = 800 if quick else 1600
    with tempfile.TemporaryDirectory() as scratch:
        for name, (location, target, lens) in shots.items():
            kit.reset()
            rover.build()
            studio.cycles(24 if quick else 160, width, 520 if quick else 1040, transparent=True)
            studio.studio_scene(transparent=True)
            studio.camera(location, target, lens)
            lossless = Path(scratch) / f"{name}.png"
            studio.render(str(lossless))
            still = RENDERS / f"{name}.webp"
            results[still.name] = {"bytes": web_copy(lossless, still, width, 92)}
            if name == "rover-studio":
                # The fleet page shows this one over the console's dark surface.
                web = PUBLIC / "rover-studio.webp"
                results[web.name] = {"bytes": web_copy(lossless, web, 1200, 88)}
    return results


def step_beacon(quick: bool) -> dict:
    import beacon

    kit.reset()
    objects = beacon.build()
    out = PUBLIC / "beacon.glb"
    kit.export_glb(str(out), objects)
    return {"beacon.glb": {"bytes": out.stat().st_size, "triangles": kit.triangle_count(objects)}}


def step_textures(quick: bool) -> dict:
    import surface

    size = 512 if quick else 1024
    results = surface.bake_set(surface.regolith_material, "regolith", PUBLIC, size, size, quick)
    results |= surface.bake_set(surface.strata_material, "strata", PUBLIC, size, size // 2, quick)
    return results


def step_sky(quick: bool) -> dict:
    import sky

    out = PUBLIC / "mars_sky.hdr"
    info = sky.render_hdr(out, 512 if quick else 1024, 256 if quick else 512)
    sky.write_sidecar(PUBLIC / "sky.json", info)
    if info["agreement"] < 0.995:
        raise RuntimeError(f"sky sun lands in the wrong place: {info}")
    return {"mars_sky.hdr": {"bytes": out.stat().st_size, **info}}


def step_hero(quick: bool) -> dict:
    import renders

    RENDERS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as scratch:
        lossless = Path(scratch) / "hero.png"
        result = {"render": renders.hero(lossless, quick)}
        still = RENDERS / "hero.jpg"
        result[still.name] = {"bytes": web_copy(lossless, still, 960 if quick else 1920, 92)}
        # The overview banner.
        banner = PUBLIC / "hero.jpg"
        result[banner.name] = {"bytes": web_copy(lossless, banner, 960 if quick else 1600, 86)}
    return result


STEPS = {
    "rover": step_rover,
    "beacon": step_beacon,
    "textures": step_textures,
    "sky": step_sky,
    "studio": step_studio,
    "hero": step_hero,
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("steps", nargs="*", help=f"steps to run, from {', '.join(STEPS)} (default: all)")
    parser.add_argument("--quick", action="store_true", help="low samples and resolution")
    args = parser.parse_args()
    unknown = [s for s in args.steps if s not in STEPS]
    if unknown:
        parser.error(f"unknown step(s): {', '.join(unknown)}")
    steps = args.steps or list(STEPS)
    report = {}
    for name in steps:
        started = time.time()
        report[name] = STEPS[name](args.quick)
        report[name]["seconds"] = round(time.time() - started, 1)
        print(json.dumps({name: report[name]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
