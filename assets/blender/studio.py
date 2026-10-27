"""Studio renders of the rover: a neutral cyclorama, a key, a rim and a fill.

Used for the fleet cards and for reviewing the model while it is being built.
"""

from __future__ import annotations

import math

import bpy
from mathutils import Vector

import kit


def cycles(samples: int, width: int, height: int, transparent: bool) -> None:
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = samples
    scene.cycles.use_denoising = True
    scene.cycles.max_bounces = 6
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = transparent
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA" if transparent else "RGB"
    scene.view_settings.view_transform = "AgX"
    scene.view_settings.look = "AgX - Medium High Contrast"


def look_at(obj, target) -> None:
    direction = Vector(target) - obj.location
    obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def camera(location, target, lens: float = 50.0, dof: float | None = None):
    data = bpy.data.cameras.new("studio_cam")
    data.lens = lens
    cam = bpy.data.objects.new("studio_cam", data)
    bpy.context.scene.collection.objects.link(cam)
    cam.location = Vector(location)
    look_at(cam, target)
    if dof:
        data.dof.use_dof = True
        data.dof.focus_distance = (Vector(target) - Vector(location)).length
        data.dof.aperture_fstop = dof
    bpy.context.scene.camera = cam
    return cam


def area_light(name, location, target, energy, size, color=(1, 1, 1)):
    data = bpy.data.lights.new(name, "AREA")
    data.energy = energy
    data.size = size
    data.color = color
    light = bpy.data.objects.new(name, data)
    bpy.context.scene.collection.objects.link(light)
    light.location = Vector(location)
    look_at(light, target)
    return light


def world(color, strength: float) -> None:
    w = bpy.data.worlds.new("studio_world")
    bg = next(n for n in w.node_tree.nodes if n.type == "BACKGROUND")
    bg.inputs["Color"].default_value = (*color, 1)
    bg.inputs["Strength"].default_value = strength
    bpy.context.scene.world = w


def cyclorama(color) -> None:
    """A floor that curves up into a wall, so the background has no horizon line."""
    import bmesh

    mesh = bpy.data.meshes.new("cyc")
    bm = bmesh.new()
    rows = []
    for i in range(24):
        t = i / 23
        if t < 0.6:
            y, z = -8 + t / 0.6 * 12, 0.0
        else:
            a = (t - 0.6) / 0.4 * math.pi / 2
            y, z = 4 + 3 * math.sin(a), 3 - 3 * math.cos(a)
        rows.append([bm.verts.new((x, y, z)) for x in (-12, 12)])
    for a, b in zip(rows, rows[1:], strict=False):
        bm.faces.new((a[0], a[1], b[1], b[0]))
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("cyc", mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(kit.material("cyc", color, roughness=0.9))
    for poly in obj.data.polygons:
        poly.use_smooth = True


def studio_scene(transparent: bool) -> None:
    world(kit.srgb("#1a1c20"), 0.6)
    if not transparent:
        cyclorama(kit.srgb("#2a2d33"))
    center = (0.05, 0, 0.9)
    area_light("key", (3.4, -3.0, 4.2), center, 620, 3.0, (1.0, 0.96, 0.9))
    area_light("rim", (-3.5, 2.6, 3.0), center, 520, 2.5, (0.85, 0.9, 1.0))
    area_light("fill", (-1.0, -4.5, 1.2), center, 110, 4.0)
    area_light("top", (0, 0, 6), center, 160, 5.0)


def render(path: str) -> None:
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
