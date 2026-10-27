"""Hero renders: the rover on Martian terrain, in Cycles.

The terrain is a heightfield built here - broad undulation, a degraded crater,
scattered rocks with a power-law size spread, and the wheel tracks the rover
left on its way in. It is illustrative terrain, not a mission tile: a 1 km
mission DEM at 2 m per pixel has nothing to say about the ground under a 3 m
rover.
"""

from __future__ import annotations

import math
import random

import bmesh
import bpy
import numpy as np
from mathutils import Vector, noise

import kit
import rover
import sky
import studio
from surface import Graph

TRACK_HALF = rover.AXLE_Y


def fbm(x: np.ndarray, y: np.ndarray, seed: int, octaves: list[tuple[float, float]]) -> np.ndarray:
    """Sum of smooth value-noise octaves; each octave is (cell size m, amplitude m)."""
    rng = np.random.default_rng(seed)
    total = np.zeros_like(x)
    for cell, amp in octaves:
        ox, oy = rng.uniform(0, 1000, 2)
        gx, gy = (x + ox) / cell, (y + oy) / cell
        x0, y0 = np.floor(gx).astype(int), np.floor(gy).astype(int)
        fx, fy = gx - x0, gy - y0
        sx, sy = fx * fx * (3 - 2 * fx), fy * fy * (3 - 2 * fy)
        size = 4096
        lattice = rng.random(size)

        def h(ix, iy, lattice=lattice):
            return lattice[(ix * 73856093 ^ iy * 19349663) % size]

        top = h(x0, y0) * (1 - sx) + h(x0 + 1, y0) * sx
        bottom = h(x0, y0 + 1) * (1 - sx) + h(x0 + 1, y0 + 1) * sx
        total += (top * (1 - sy) + bottom * sy - 0.5) * 2 * amp
    return total


def track_path() -> np.ndarray:
    """Centre line of the drive in, ending under the rover facing +X."""
    t = np.linspace(0, 1, 400)
    x = -42 + 42 * t
    y = 9.0 * (1 - t) ** 2 - 1.2 * np.sin(t * math.pi * 1.4) * (1 - t)
    return np.stack([x, y], axis=1)


NEAR = ((-46.0, 22.0), (-24.0, 30.0))  # the detailed patch, in metres


def base_height(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """The ground both terrain meshes share, so they meet without a seam."""
    z = fbm(x, y, 11, [(26.0, 1.4), (11.0, 0.55), (4.0, 0.22), (1.6, 0.07), (0.6, 0.025)])
    # A degraded crater behind and to the left: bowl, raised rim, softened.
    cx, cy, radius = -14.0, 16.0, 8.5
    d = np.hypot(x - cx, y - cy) / radius
    bowl = np.where(d < 1, -1.6 * (1 - d**2), 0.0)
    rim = 0.75 * np.exp(-((d - 1.0) ** 2) / 0.05)
    return z + bowl + rim


def heightfield(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Heights, a 0-1 mask of where the wheels disturbed the regolith, and distance along the track."""
    x, y = np.meshgrid(xs, ys)
    z = base_height(x, y)

    # Flatten gently where the rover stands, so it sits on the ground.
    pad = np.exp(-((x - 0.0) ** 2 + y**2) / 9.0)
    z = z * (1 - 0.8 * pad) + z[np.unravel_index(np.argmin(np.hypot(x, y)), x.shape)] * 0.8 * pad

    # Wheel tracks: two grooves, a wheel track apart, pressed into the regolith.
    path = track_path()
    track = np.zeros_like(z)
    along = np.zeros_like(z)
    seg = np.diff(path, axis=0)
    normals = np.stack([-seg[:, 1], seg[:, 0]], axis=1)
    normals /= np.linalg.norm(normals, axis=1, keepdims=True)
    for side in (-1, 1):
        line = path[:-1] + normals * side * TRACK_HALF
        groove = np.zeros_like(z)
        pts = np.stack([x.ravel(), y.ravel()], axis=1)
        # Distance from every vertex to the track polyline, and how far along
        # it the nearest point is (for the grouser imprints), done in chords.
        best = np.full(pts.shape[0], np.inf)
        best_s = np.zeros(pts.shape[0])
        travelled = 0.0
        for i in range(0, len(line) - 1, 8):
            a, b = line[i], line[min(i + 8, len(line) - 1)]
            ab = b - a
            length = float(np.linalg.norm(ab))
            t = np.clip(((pts - a) @ ab) / max(ab @ ab, 1e-9), 0, 1)
            proj = a + t[:, None] * ab
            d = np.linalg.norm(pts - proj, axis=1)
            closer = d < best
            best = np.where(closer, d, best)
            best_s = np.where(closer, travelled + t * length, best_s)
            travelled += length
        dist = best.reshape(z.shape)
        groove = -0.06 * np.exp(-(dist**2) / (2 * 0.14**2)) + 0.02 * np.exp(-((dist - 0.34) ** 2) / (2 * 0.07**2))
        # Tracks fade as they age: older ground has had dust blown over it.
        age = np.clip((x + 42) / 42, 0.25, 1.0)
        behind = x < -0.6
        z += np.where(behind, groove * age, 0.0)
        mask = np.where(behind, np.exp(-(dist**2) / (2 * 0.22**2)) * age, 0.0)
        along = np.where(mask > track, best_s.reshape(z.shape), along)
        track = np.maximum(track, mask)
    return z, track, along


def terrain_mesh(name: str, xs, ys, z, material, track=None, along=None) -> bpy.types.Object:
    nx, ny = len(xs), len(ys)
    gx, gy = np.meshgrid(xs, ys)
    verts = np.stack([gx.ravel(), gy.ravel(), z.ravel()], axis=1).astype(np.float32)
    ii, jj = np.meshgrid(np.arange(nx - 1), np.arange(ny - 1))
    a = (jj * nx + ii).ravel()
    faces = np.stack([a, a + 1, a + nx + 1, a + nx], axis=1).astype(np.int32)
    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(len(verts))
    mesh.vertices.foreach_set("co", verts.ravel())
    mesh.loops.add(faces.size)
    mesh.loops.foreach_set("vertex_index", faces.ravel())
    mesh.polygons.add(len(faces))
    mesh.polygons.foreach_set("loop_start", np.arange(0, faces.size, 4, dtype=np.int32))
    mesh.update(calc_edges=True)
    mesh.polygons.foreach_set("use_smooth", np.ones(len(faces), dtype=bool))
    for attr_name, values in (("track", track), ("track_s", along)):
        if values is not None:
            attr = mesh.attributes.new(attr_name, "FLOAT", "POINT")
            attr.data.foreach_set("value", values.ravel().astype(np.float32))
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    obj.data.materials.append(material)
    return obj


def hazed(mat: bpy.types.Material, near: float, far: float, colour) -> None:
    """Aerial perspective: blend the surface towards the horizon colour with distance."""
    g = Graph(mat)
    out = next(n for n in g.nodes if n.type == "OUTPUT_MATERIAL")
    surface_link = out.inputs["Surface"].links[0]
    shader = surface_link.from_socket
    cam = g.node("ShaderNodeCameraData")
    fac = g.node("ShaderNodeMapRange")
    fac.inputs["From Min"].default_value = near
    fac.inputs["From Max"].default_value = far
    fac.inputs["To Max"].default_value = 0.85
    g.link(cam.outputs["View Distance"], fac.inputs["Value"])
    emit = g.node("ShaderNodeEmission")
    emit.inputs["Color"].default_value = (*colour, 1.0)
    emit.inputs["Strength"].default_value = 1.0
    mix = g.node("ShaderNodeMixShader")
    g.link(fac.outputs["Result"], mix.inputs["Fac"])
    g.link(shader, mix.inputs[1])
    g.link(emit.outputs[0], mix.inputs[2])
    g.links.remove(surface_link)
    g.link(mix.outputs[0], out.inputs["Surface"])


def dusty(mat: bpy.types.Material, amount: float = 0.75) -> None:
    """Settle a little regolith on faces that point up: renders only, never exported."""
    g = Graph(mat)
    bsdf = g.bsdf()
    base = bsdf.inputs["Base Color"]
    colour = tuple(base.default_value[:3]) if not base.links else None
    normal = g.node("ShaderNodeNewGeometry").outputs["Normal"]
    sep = g.node("ShaderNodeSeparateXYZ")
    g.link(normal, sep.inputs[0])
    up = g.math("POWER", g.math("MAXIMUM", sep.outputs["Z"], 0.0), 3.0)
    speckle = g.noise(g.node("ShaderNodeTexCoord").outputs["Object"], None, 18.0, detail=6, roughness=0.6)
    amount = g.math("MULTIPLY", up, g.math("MULTIPLY", speckle.outputs["Fac"], amount))
    if colour is not None:
        mixed = g.mix(amount, colour, kit.srgb("#b07a52"))
        g.link(mixed, base)
    elif base.links:
        mixed = g.mix(amount, base.links[0].from_socket, kit.srgb("#9c6a46"))
        g.link(mixed, base)
    rough = bsdf.inputs["Roughness"]
    if not rough.links:
        r = g.node("ShaderNodeMapRange")
        r.inputs["To Min"].default_value = rough.default_value
        r.inputs["To Max"].default_value = 0.95
        g.link(amount, r.inputs["Value"])
        g.link(r.outputs["Result"], rough)


def ground_material() -> bpy.types.Material:
    """The baked regolith graph, driven by object coordinates instead of the torus."""
    import surface

    mat = surface.regolith_material("ground")
    g = Graph(mat)
    tex = g.node("ShaderNodeTexCoord")
    scale = g.node("ShaderNodeVectorMath", operation="SCALE")
    scale.inputs["Scale"].default_value = 0.42
    g.link(tex.outputs["Object"], scale.inputs[0])
    # Re-point every 4-D texture at 3-D object space.
    for node in list(g.nodes):
        if node.type == "TEX_NOISE":
            node.noise_dimensions = "3D"
            g.link(scale.outputs["Vector"], node.inputs["Vector"])
        elif node.type == "TEX_VORONOI":
            node.voronoi_dimensions = "3D"
            g.link(scale.outputs["Vector"], node.inputs["Vector"])
    # Wheel-disturbed regolith is darker than the dust-covered surface around it.
    bsdf = g.bsdf()
    # Dust is matte: without this, millions of tiny domes mirror the bright sky
    # at grazing angles and read as pale dots.
    bsdf.inputs["Specular IOR Level"].default_value = 0.25
    albedo = bsdf.inputs["Base Color"].links[0].from_socket
    track = g.node("ShaderNodeAttribute", attribute_name="track").outputs["Fac"]
    along = g.node("ShaderNodeAttribute", attribute_name="track_s").outputs["Fac"]
    # Grouser imprints: a cleat every 6.9 cm of track, pressed a little deeper.
    phase = g.math("FRACT", g.math("DIVIDE", along, 0.069))
    cleat = g.math("LESS_THAN", phase, 0.32)
    darken = g.math("MULTIPLY", track, g.math("ADD", 0.82, g.math("MULTIPLY", cleat, 0.18)))
    darker = g.mix(darken, albedo, (0.3, 0.25, 0.22), blend="MULTIPLY")
    g.link(darker, bsdf.inputs["Base Color"])
    return mat


def rock_material() -> bpy.types.Material:
    """Basalt: dark, mottled, pitted, with dust settled on the tops."""
    import surface

    mat = bpy.data.materials.new("basalt")
    g = Graph(mat)
    coords = g.node("ShaderNodeTexCoord").outputs["Object"]
    mottle = g.noise(coords, None, 3.0, detail=6, roughness=0.6)
    pits = g.noise(coords, None, 22.0, detail=4, roughness=0.7)
    s = kit.srgb
    colour = g.ramp(mottle.outputs["Fac"], [(0.3, s("#241d1a")), (0.55, s("#3a2f29")), (0.75, s("#54443a"))])
    bump = g.node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.6
    bump.inputs["Distance"].default_value = 0.02
    g.link(g.math("ADD", mottle.outputs["Fac"], g.math("MULTIPLY", pits.outputs["Fac"], 0.6)), bump.inputs["Height"])
    bsdf = g.bsdf()
    g.link(colour, bsdf.inputs["Base Color"])
    g.link(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.9
    bsdf.inputs["Specular IOR Level"].default_value = 0.3
    del surface
    return mat


def rock_library(count: int, seed: int) -> list:
    """Angular rocks: a displaced sphere, decimated so it breaks into facets."""
    rng = random.Random(seed)
    basalt = rock_material()
    dusty(basalt, amount=0.95)
    meshes = []
    for i in range(count):
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=3, radius=1.0)
        offset = Vector((rng.uniform(0, 100), rng.uniform(0, 100), rng.uniform(0, 100)))
        for v in bm.verts:
            n = v.co.normalized()
            d = 0.3 * noise.noise(n * 1.2 + offset) + 0.12 * noise.noise(n * 3.4 + offset)
            v.co = n * (1.0 + d)
            v.co.z = max(v.co.z, -0.3)
        mesh = bpy.data.meshes.new(f"rock_{i}")
        bm.to_mesh(mesh)
        bm.free()
        holder = bpy.data.objects.new(f"rock_src_{i}", mesh)
        bpy.context.scene.collection.objects.link(holder)
        mod = holder.modifiers.new("facets", "DECIMATE")
        mod.ratio = rng.uniform(0.12, 0.22)
        kit.deselect_all()
        holder.select_set(True)
        bpy.context.view_layer.objects.active = holder
        bpy.ops.object.modifier_apply(modifier="facets")
        bpy.ops.object.shade_smooth_by_angle(angle=math.radians(28))
        mesh.materials.append(basalt)
        bpy.data.objects.remove(holder)
        meshes.append(mesh)
    return meshes


def scatter_rocks(meshes, sample_height, seed: int, n: int, area, avoid, camera_xy=None) -> None:
    rng = random.Random(seed)
    placed = 0
    attempts = 0
    while placed < n and attempts < n * 20:
        attempts += 1
        x = rng.uniform(*area[0])
        y = rng.uniform(*area[1])
        if avoid(x, y):
            continue
        # Power-law sizes: many pebbles, few boulders.
        size = 0.05 * (1 - rng.random()) ** -0.7
        size = min(size, 0.9)
        # Nothing big right under the lens: a boulder in the foreground takes the shot.
        if camera_xy is not None and math.hypot(x - camera_xy[0], y - camera_xy[1]) < 6.0:
            size = min(size, 0.1)
        obj = bpy.data.objects.new(f"rock_inst_{placed}", rng.choice(meshes))
        bpy.context.scene.collection.objects.link(obj)
        obj.scale = (size * rng.uniform(0.8, 1.4), size * rng.uniform(0.8, 1.3), size * rng.uniform(0.45, 0.8))
        obj.rotation_euler = (rng.uniform(-0.2, 0.2), rng.uniform(-0.2, 0.2), rng.uniform(0, math.tau))
        obj.location = (x, y, sample_height(x, y) - size * 0.25)
        placed += 1


def hero(path, quick: bool) -> dict:
    kit.reset()
    scene = bpy.context.scene

    # Terrain: a detailed patch around the rover inside a coarse apron to the horizon.
    near_xs = np.arange(NEAR[0][0], NEAR[0][1], 0.1 if not quick else 0.2)
    near_ys = np.arange(NEAR[1][0], NEAR[1][1], 0.1 if not quick else 0.2)
    z, track, along = heightfield(near_xs, near_ys)
    ground = ground_material()
    horizon = kit.srgb("#c99467")
    hazed(ground, 18.0, 160.0, horizon)
    terrain_mesh("terrain", near_xs, near_ys, z, ground, track, along)
    # The apron to the horizon: the same ground at the patch edge, with broad
    # relief fading in further out; under the patch it drops out of sight.
    far_xs = np.arange(-400, 300, 2.0)
    far_ys = np.arange(-300, 400, 2.0)
    gx, gy = np.meshgrid(far_xs, far_ys)
    (x0, x1), (y0, y1) = NEAR
    outside = np.hypot(np.maximum(np.maximum(x0 - gx, gx - x1), 0), np.maximum(np.maximum(y0 - gy, gy - y1), 0))
    fade = np.clip(outside / 50.0, 0, 1) ** 2
    far_z = base_height(gx, gy) + fbm(gx, gy, 23, [(80.0, 5.0)]) * fade
    inside = (gx > x0 + 0.5) & (gx < x1 - 0.5) & (gy > y0 + 0.5) & (gy < y1 - 0.5)
    far_z = np.where(inside, far_z - 0.8, far_z)
    terrain_mesh("apron", far_xs, far_ys, far_z, ground)

    def sample(x: float, y: float) -> float:
        i = int(np.clip(round((y - near_ys[0]) / (near_ys[1] - near_ys[0])), 0, len(near_ys) - 1))
        j = int(np.clip(round((x - near_xs[0]) / (near_xs[1] - near_xs[0])), 0, len(near_xs) - 1))
        return float(z[i, j])

    path_pts = track_path()

    def avoid(x: float, y: float) -> bool:
        if x * x + y * y < 4.0:
            return True
        d = np.min(np.hypot(path_pts[:, 0] - x, path_pts[:, 1] - y))
        return d < TRACK_HALF + 0.5

    rocks = rock_library(14, 3)
    hazed(rocks[0].materials[0], 18.0, 160.0, horizon)  # one basalt material, shared
    camera_at = (3.0, 7.6)
    scatter_rocks(rocks, sample, 7, 120 if quick else 300, ((-40, 20), (-22, 28)), avoid, camera_at)

    # The rover, settled onto the pad.
    objects = rover.build()
    root = next(o for o in objects if o.name == "rover")
    root.location = (0, 0, sample(0, 0))

    # Light: the sun is ahead and to the left of the rover, the side the camera
    # sees, so the visible faces are lit and the shadow falls away from the viewer.
    elevation, azimuth = math.radians(28), math.radians(18)
    sun_dir = Vector((math.cos(elevation) * math.cos(azimuth), math.cos(elevation) * math.sin(azimuth), math.sin(elevation)))
    scene.world = sky.world(strength=1.0, sun=sun_dir)
    sun = bpy.data.objects.new("sun", bpy.data.lights.new("sun", "SUN"))
    scene.collection.objects.link(sun)
    sun.data.energy = 4.2
    sun.data.angle = math.radians(0.6)
    sun.data.color = (1.0, 0.93, 0.84)
    sun.rotation_euler = (-sun_dir).to_track_quat("-Z", "Y").to_euler()
    studio.area_light("rim", (-5.5, -5.0, 3.0), (0, 0, 1.0), 900, 4.0, (0.95, 0.88, 0.8))
    for mat in {slot.material for obj in objects if obj.type == "MESH" for slot in obj.material_slots}:
        if mat is not None and mat.name not in {"status_led", "lens_glass"}:
            dusty(mat)

    studio.cycles(28 if quick else 160, 960 if quick else 1920, 540 if quick else 1080, transparent=False)
    scene.cycles.max_bounces = 4
    scene.cycles.adaptive_threshold = 0.02
    cam = studio.camera((3.0, 7.6, 1.3), (-1.5, 0.3, 0.45), lens=36, dof=4.5)
    cam.data.clip_end = 2000
    studio.render(str(path))
    return {"bytes": path.stat().st_size}
