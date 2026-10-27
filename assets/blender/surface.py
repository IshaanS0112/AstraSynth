"""Regolith and strata texture sets, baked from procedural shader graphs.

Tileable without seams: the noise is sampled on a torus in 4-D, so u = 0 and
u = 1 are the same point, and the same for v. The web viewer tiles these over
the terrain and the cliff sides of the terrain block.
"""

from __future__ import annotations

import math

import bpy

import kit


class Graph:
    """Terse node-graph building, since a shader here is a few dozen nodes."""

    def __init__(self, mat: bpy.types.Material) -> None:
        self.tree = mat.node_tree
        self.nodes = self.tree.nodes
        self.links = self.tree.links

    def node(self, kind: str, **props):
        n = self.nodes.new(kind)
        for key, value in props.items():
            setattr(n, key, value)
        return n

    def link(self, a, b) -> None:
        self.links.new(a, b)

    def math(self, op: str, a, b=None):
        n = self.node("ShaderNodeMath", operation=op)
        for i, v in enumerate((a, b)):
            if v is None:
                continue
            if isinstance(v, (int, float)):
                n.inputs[i].default_value = v
            else:
                self.link(v, n.inputs[i])
        return n.outputs[0]

    def mix(self, factor, a, b, blend: str = "MIX"):
        """Colour mix; ``a`` and ``b`` are sockets or linear RGB tuples."""
        n = self.node("ShaderNodeMix", data_type="RGBA", blend_type=blend)
        if isinstance(factor, (int, float)):
            n.inputs["Factor"].default_value = factor
        else:
            self.link(factor, n.inputs["Factor"])
        colours = [sock for sock in n.inputs if sock.type == "RGBA"]
        for sock, value in zip(colours, (a, b), strict=True):
            if isinstance(value, tuple):
                sock.default_value = (*value, 1.0)
            else:
                self.link(value, sock)
        return next(out for out in n.outputs if out.type == "RGBA")

    def ramp(self, value, stops):
        n = self.node("ShaderNodeValToRGB")
        elements = n.color_ramp.elements
        while len(elements) < len(stops):
            elements.new(0.5)
        for el, (pos, color) in zip(elements, stops, strict=True):
            el.position = pos
            el.color = (*color, 1.0)
        self.link(value, n.inputs["Fac"])
        return n.outputs["Color"]

    def torus(self, scale_v: bool = True):
        """UV on a 4-D torus: (cos 2πu, sin 2πu, cos 2πv) and W = sin 2πv."""
        uv = self.node("ShaderNodeTexCoord").outputs["UV"]
        sep = self.node("ShaderNodeSeparateXYZ")
        self.link(uv, sep.inputs[0])
        u = self.math("MULTIPLY", sep.outputs["X"], 2 * math.pi)
        v = self.math("MULTIPLY", sep.outputs["Y"], 2 * math.pi)
        comb = self.node("ShaderNodeCombineXYZ")
        self.link(self.math("COSINE", u), comb.inputs["X"])
        self.link(self.math("SINE", u), comb.inputs["Y"])
        if scale_v:
            self.link(self.math("COSINE", v), comb.inputs["Z"])
            return comb.outputs[0], self.math("SINE", v)
        self.link(sep.outputs["Y"], comb.inputs["Z"])
        return comb.outputs[0], None

    def noise(self, vector, w, scale: float, detail: float = 6.0, roughness: float = 0.55, distortion: float = 0.0):
        n = self.node("ShaderNodeTexNoise", noise_dimensions="4D" if w is not None else "3D")
        self.link(vector, n.inputs["Vector"])
        if w is not None:
            self.link(w, n.inputs["W"])
        n.inputs["Scale"].default_value = scale
        n.inputs["Detail"].default_value = detail
        n.inputs["Roughness"].default_value = roughness
        n.inputs["Distortion"].default_value = distortion
        return n

    def voronoi(self, vector, w, scale: float, randomness: float = 1.0):
        n = self.node("ShaderNodeTexVoronoi", voronoi_dimensions="4D" if w is not None else "3D")
        self.link(vector, n.inputs["Vector"])
        if w is not None:
            self.link(w, n.inputs["W"])
        n.inputs["Scale"].default_value = scale
        n.inputs["Randomness"].default_value = randomness
        return n

    def bsdf(self):
        return next(n for n in self.nodes if n.type == "BSDF_PRINCIPLED")


def regolith_material(name: str = "regolith") -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    g = Graph(mat)
    vec, w = g.torus()

    broad = g.noise(vec, w, 1.4, detail=3, roughness=0.5)
    mid = g.noise(vec, w, 5.0, detail=8, roughness=0.6, distortion=0.2)
    fine = g.noise(vec, w, 38.0, detail=4, roughness=0.7)
    cobbles = g.voronoi(vec, w, 7.0)
    pebbles = g.voronoi(vec, w, 16.0)
    gravel = g.voronoi(vec, w, 44.0)

    def stones(vor, keep: float, size: float):
        """Domes where a cell's random value clears ``keep``, and a wider skirt for contact shadow."""
        sep = g.node("ShaderNodeSeparateColor")
        g.link(vor.outputs["Color"], sep.inputs[0])
        present = g.math("GREATER_THAN", sep.outputs[0], keep)
        dist = vor.outputs["Distance"]
        dome = g.math("MAXIMUM", g.math("SUBTRACT", 1.0, g.math("DIVIDE", dist, size)), 0.0)
        skirt = g.math("MAXIMUM", g.math("SUBTRACT", 1.0, g.math("DIVIDE", dist, size * 1.35)), 0.0)
        body = g.math("MULTIPLY", present, g.math("POWER", dome, 0.45))
        edge = g.math("MULTIPLY", present, g.math("MINIMUM", g.math("MULTIPLY", dome, 9.0), 1.0))
        return body, edge, g.math("MULTIPLY", present, skirt)

    c_body, c_edge, c_skirt = stones(cobbles, 0.86, 0.36)
    p_body, p_edge, p_skirt = stones(pebbles, 0.66, 0.4)
    gr_body, gr_edge, _ = stones(gravel, 0.5, 0.42)

    stone = g.math("MINIMUM", g.math("ADD", g.math("ADD", c_edge, p_edge), g.math("MULTIPLY", gr_edge, 0.8)), 1.0)
    contact = g.math("MINIMUM", g.math("ADD", c_skirt, p_skirt), 1.0)

    height = g.math("ADD", g.math("MULTIPLY", c_body, 1.0), g.math("MULTIPLY", p_body, 0.6))
    height = g.math("ADD", height, g.math("MULTIPLY", gr_body, 0.25))
    height = g.math("ADD", height, g.math("MULTIPLY", fine.outputs["Fac"], 0.14))
    height = g.math("ADD", height, g.math("MULTIPLY", mid.outputs["Fac"], 0.2))

    s = kit.srgb
    # Fine bright dust in drifts, darker coarse sand between them.
    dust = g.ramp(mid.outputs["Fac"], [(0.35, s("#9c623c")), (0.55, s("#b8794b")), (0.75, s("#cf9668"))])
    sand = g.ramp(mid.outputs["Fac"], [(0.35, s("#74482f")), (0.6, s("#8a5839")), (0.8, s("#9c6644"))])
    drift = g.ramp(broad.outputs["Fac"], [(0.38, (0.0, 0.0, 0.0)), (0.62, (1.0, 1.0, 1.0))])
    drift_sep = g.node("ShaderNodeSeparateColor")
    g.link(drift, drift_sep.inputs[0])
    ground = g.mix(drift_sep.outputs[0], sand, dust)
    grain = g.ramp(fine.outputs["Fac"], [(0.3, s("#c9bdb3")), (0.7, s("#ffffff"))])
    ground = g.mix(1.0, ground, grain, blend="MULTIPLY")
    shadowed = g.mix(g.math("MULTIPLY", contact, 0.35), ground, s("#2a1a12"))
    stone_col = g.ramp(fine.outputs["Fac"], [(0.3, s("#2b2420")), (0.55, s("#463a33")), (0.75, s("#5d4d42"))])
    albedo = g.mix(stone, shadowed, stone_col)

    bump = g.node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 0.08
    g.link(height, bump.inputs["Height"])
    rough = g.math("SUBTRACT", 0.96, g.math("MULTIPLY", stone, 0.1))

    bsdf = g.bsdf()
    g.link(albedo, bsdf.inputs["Base Color"])
    g.link(bump.outputs["Normal"], bsdf.inputs["Normal"])
    g.link(rough, bsdf.inputs["Roughness"])
    return mat


def strata_material(name: str = "strata") -> bpy.types.Material:
    """Sedimentary layers for the cliff faces of the terrain block; tiles along u only.

    v runs up the cliff. Each layer is a band of v whose boundary wanders a
    little, with its own colour, and the harder layers stand proud.
    """
    mat = bpy.data.materials.new(name)
    g = Graph(mat)
    vec, _ = g.torus(scale_v=False)
    uv = g.node("ShaderNodeTexCoord").outputs["UV"]
    sep = g.node("ShaderNodeSeparateXYZ")
    g.link(uv, sep.inputs[0])
    wobble = g.noise(vec, None, 1.8, detail=5, roughness=0.55)
    grain = g.noise(vec, None, 34.0, detail=4, roughness=0.7)
    blocks = g.voronoi(vec, None, 9.0)
    level = g.math("ADD", sep.outputs["Y"], g.math("MULTIPLY", g.math("SUBTRACT", wobble.outputs["Fac"], 0.5), 0.07))

    s = kit.srgb
    layers = [
        (0.0, "#3b2418"), (0.09, "#6e4329"), (0.16, "#8e5c3c"), (0.24, "#5a3a2a"),
        (0.31, "#a36f4a"), (0.43, "#7c5038"), (0.5, "#4c3022"), (0.58, "#b07a52"),
        (0.7, "#8a5a3e"), (0.78, "#6a432f"), (0.86, "#a8724c"), (1.0, "#c08a5f"),
    ]
    albedo = g.ramp(level, [(pos, s(col)) for pos, col in layers])
    # Hard layers (the lighter ones) are resistant and stand out from the face.
    hardness = g.ramp(level, [(pos, (h, h, h)) for (pos, _), h in zip(layers, (0.2, 0.5, 0.8, 0.3, 1.0, 0.5, 0.2, 1.0, 0.6, 0.3, 0.9, 0.7), strict=True)])
    hard_sep = g.node("ShaderNodeSeparateColor")
    g.link(hardness, hard_sep.inputs[0])
    blocky = g.math("MULTIPLY", blocks.outputs["Distance"], hard_sep.outputs[0])
    height = g.math("ADD", g.math("MULTIPLY", hard_sep.outputs[0], 0.6), g.math("MULTIPLY", blocky, 0.25))
    height = g.math("ADD", height, g.math("MULTIPLY", grain.outputs["Fac"], 0.2))
    grain_tint = g.ramp(grain.outputs["Fac"], [(0.3, s("#cfc5bb")), (0.7, s("#ffffff"))])
    albedo = g.mix(1.0, albedo, grain_tint, blend="MULTIPLY")

    bump = g.node("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.9
    bump.inputs["Distance"].default_value = 0.1
    g.link(height, bump.inputs["Height"])
    bsdf = g.bsdf()
    g.link(albedo, bsdf.inputs["Base Color"])
    g.link(bump.outputs["Normal"], bsdf.inputs["Normal"])
    bsdf.inputs["Roughness"].default_value = 0.9
    return mat


def bake_set(make_material, prefix: str, out_dir, width: int, height: int, quick: bool) -> dict:
    """Bake colour, tangent-space normal and roughness of a material on a unit plane."""
    kit.reset()
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 2 if quick else 8
    bpy.ops.mesh.primitive_plane_add(size=2)
    plane = bpy.context.active_object
    material = make_material()
    plane.data.materials.append(material)

    results = {}
    for kind, settings in (
        ("albedo", {"type": "DIFFUSE", "pass_filter": {"COLOR"}}),
        ("normal", {"type": "NORMAL", "normal_space": "TANGENT"}),
        ("roughness", {"type": "ROUGHNESS"}),
    ):
        image = bpy.data.images.new(f"{prefix}_{kind}", width, height, alpha=False, float_buffer=False)
        if kind != "albedo":
            image.colorspace_settings.name = "Non-Color"
        node = material.node_tree.nodes.new("ShaderNodeTexImage")
        node.image = image
        material.node_tree.nodes.active = node
        bpy.ops.object.bake(margin=0, use_clear=True, **settings)
        path = out_dir / f"{prefix}_{kind}.jpg"
        image.filepath_raw = str(path)
        image.file_format = "JPEG"
        scene.render.image_settings.quality = 92
        image.save(filepath=str(path), quality=92)
        results[path.name] = {"bytes": path.stat().st_size}
    return results
