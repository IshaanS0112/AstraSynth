"""A dusty Mars daytime sky, as a world shader and as an HDR environment map.

Butterscotch at the horizon where the light crosses the most suspended dust,
darker towards the zenith, and a pale glow around the sun. The web viewer
lights its photographic surface view with the HDR (image-based lighting) and
reads the sun direction from the sidecar, so the shadows agree with the sky.
"""

from __future__ import annotations

import json
import math

import bpy
import numpy as np
from mathutils import Vector

import kit
from surface import Graph

# Blender frame: +X forward, +Y left, +Z up. Elevation 35 degrees: long enough
# shadows to model relief, high enough that crater floors are not black.
SUN_ELEVATION = math.radians(35.0)
SUN_AZIMUTH = math.radians(212.5)  # measured from +X towards +Y


def sun_direction() -> Vector:
    return Vector(
        (
            math.cos(SUN_ELEVATION) * math.cos(SUN_AZIMUTH),
            math.cos(SUN_ELEVATION) * math.sin(SUN_AZIMUTH),
            math.sin(SUN_ELEVATION),
        )
    )


def world(strength: float = 1.0, sun: Vector | None = None) -> bpy.types.World:
    w = bpy.data.worlds.new("mars_sky")
    tree = w.node_tree
    for n in list(tree.nodes):
        if n.type != "OUTPUT_WORLD":
            tree.nodes.remove(n)
    out = next(n for n in tree.nodes if n.type == "OUTPUT_WORLD")

    class _M:  # Graph expects a material-like holder
        node_tree = tree

    g = Graph(_M())
    direction = g.node("ShaderNodeTexCoord").outputs["Generated"]
    norm = g.node("ShaderNodeVectorMath", operation="NORMALIZE")
    g.link(direction, norm.inputs[0])
    sep = g.node("ShaderNodeSeparateXYZ")
    g.link(norm.outputs["Vector"], sep.inputs[0])
    elevation = g.math("ADD", g.math("MULTIPLY", sep.outputs["Z"], 0.5), 0.5)

    s = kit.srgb
    sky = g.ramp(
        elevation,
        [
            (0.0, s("#1c120c")),
            (0.47, s("#3a261b")),
            (0.5, s("#d6a77c")),
            (0.53, s("#c99467")),
            (0.62, s("#a8764f")),
            (0.8, s("#7d5640")),
            (1.0, s("#5e4234")),
        ],
    )

    sun = sun_direction() if sun is None else sun.normalized()
    dot = g.node("ShaderNodeVectorMath", operation="DOT_PRODUCT")
    g.link(norm.outputs["Vector"], dot.inputs[0])
    dot.inputs[1].default_value = sun
    near = g.math("MAXIMUM", dot.outputs["Value"], 0.0)
    # The glow around the sun, but not the disk: the viewer adds the sun as a
    # shadow-casting light, and a disk here as well would light the scene twice.
    halo = g.math("MULTIPLY", g.math("POWER", near, 10.0), 1.6)
    corona = g.math("MULTIPLY", g.math("POWER", near, 180.0), 6.0)
    glow = g.math("ADD", halo, corona)
    shade = g.node("ShaderNodeCombineColor")
    for channel, weight in zip(shade.inputs[:3], (0.95, 0.9, 0.85), strict=True):
        g.link(g.math("MULTIPLY", glow, weight), channel)
    tinted_out = shade.outputs[0]

    total = g.mix(1.0, sky, tinted_out, blend="ADD")
    bg = g.node("ShaderNodeBackground")
    bg.inputs["Strength"].default_value = strength
    g.link(total, bg.inputs["Color"])
    g.link(bg.outputs[0], out.inputs["Surface"])
    return w


def render_hdr(path, width: int, height: int) -> dict:
    """Render the world alone through an equirectangular camera."""
    kit.reset()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = False
    scene.world = world()
    data = bpy.data.cameras.new("pano")
    data.type = "PANO"
    data.panorama_type = "EQUIRECTANGULAR"
    cam = bpy.data.objects.new("pano", data)
    scene.collection.objects.link(cam)
    # Looking along +X with +Z up, which is the layout three.js expects of an
    # equirectangular map once the exporter's +Y-up conversion is applied.
    cam.rotation_euler = (math.pi / 2, 0, -math.pi / 2)
    scene.camera = cam
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.image_settings.file_format = "HDR"
    scene.view_settings.view_transform = "Standard"
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True)

    # Find the sun in the rendered image and convert to three.js's frame, so the
    # viewer's light comes from where the sky says it does.
    img = bpy.data.images.load(str(path))
    px = np.array(img.pixels[:], dtype=np.float32).reshape(height, width, 4)
    lum = px[..., :3].sum(axis=2)
    row, col = np.unravel_index(int(np.argmax(lum)), lum.shape)
    u = (col + 0.5) / width
    v = (row + 0.5) / height  # Blender rows run bottom to top
    phi = (u - 0.5) * 2 * math.pi  # three.js: u = atan2(z, x) / 2π + 0.5
    theta = (v - 0.5) * math.pi  # v = asin(y) / π + 0.5
    measured = (math.cos(theta) * math.cos(phi), math.sin(theta), math.cos(theta) * math.sin(phi))
    expected_blender = sun_direction()
    expected = (expected_blender.x, expected_blender.z, -expected_blender.y)
    agreement = sum(a * b for a, b in zip(measured, expected, strict=True))
    return {
        "sun_three": [round(c, 4) for c in measured],
        "sun_expected_three": [round(c, 4) for c in expected],
        "agreement": round(agreement, 5),
        "peak": float(lum.max()),
    }


def write_sidecar(path, info: dict) -> None:
    payload = {
        "sun": info["sun_three"],
        "sunElevationDeg": round(math.degrees(SUN_ELEVATION), 2),
        "note": "Sun direction in three.js world space, measured from the rendered HDR.",
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
