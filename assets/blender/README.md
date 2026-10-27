# Blender asset pipeline

Every 3-D asset the app ships is built here by script, with Blender running as a
Python module (`bpy`). Nothing is hand-modelled, so every asset can be rebuilt
from source and every change shows up as a diff.

```bash
python3.11 -m venv .venv-blender && . .venv-blender/bin/activate
pip install -r assets/blender/requirements.txt     # bpy 5.0.1, CPython 3.11 only

python assets/blender/build.py                     # everything, about 15 min on 4 CPU cores
python assets/blender/build.py rover studio        # just these steps
python assets/blender/build.py --quick             # low samples and resolution, for iterating
```

The outputs are committed, so the app runs without Blender installed. Rebuild only
when you change a script here.

## What it builds

| Step | Output | What it is | Time* |
|---|---|---|---|
| `rover` | `frontend/public/3d/rover.glb` | Six-wheeled rocker-bogie rover, 77,208 triangles, 2.9 MB | 2 s |
| `beacon` | `frontend/public/3d/beacon.glb` | Goal beacon: survey tripod, mast, panel and lamp, 3,500 triangles | <1 s |
| `textures` | `frontend/public/3d/regolith_*.jpg`, `strata_*.jpg` | Albedo, normal and roughness maps baked in Cycles from procedural shader graphs; tile without seams | 3.5 min |
| `sky` | `frontend/public/3d/mars_sky.hdr`, `sky.json` | Equirectangular HDR of a dusty Mars sky, plus the sun direction in three.js coordinates | 2 s |
| `studio` | `docs/images/renders/rover-*.webp`, `frontend/public/3d/rover-studio.webp` | Studio renders, 1600×1040 on a transparent background, and a smaller copy for the Fleet page | 2 min |
| `hero` | `docs/images/renders/hero.jpg`, `frontend/public/3d/hero.jpg` | The rover on Martian terrain, 1920×1080, 160 samples, denoised; a 1600 px copy for the Overview banner | 10 min |

Stills render losslessly to a temporary directory and are committed as WebP,
which keeps the studio shots' transparency, or JPEG. Three stills cost 660 kB in
the repo rather than 5 MB.

\*Measured on the 4-core Xeon CPU container the committed assets were built in. Cycles runs on CPU only here.

## How the web viewer uses them

- **`rover.glb`** carries named pivots: `body`, `suspension`, `mast_head`, `hga`,
  `steer_FL` and the other corners, each with its `wheel_XX` inside, and
  `status_light`. Mission control spins the wheels with distance travelled,
  steers the corner wheels, pans the mast and pulses the status light. If the
  file fails to load, the viewer falls back to its procedural rover.
- **`mars_sky.hdr`** lights the Surface view (image-based lighting through a
  PMREM), and `sky.json` places the sun so shadows agree with the sky. The build
  checks that the brightest pixel of the HDR lands where `sky.json` says the sun
  is (`agreement` ≥ 0.995), or it stops.
- **Regolith and strata maps** cover the terrain and the cliff sides of the
  terrain block. The albedo is sampled at two scales in the shader, so the tile
  never reads as a grid.
- **`hero.jpg` and `rover-studio.webp`** are the Overview banner and the Fleet page image.

## Conventions

- Built in Blender's frame: +X forward, +Z up, metres. The glTF exporter
  converts to three.js's +Y up, so +Y in Blender becomes −Z in the app.
- The sky renders through a panoramic camera rotated to match three.js's
  equirectangular mapping, which is why the sun check above is meaningful.
- Colour management is AgX. The Surface view tone-maps with AgX too, so a
  render and the live view agree.

## What these are not

- **The rover is not any real vehicle.** Its layout follows the six-wheeled
  rocker-bogie family; proportions and parts are illustrative.
- **The hero terrain is illustrative.** A mission tile at 2 m per pixel says
  nothing about ground at the scale of a 3 m rover, so the render builds its own
  heightfield. Mission terrain in the app always comes from the uploaded DEM.
- **The Surface view is a visual, not a data layer.** It is labelled that way in
  the app. Hazard, slope, uncertainty and clearance keep their own colour ramps.

## Files

| File | Role |
|---|---|
| `build.py` | Entry point: runs the steps, writes the outputs, prints a JSON line per step |
| `kit.py` | Primitives, materials, pivots, joining, glTF export |
| `rover.py` | The rover, part by part |
| `beacon.py` | The goal beacon |
| `surface.py` | Procedural regolith and strata graphs, and the bake |
| `sky.py` | Sky world shader, HDR render and the sun sidecar |
| `studio.py` | Studio lights, camera and Cycles settings |
| `renders.py` | Hero scene: terrain, rocks, wheel tracks, camera |
