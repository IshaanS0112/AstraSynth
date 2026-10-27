"""Small modelling kit over bpy: primitives, materials, hierarchy, export.

Everything is built in Blender's frame (+X forward, +Z up, metres). The glTF
exporter turns that into three.js's +Y-up frame on the way out.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

import bpy
from mathutils import Matrix, Vector

Vec3 = Sequence[float]


def reset() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def deselect_all() -> None:
    for obj in bpy.context.view_layer.objects:
        obj.select_set(False)


# --- materials ------------------------------------------------------------------


def material(
    name: str,
    base: Vec3,
    *,
    metallic: float = 0.0,
    roughness: float = 0.5,
    emission: Vec3 | None = None,
    emission_strength: float = 0.0,
    coat: float = 0.0,
) -> bpy.types.Material:
    """A Principled BSDF with factors only, so it exports to glTF without textures."""
    mat = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    bsdf.inputs["Base Color"].default_value = (*base, 1.0)
    bsdf.inputs["Metallic"].default_value = metallic
    bsdf.inputs["Roughness"].default_value = roughness
    if emission is not None:
        bsdf.inputs["Emission Color"].default_value = (*emission, 1.0)
        bsdf.inputs["Emission Strength"].default_value = emission_strength
    if coat:
        bsdf.inputs["Coat Weight"].default_value = coat
        bsdf.inputs["Coat Roughness"].default_value = 0.05
    mat.diffuse_color = (*base, 1.0)
    return mat


def srgb(hex_code: str) -> tuple[float, float, float]:
    """A '#rrggbb' colour as linear RGB, the space Blender's colour sockets use."""
    hex_code = hex_code.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(hex_code[i : i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return out[0], out[1], out[2]


# --- hierarchy ------------------------------------------------------------------


def pivot(name: str, location: Vec3 = (0, 0, 0), parent: bpy.types.Object | None = None) -> bpy.types.Object:
    """An empty: a named node the web viewer can find and animate."""
    empty = bpy.data.objects.new(name, None)
    empty.empty_display_size = 0.1
    empty.location = Vector(location)
    bpy.context.scene.collection.objects.link(empty)
    if parent is not None:
        empty.parent = parent
    return empty


def _finish(
    obj: bpy.types.Object,
    mat: bpy.types.Material | None,
    parent: bpy.types.Object | None,
    bevel: float,
    segments: int,
    angle: float = 40.0,
) -> bpy.types.Object:
    if mat is not None:
        obj.data.materials.clear()
        obj.data.materials.append(mat)
    if bevel > 0:
        mod = obj.modifiers.new("Bevel", "BEVEL")
        mod.width = bevel
        mod.segments = segments
        mod.limit_method = "ANGLE"
        mod.angle_limit = math.radians(angle)
    if parent is not None:
        obj.parent = parent
    return obj


def _link(obj: bpy.types.Object) -> bpy.types.Object:
    bpy.context.scene.collection.objects.link(obj)
    return obj


def box(
    name: str,
    size: Vec3,
    location: Vec3 = (0, 0, 0),
    rotation: Vec3 = (0, 0, 0),
    *,
    mat: bpy.types.Material | None = None,
    parent: bpy.types.Object | None = None,
    bevel: float = 0.0,
    segments: int = 2,
) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    sx, sy, sz = (s / 2 for s in size)
    verts = [(x * sx, y * sy, z * sz) for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    faces = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = _link(bpy.data.objects.new(name, mesh))
    obj.location = Vector(location)
    obj.rotation_euler = rotation
    return _finish(obj, mat, parent, bevel, segments)


def cylinder(
    name: str,
    radius: float,
    depth: float,
    location: Vec3 = (0, 0, 0),
    rotation: Vec3 = (0, 0, 0),
    *,
    vertices: int = 32,
    mat: bpy.types.Material | None = None,
    parent: bpy.types.Object | None = None,
    bevel: float = 0.0,
    segments: int = 2,
    cap: bool = True,
    radius_top: float | None = None,
) -> bpy.types.Object:
    """A cylinder along local Z; ``radius_top`` makes it a cone frustum."""
    mesh = bpy.data.meshes.new(name)
    top = radius if radius_top is None else radius_top
    verts, faces = [], []
    for i in range(vertices):
        a = 2 * math.pi * i / vertices
        verts.append((radius * math.cos(a), radius * math.sin(a), -depth / 2))
        verts.append((top * math.cos(a), top * math.sin(a), depth / 2))
    for i in range(vertices):
        j = (i + 1) % vertices
        faces.append((2 * i, 2 * j, 2 * j + 1, 2 * i + 1))
    if cap:
        faces.append(tuple(2 * i for i in reversed(range(vertices))))
        faces.append(tuple(2 * i + 1 for i in range(vertices)))
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = _link(bpy.data.objects.new(name, mesh))
    obj.location = Vector(location)
    obj.rotation_euler = rotation
    return _finish(obj, mat, parent, bevel, segments)


def sphere(
    name: str,
    radius: float,
    location: Vec3 = (0, 0, 0),
    *,
    subdivisions: int = 2,
    mat: bpy.types.Material | None = None,
    parent: bpy.types.Object | None = None,
) -> bpy.types.Object:
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=subdivisions, radius=radius, location=(0, 0, 0))
    obj = bpy.context.active_object
    obj.name = name
    obj.location = Vector(location)
    bpy.ops.object.shade_smooth()
    return _finish(obj, mat, parent, 0.0, 0)


def tube(
    name: str,
    points: Iterable[Vec3],
    radius: float,
    *,
    mat: bpy.types.Material | None = None,
    parent: bpy.types.Object | None = None,
    smooth: bool = False,
    resolution: int = 6,
) -> bpy.types.Object:
    """A round tube through ``points``: struts, rocker arms, cables.

    ``smooth`` runs a Bezier through the points; otherwise a polyline with a
    small round at each joint, which is how welded tube frames look.
    """
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 3
    curve.use_fill_caps = True
    curve.resolution_u = resolution
    pts = [Vector(p) for p in points]
    if smooth:
        spline = curve.splines.new("BEZIER")
        spline.bezier_points.add(len(pts) - 1)
        for bp, p in zip(spline.bezier_points, pts, strict=True):
            bp.co = p
            bp.handle_left_type = bp.handle_right_type = "AUTO"
    else:
        spline = curve.splines.new("POLY")
        spline.points.add(len(pts) - 1)
        for sp, p in zip(spline.points, pts, strict=True):
            sp.co = (*p, 1.0)
    obj = _link(bpy.data.objects.new(name, curve))
    if mat is not None:
        curve.materials.append(mat)
    if parent is not None:
        obj.parent = parent
    return obj


def text(
    name: str,
    body: str,
    size: float,
    location: Vec3,
    rotation: Vec3,
    *,
    mat: bpy.types.Material | None = None,
    parent: bpy.types.Object | None = None,
    extrude: float = 0.002,
) -> bpy.types.Object:
    curve = bpy.data.curves.new(name, "FONT")
    curve.body = body
    curve.size = size
    curve.extrude = extrude
    curve.align_x = "CENTER"
    curve.align_y = "CENTER"
    obj = _link(bpy.data.objects.new(name, curve))
    obj.location = Vector(location)
    obj.rotation_euler = rotation
    if mat is not None:
        curve.materials.append(mat)
    if parent is not None:
        obj.parent = parent
    return obj


def lathe(
    name: str,
    profile: Sequence[tuple[float, float]],
    location: Vec3 = (0, 0, 0),
    rotation: Vec3 = (0, 0, 0),
    *,
    vertices: int = 48,
    mat: bpy.types.Material | None = None,
    parent: bpy.types.Object | None = None,
) -> bpy.types.Object:
    """A surface of revolution about local Z from (radius, z) pairs: dishes, lenses."""
    mesh = bpy.data.meshes.new(name)
    verts, faces = [], []
    rings = len(profile)
    for i in range(vertices):
        a = 2 * math.pi * i / vertices
        for r, z in profile:
            verts.append((r * math.cos(a), r * math.sin(a), z))
    for i in range(vertices):
        j = (i + 1) % vertices
        for k in range(rings - 1):
            faces.append((i * rings + k, j * rings + k, j * rings + k + 1, i * rings + k + 1))
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = _link(bpy.data.objects.new(name, mesh))
    obj.location = Vector(location)
    obj.rotation_euler = rotation
    bpy.context.view_layer.objects.active = obj
    return _finish(obj, mat, parent, 0.0, 0)


def align_z(start: Vec3, end: Vec3) -> tuple[Vector, tuple[float, float, float], float]:
    """Location, rotation and length that put a local-Z primitive from ``start`` to ``end``."""
    a, b = Vector(start), Vector(end)
    direction = b - a
    rotation = direction.to_track_quat("Z", "Y").to_euler()
    return (a + b) / 2, (rotation.x, rotation.y, rotation.z), direction.length


# --- finishing ------------------------------------------------------------------


def bake_group(root: bpy.types.Object, name: str, smooth_angle: float = 35.0) -> bpy.types.Object | None:
    """Apply modifiers and join every mesh directly under ``root`` into one object.

    A handful of meshes instead of hundreds is the difference between a few
    draw calls and a few hundred for the web viewer.
    """
    bpy.context.view_layer.update()
    deselect_all()
    parts = [c for c in root.children if c.type in {"MESH", "CURVE", "FONT"}]
    if not parts:
        return None
    for obj in parts:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = parts[0]
    bpy.ops.object.convert(target="MESH")
    parts = [o for o in bpy.context.selected_objects]
    bpy.context.view_layer.objects.active = parts[0]
    if len(parts) > 1:
        bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = name
    joined.data.name = name
    bpy.ops.object.shade_smooth_by_angle(angle=math.radians(smooth_angle), keep_sharp_edges=True)
    # Keep the joined mesh under its pivot without moving it.
    world = joined.matrix_world.copy()
    joined.parent = root
    joined.matrix_world = world
    deselect_all()
    return joined


def triangle_count(objects: Iterable[bpy.types.Object]) -> int:
    depsgraph = bpy.context.evaluated_depsgraph_get()
    total = 0
    for obj in objects:
        if obj.type != "MESH":
            continue
        mesh = obj.evaluated_get(depsgraph).to_mesh()
        mesh.calc_loop_triangles()
        total += len(mesh.loop_triangles)
        obj.evaluated_get(depsgraph).to_mesh_clear()
    return total


def export_glb(path: str, objects: Iterable[bpy.types.Object]) -> None:
    deselect_all()
    for obj in objects:
        obj.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=path,
        export_format="GLB",
        use_selection=True,
        export_apply=True,
        export_yup=True,
        export_materials="EXPORT",
        export_normals=True,
        export_tangents=False,
        export_extras=False,
        export_animations=False,
    )
    deselect_all()


def transform_matrix(location: Vec3, rotation: Vec3 = (0, 0, 0)) -> Matrix:
    from mathutils import Euler

    return Matrix.Translation(Vector(location)) @ Euler(rotation).to_matrix().to_4x4()
