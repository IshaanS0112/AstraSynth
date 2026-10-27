"""The rover, modelled part by part.

Laid out after the six-wheeled rocker-bogie rovers: a warm electronics box on a
differential, a rocker per side carrying the front wheel and a bogie carrying the
middle and rear wheels, steerable corner wheels, a remote-sensing mast, a
hexagonal high-gain antenna, a finned RTG and a stowed arm with a tool turret.
Proportions are illustrative, not any real vehicle's drawings.

Named pivots are what the web viewer animates:

    rover
    ├── body          static parts, joined
    ├── suspension    rocker, bogie, differential
    ├── mast_head     pans
    ├── hga           the antenna dish
    ├── steer_FL ...  steering actuators (corner wheels)
    │   └── wheel_FL  spins about its axle (local Y in Blender, -Z in glTF)
    └── status_light  the one accent, pulsed by the viewer
"""

from __future__ import annotations

import math

from mathutils import Matrix, Vector

import kit

WHEEL_R = 0.262
WHEEL_W = 0.40
AXLE_Y = 1.02
WHEEL_X = {"F": 1.08, "M": 0.05, "R": -0.98}
ARM_Y = AXLE_Y - WHEEL_W / 2 - 0.03  # the plane the suspension tubes run in
STEER_BASE = WHEEL_R + 0.03  # above the wheel centre, where the actuator starts
STEER_H = 0.21
STEER_TOP = WHEEL_R + STEER_BASE + STEER_H  # world height the rocker meets it at
MID_TOP = WHEEL_R + STEER_BASE + 0.05  # world height of the middle-wheel bracket


def palette() -> dict:
    s = kit.srgb
    return {
        "paint": kit.material("paint_white", s("#cfccc4"), roughness=0.5),
        "panel": kit.material("paint_panel", s("#c4c1b8"), roughness=0.6),
        "deck": kit.material("deck_gray", s("#3b3e44"), metallic=0.25, roughness=0.6),
        "alu": kit.material("aluminum", s("#b9bdc3"), metallic=1.0, roughness=0.3),
        "wheel": kit.material("aluminum_wheel", s("#a6aab0"), metallic=1.0, roughness=0.42),
        "ti": kit.material("titanium", s("#8f8b85"), metallic=1.0, roughness=0.36),
        "black": kit.material("anodized_black", s("#1e2024"), metallic=0.5, roughness=0.42),
        "rtg": kit.material("rtg_dark", s("#46474b"), metallic=0.6, roughness=0.42),
        "gold": kit.material("gold_mli", s("#d6a54e"), metallic=1.0, roughness=0.3),
        "glass": kit.material("lens_glass", s("#0a0c0f"), roughness=0.04, coat=1.0),
        "cable": kit.material("cable", s("#151515"), roughness=0.75),
        "copper": kit.material("copper", s("#b8743a"), metallic=1.0, roughness=0.34),
        "ink": kit.material("decal_ink", s("#2a2d32"), roughness=0.5),
        "accent": kit.material(
            "status_led", s("#c6f432"), roughness=0.3, emission=s("#c6f432"), emission_strength=6.0
        ),
    }


def beam(name, a, b, width, height, up, *, mat, parent, bevel=0.003):
    """A rectangular bar from ``a`` to ``b`` whose height faces ``up``."""
    a, b, up = Vector(a), Vector(b), Vector(up)
    x_axis = (b - a).normalized()
    z_axis = (up - x_axis * up.dot(x_axis)).normalized()
    y_axis = z_axis.cross(x_axis)
    obj = kit.box(name, ((b - a).length, width, height), mat=mat, parent=parent, bevel=bevel, segments=1)
    rot = Matrix((x_axis, y_axis, z_axis)).transposed().to_4x4()
    obj.matrix_basis = Matrix.Translation((a + b) / 2) @ rot
    return obj


def rod(name, a, b, radius, *, mat, parent, vertices=16, bevel=0.0):
    """A cylinder from ``a`` to ``b``."""
    loc, rot, length = kit.align_z(a, b)
    return kit.cylinder(name, radius, length, loc, rot, vertices=vertices, mat=mat, parent=parent, bevel=bevel)


# --- wheels ---------------------------------------------------------------------


def build_wheel(tag: str, side: int, m: dict, steer_parent) -> None:
    """One wheel: a spinning assembly under a fixed (steering) assembly."""
    out = side  # +Y is outboard on the left side, -Y on the right
    wheel = kit.pivot(f"wheel_{tag}", (0, 0, 0), steer_parent)

    # Rim: a slightly barrelled shell, thin-walled, open on the outboard face.
    profile = [
        (WHEEL_R - 0.014, -WHEEL_W / 2),
        (WHEEL_R * 0.982, -WHEEL_W / 2),
        (WHEEL_R * 0.996, -WHEEL_W / 4),
        (WHEEL_R, 0.0),
        (WHEEL_R * 0.996, WHEEL_W / 4),
        (WHEEL_R * 0.982, WHEEL_W / 2),
        (WHEEL_R - 0.014, WHEEL_W / 2),
        (WHEEL_R - 0.014, -WHEEL_W / 2),
    ]
    kit.lathe(f"rim_{tag}", profile, rotation=(math.pi / 2, 0, 0), vertices=64, mat=m["wheel"], parent=wheel)

    # Grousers: chevrons that bite into regolith.
    count = 24
    for k in range(count):
        theta = 2 * math.pi * k / count
        sweep = math.radians(5.5)

        def at(angle: float, y: float, r: float = WHEEL_R + 0.010) -> Vector:
            return Vector((r * math.cos(angle), y, r * math.sin(angle)))

        mid = at(theta, 0.0)
        for y_edge in (-WHEEL_W / 2 + 0.012, WHEEL_W / 2 - 0.012):
            edge = at(theta + sweep, y_edge)
            radial = Vector((math.cos(theta), 0, math.sin(theta)))
            beam(f"grouser_{tag}_{k}_{y_edge > 0}", edge, mid, 0.011, 0.02, radial, mat=m["wheel"], parent=wheel)

    # Hub and curved flexure spokes on the outboard face.
    face_y = out * (WHEEL_W / 2 - 0.03)
    kit.cylinder(f"hub_{tag}", 0.068, 0.11, (0, face_y - out * 0.03, 0), (math.pi / 2, 0, 0), mat=m["alu"], parent=wheel, bevel=0.006)
    kit.cylinder(f"hubcap_{tag}", 0.045, 0.02, (0, face_y + out * 0.03, 0), (math.pi / 2, 0, 0), mat=m["black"], parent=wheel, bevel=0.004)
    for s in range(6):
        base = 2 * math.pi * s / 6
        polar = [(0.07, base), (0.13, base + 0.32), (0.19, base + 0.12), (WHEEL_R - 0.018, base + 0.36)]
        pts = [(r * math.cos(a), face_y, r * math.sin(a)) for r, a in polar]
        kit.tube(f"spoke_{tag}_{s}", pts, 0.0095, mat=m["ti"], parent=wheel, smooth=True, resolution=8)


def build_corner(tag: str, x: float, side: int, m: dict, root, steerable: bool):
    inner = -side
    center = (x, side * AXLE_Y, WHEEL_R)
    steer = kit.pivot(f"steer_{tag}", center, root)
    # Drive motor inside the hub, on the inboard side.
    kit.cylinder(f"motor_{tag}", 0.078, 0.16, (0, inner * (WHEEL_W / 2 - 0.05), 0), (math.pi / 2, 0, 0), mat=m["black"], parent=steer, bevel=0.008)
    bracket_y = inner * (WHEEL_W / 2 + 0.03)
    # Local frame: origin at the wheel centre, so heights here are above the axle.
    if steerable:
        kit.cylinder(f"steer_act_{tag}", 0.074, STEER_H, (0, bracket_y, STEER_BASE + STEER_H / 2), mat=m["alu"], parent=steer, bevel=0.01)
        kit.cylinder(f"steer_cap_{tag}", 0.08, 0.03, (0, bracket_y, STEER_BASE + STEER_H + 0.012), mat=m["black"], parent=steer, bevel=0.006)
        beam(f"bracket_{tag}", (0, bracket_y, STEER_BASE + 0.02), (0, inner * (WHEEL_W / 2 - 0.02), 0.0), 0.07, 0.03, (1.0, 0, 0), mat=m["ti"], parent=steer)
    else:
        beam(f"bracket_{tag}", (0, bracket_y, STEER_BASE + 0.05), (0, inner * (WHEEL_W / 2 - 0.02), 0.0), 0.07, 0.03, (1.0, 0, 0), mat=m["ti"], parent=steer)
    build_wheel(tag, side, m, steer)


# --- suspension -----------------------------------------------------------------


def build_suspension(m: dict, root) -> None:
    sus = kit.pivot("suspension", (0, 0, 0), root)
    r = 0.031
    for side in (1, -1):
        y = side * ARM_Y
        front_top = (WHEEL_X["F"], y, STEER_TOP)
        rocker_pivot = (0.30, side * 0.80, 0.98)
        bogie_pivot = (-0.40, side * 0.83, 0.74)
        middle_top = (WHEEL_X["M"], y, MID_TOP)
        rear_top = (WHEEL_X["R"], y, STEER_TOP)

        kit.tube(f"rocker_{side}", [front_top, (0.82, side * 0.79, 0.90), rocker_pivot, (0.0, side * 0.81, 0.90), bogie_pivot], r, mat=m["ti"], parent=sus)
        kit.tube(f"bogie_{side}", [middle_top, (-0.14, side * 0.81, 0.70), bogie_pivot, (-0.68, side * 0.82, 0.78), rear_top], r * 0.92, mat=m["ti"], parent=sus)
        for name, p, rad in (("rpivot", rocker_pivot, 0.062), ("bpivot", bogie_pivot, 0.055)):
            kit.cylinder(f"{name}_{side}", rad, 0.12, p, (math.pi / 2, 0, 0), mat=m["black"], parent=sus, bevel=0.008)
            kit.cylinder(f"{name}_cap_{side}", rad * 0.6, 0.135, p, (math.pi / 2, 0, 0), mat=m["alu"], parent=sus, bevel=0.004)
        # Harness along the rocker: the cabling that makes it read as hardware.
        kit.tube(f"harness_{side}", [(1.02, side * 0.755, 0.74), (0.80, side * 0.755, 0.86), (0.32, side * 0.755, 0.94), (-0.38, side * 0.79, 0.71), (-0.92, side * 0.755, 0.74)], 0.009, mat=m["cable"], parent=sus, smooth=True)

    # Differential: a bar across the deck linking the two rockers.
    kit.cylinder("diff_bar", 0.028, 1.46, (0.30, 0, 1.30), (math.pi / 2, 0, 0), mat=m["ti"], parent=sus, bevel=0.004)
    kit.cylinder("diff_pivot", 0.06, 0.07, (0.30, 0, 1.27), mat=m["black"], parent=sus, bevel=0.006)
    for side in (1, -1):
        rod(f"diff_link_{side}", (0.30, side * 0.72, 1.30), (0.40, side * 0.80, 1.02), 0.014, mat=m["alu"], parent=sus)
        kit.sphere(f"diff_ball_{side}", 0.024, (0.30, side * 0.72, 1.30), mat=m["black"], parent=sus)


# --- body -----------------------------------------------------------------------


def build_body(m: dict, root):
    body = kit.pivot("body", (0, 0, 0), root)
    kit.box("chassis", (1.90, 1.25, 0.50), (0.05, 0, 0.93), mat=m["paint"], parent=body, bevel=0.028, segments=3)
    kit.box("deck", (1.98, 1.31, 0.05), (0.05, 0, 1.205), mat=m["deck"], parent=body, bevel=0.012, segments=2)
    kit.box("belly", (1.70, 1.05, 0.06), (0.05, 0, 0.665), mat=m["black"], parent=body, bevel=0.01)

    # Side panels with a hairline gap read as separate access covers.
    for side in (1, -1):
        y = side * 0.631
        for i, (x, w) in enumerate(((-0.55, 0.62), (0.12, 0.56), (0.70, 0.46))):
            kit.box(f"panel_{side}_{i}", (w, 0.014, 0.36), (x, y, 0.93), mat=m["panel"], parent=body, bevel=0.006)
        rot = (math.pi / 2, 0, math.pi) if side > 0 else (math.pi / 2, 0, 0)
        kit.text(f"decal_{side}", "ASTRASYNTH", 0.075, (0.12, side * 0.6395, 1.03), rot, mat=m["ink"], parent=body, extrude=0.0012)
        kit.text(f"decal_id_{side}", "MSX-02", 0.05, (0.12, side * 0.6395, 0.955), rot, mat=m["ink"], parent=body, extrude=0.0012)

    # Hazard cameras, two pairs front and rear, looking down at the ground ahead.
    for x, facing in ((1.005, 1), (-0.905, -1)):
        for y in (-0.36, -0.20, 0.20, 0.36):
            cam_box = kit.box(f"hazcam_{x}_{y}", (0.07, 0.09, 0.07), (x + facing * 0.03, y, 0.76), (0, facing * 0.35, 0), mat=m["black"], parent=body, bevel=0.006)
            lens_loc = (x + facing * 0.066, y, 0.75)
            kit.cylinder(f"hazlens_{x}_{y}", 0.019, 0.02, lens_loc, (0, math.pi / 2 + facing * 0.35, 0), mat=m["glass"], parent=body, bevel=0.003)
            del cam_box

    # Deck payload: instrument boxes, some wrapped in gold multi-layer insulation.
    kit.box("sam", (0.40, 0.34, 0.15), (-0.12, -0.24, 1.305), mat=m["paint"], parent=body, bevel=0.012)
    kit.box("chemin", (0.30, 0.24, 0.12), (0.30, 0.30, 1.29), mat=m["gold"], parent=body, bevel=0.01, segments=3)
    kit.box("avionics", (0.30, 0.26, 0.09), (-0.58, -0.14, 1.275), mat=m["gold"], parent=body, bevel=0.01, segments=3)
    kit.box("radiator", (0.42, 0.50, 0.03), (-0.62, 0.34, 1.245), mat=m["alu"], parent=body, bevel=0.006)
    for i in range(7):
        kit.box(f"radiator_fin_{i}", (0.42, 0.012, 0.03), (-0.62, 0.13 + i * 0.07, 1.27), mat=m["alu"], parent=body, bevel=0.002)
    kit.tube("deck_cable_a", [(-0.12, -0.06, 1.24), (0.10, 0.10, 1.245), (0.30, 0.17, 1.24)], 0.011, mat=m["cable"], parent=body, smooth=True)
    kit.tube("deck_cable_b", [(-0.58, -0.02, 1.24), (-0.40, -0.10, 1.25), (-0.30, -0.24, 1.24)], 0.011, mat=m["cable"], parent=body, smooth=True)

    # Low-gain and UHF antennas.
    kit.cylinder("lga", 0.035, 0.26, (-0.48, 0.10, 1.36), mat=m["alu"], parent=body, bevel=0.004)
    kit.cylinder("lga_tip", 0.05, 0.05, (-0.48, 0.10, 1.51), mat=m["paint"], parent=body, radius_top=0.012)
    kit.cylinder("uhf", 0.05, 0.30, (-0.80, -0.45, 1.38), mat=m["paint"], parent=body, bevel=0.006)
    for i in range(6):
        kit.cylinder(f"uhf_coil_{i}", 0.055, 0.012, (-0.80, -0.45, 1.27 + i * 0.045), mat=m["copper"], parent=body, cap=False)

    build_rtg(m, body)
    build_arm(m, body)

    # Mast base and tube; the head pans on its own pivot.
    kit.box("mast_shoulder", (0.16, 0.16, 0.10), (0.72, -0.40, 1.28), mat=m["black"], parent=body, bevel=0.01)
    kit.cylinder("mast_tube", 0.048, 0.78, (0.72, -0.40, 1.70), mat=m["paint"], parent=body, bevel=0.004)
    kit.cylinder("mast_collar", 0.062, 0.06, (0.72, -0.40, 1.55), mat=m["black"], parent=body, bevel=0.006)
    # Weather booms on the mast.
    for i, ang in enumerate((math.radians(20), math.radians(140))):
        end = (0.72 + 0.22 * math.cos(ang), -0.40 + 0.22 * math.sin(ang), 1.66)
        rod(f"boom_{i}", (0.72, -0.40, 1.66), end, 0.01, mat=m["alu"], parent=body)
        kit.box(f"boom_sensor_{i}", (0.06, 0.03, 0.03), end, (0, 0, ang), mat=m["paint"], parent=body, bevel=0.004)
    return body


def build_rtg(m: dict, body) -> None:
    frame = kit.pivot("rtg_frame", (-1.13, 0, 1.04), body)
    frame.rotation_euler = (0, math.radians(-55), 0)
    kit.cylinder("rtg_core", 0.17, 0.62, (0, 0, 0), mat=m["rtg"], parent=frame, bevel=0.01, vertices=40)
    for end in (-0.32, 0.32):
        kit.cylinder(f"rtg_cap_{end}", 0.2, 0.04, (0, 0, end), mat=m["black"], parent=frame, bevel=0.008, vertices=40)
    for i in range(8):
        a = 2 * math.pi * i / 8 + math.pi / 8
        kit.box(f"rtg_fin_{i}", (0.12, 0.01, 0.56), (0.23 * math.cos(a), 0.23 * math.sin(a), 0), (0, 0, a), mat=m["rtg"], parent=frame, bevel=0.003)
    for side in (1, -1):
        rod(f"rtg_strut_{side}", (-0.93, side * 0.32, 0.88), (-1.05, side * 0.16, 0.98), 0.016, mat=m["ti"], parent=body)
        rod(f"rtg_strut_hi_{side}", (-0.93, side * 0.32, 1.16), (-1.0, side * 0.14, 1.16), 0.016, mat=m["ti"], parent=body)


def build_arm(m: dict, body) -> None:
    """The arm, stowed across the front with its turret parked high."""
    shoulder = (1.02, 0.42, 0.80)
    hinge = (1.10, 0.42, 0.80)
    elbow = (1.24, -0.28, 0.80)
    wrist = (1.16, 0.18, 0.98)
    kit.cylinder("arm_azimuth", 0.07, 0.13, shoulder, mat=m["black"], parent=body, bevel=0.008)
    kit.cylinder("arm_shoulder", 0.064, 0.15, hinge, (math.pi / 2, 0, 0), mat=m["alu"], parent=body, bevel=0.008)
    rod("arm_upper", hinge, elbow, 0.042, mat=m["paint"], parent=body, vertices=20)
    kit.cylinder("arm_elbow", 0.058, 0.13, elbow, mat=m["alu"], parent=body, bevel=0.008)
    rod("arm_fore", elbow, wrist, 0.036, mat=m["paint"], parent=body, vertices=20)
    turret = kit.pivot("turret_frame", wrist, body)
    turret.rotation_euler = (math.radians(90), 0, math.radians(15))
    kit.cylinder("turret_hub", 0.11, 0.12, (0, 0, 0), mat=m["black"], parent=turret, bevel=0.01)
    for i, (kind, ang) in enumerate((("drill", 0.0), ("camera", 1.3), ("spectrometer", 2.5), ("brush", 3.8), ("sensor", 5.0))):
        x, y = 0.13 * math.cos(ang), 0.13 * math.sin(ang)
        if kind == "drill":
            kit.cylinder("drill_body", 0.05, 0.16, (x, y, -0.02), mat=m["alu"], parent=turret, bevel=0.006)
            kit.cylinder("drill_bit", 0.016, 0.12, (x, y, -0.16), mat=m["ti"], parent=turret, radius_top=0.016)
            kit.cylinder("drill_tip", 0.016, 0.03, (x, y, -0.235), mat=m["ti"], parent=turret, radius_top=0.002)
        elif kind == "camera":
            kit.box("mahli", (0.08, 0.08, 0.1), (x, y, -0.01), mat=m["paint"], parent=turret, bevel=0.008)
            kit.cylinder("mahli_lens", 0.022, 0.03, (x, y, -0.07), mat=m["glass"], parent=turret, bevel=0.003)
        elif kind == "spectrometer":
            kit.cylinder("apxs", 0.035, 0.12, (x, y, -0.04), mat=m["alu"], parent=turret, bevel=0.005)
        elif kind == "brush":
            kit.box("brush", (0.06, 0.05, 0.08), (x, y, -0.02), mat=m["black"], parent=turret, bevel=0.006)
        else:
            kit.cylinder("sensor", 0.03, 0.09, (x, y, -0.02), mat=m["gold"], parent=turret, bevel=0.004)


def build_mast_head(m: dict, root) -> None:
    head = kit.pivot("mast_head", (0.72, -0.40, 2.10), root)
    kit.box("head_body", (0.24, 0.44, 0.19), (0.0, 0, 0.0), mat=m["paint"], parent=head, bevel=0.014, segments=3)
    kit.box("head_back", (0.10, 0.36, 0.14), (-0.15, 0, 0.0), mat=m["black"], parent=head, bevel=0.01)
    # A large telescope aperture in the middle, camera pairs either side.
    kit.cylinder("tele_ring", 0.072, 0.05, (0.13, 0, 0.0), (0, math.pi / 2, 0), mat=m["alu"], parent=head, bevel=0.006, vertices=40)
    kit.cylinder("tele_glass", 0.058, 0.02, (0.152, 0, 0.0), (0, math.pi / 2, 0), mat=m["glass"], parent=head, vertices=40)
    for y in (-0.15, 0.15):
        kit.cylinder(f"mastcam_{y}", 0.036, 0.07, (0.14, y, 0.01), (0, math.pi / 2, 0), mat=m["black"], parent=head, bevel=0.005)
        kit.cylinder(f"mastcam_glass_{y}", 0.025, 0.02, (0.178, y, 0.01), (0, math.pi / 2, 0), mat=m["glass"], parent=head)
    for y in (-0.19, 0.19):
        kit.box(f"navcam_{y}", (0.08, 0.06, 0.05), (0.08, y, 0.12), mat=m["black"], parent=head, bevel=0.006)
        kit.cylinder(f"navcam_glass_{y}", 0.016, 0.02, (0.125, y, 0.12), (0, math.pi / 2, 0), mat=m["glass"], parent=head)
    kit.box("calibration", (0.05, 0.05, 0.012), (0.0, 0.0, 0.103), mat=m["gold"], parent=head, bevel=0.003)


def build_hga(m: dict, root, body) -> None:
    kit.cylinder("hga_post", 0.04, 0.22, (-0.30, 0.42, 1.34), mat=m["alu"], parent=body, bevel=0.005)
    kit.box("hga_yoke", (0.08, 0.20, 0.06), (-0.30, 0.42, 1.46), mat=m["black"], parent=body, bevel=0.008)
    hga = kit.pivot("hga", (-0.30, 0.42, 1.52), root)
    dish = kit.pivot("hga_dish", (0, 0, 0.0), hga)
    dish.rotation_euler = (0, math.radians(-38), math.radians(25))
    kit.cylinder("hga_panel", 0.30, 0.035, (0, 0, 0.04), mat=m["paint"], parent=dish, vertices=6, bevel=0.008)
    kit.cylinder("hga_face", 0.27, 0.01, (0, 0, 0.061), mat=m["panel"], parent=dish, vertices=6)
    kit.cylinder("hga_back", 0.22, 0.03, (0, 0, 0.005), mat=m["black"], parent=dish, vertices=6, bevel=0.004)
    kit.cylinder("hga_gimbal", 0.045, 0.12, (0, 0, -0.03), (math.pi / 2, 0, 0), mat=m["alu"], parent=dish, bevel=0.005)


def build() -> list:
    """Build the whole rover; returns the objects to export."""
    m = palette()
    root = kit.pivot("rover", (0, 0, 0))
    body = build_body(m, root)
    build_suspension(m, root)
    build_mast_head(m, root)
    build_hga(m, root, body)
    for wheel_key, x in WHEEL_X.items():
        for side, letter in ((1, "L"), (-1, "R")):
            build_corner(f"{wheel_key}{letter}", x, side, m, root, steerable=wheel_key != "M")
    light = kit.sphere("status_light", 0.032, (0.98, 0.0, 1.25), subdivisions=3, mat=m["accent"], parent=root)

    finish(root)
    return [root, *root.children_recursive, light]


def _descendant_meshes(node, stop: set) -> list:
    found = []
    for child in node.children:
        if child.name in stop:
            continue
        if child.type in {"MESH", "CURVE", "FONT"}:
            found.append(child)
        found.extend(_descendant_meshes(child, stop))
    return found


def finish(root) -> None:
    """Join each articulated group into one mesh, leaving the pivots in place."""
    import bpy

    pivots = ["body", "suspension", "mast_head", "hga"]
    pivots += [f"steer_{k}{s}" for k in WHEEL_X for s in "LR"]
    pivots += [f"wheel_{k}{s}" for k in WHEEL_X for s in "LR"]
    stop = set(pivots) | {"status_light"}
    for name in pivots:
        node = bpy.data.objects[name]
        meshes = _descendant_meshes(node, stop)
        if not meshes:
            continue
        holder = kit.pivot(f"{name}__join", (0, 0, 0))
        bpy.context.view_layer.update()
        for obj in meshes:
            world = obj.matrix_world.copy()
            obj.parent = holder
            obj.matrix_world = world
        joined = kit.bake_group(holder, f"{name}_mesh")
        world = joined.matrix_world.copy()
        joined.parent = node
        joined.matrix_world = world
        bpy.data.objects.remove(holder)
    # Helper frames that only positioned parts are empty now.
    for name in ("rtg_frame", "turret_frame", "hga_dish"):
        obj = bpy.data.objects.get(name)
        if obj is not None and not obj.children:
            bpy.data.objects.remove(obj)
