"""The goal beacon: a survey tripod with a mast, a small solar panel and a lamp.

The lamp is its own node (``beacon_light``) so the viewer can pulse it.
"""

from __future__ import annotations

import math

import kit


def build() -> list:
    s = kit.srgb
    alu = kit.material("beacon_alu", s("#b9bdc3"), metallic=1.0, roughness=0.32)
    dark = kit.material("beacon_dark", s("#1e2024"), metallic=0.5, roughness=0.45)
    paint = kit.material("beacon_paint", s("#cfccc4"), roughness=0.5)
    cell = kit.material("beacon_cell", s("#14213a"), metallic=0.3, roughness=0.18, coat=1.0)
    lamp = kit.material("beacon_lamp", s("#e8ecef"), roughness=0.2, emission=s("#e8ecef"), emission_strength=8.0)

    root = kit.pivot("beacon", (0, 0, 0))
    frame = kit.pivot("beacon_frame", (0, 0, 0), root)
    hub = (0, 0, 0.62)
    for i in range(3):
        a = 2 * math.pi * i / 3 + math.pi / 6
        foot = (0.42 * math.cos(a), 0.42 * math.sin(a), 0.02)
        loc, rot, length = kit.align_z(foot, hub)
        kit.cylinder(f"leg_{i}", 0.016, length, loc, rot, vertices=12, mat=alu, parent=frame)
        kit.cylinder(f"foot_{i}", 0.05, 0.02, foot, mat=dark, parent=frame, bevel=0.004)
    kit.cylinder("collar", 0.045, 0.08, hub, mat=dark, parent=frame, bevel=0.006)
    kit.cylinder("mast", 0.022, 1.0, (0, 0, 1.1), mat=paint, parent=frame, bevel=0.003)
    kit.box("electronics", (0.16, 0.12, 0.12), (0, 0, 0.78), mat=paint, parent=frame, bevel=0.01)
    kit.box("panel", (0.32, 0.22, 0.012), (0.05, 0, 0.95), (0, math.radians(-30), 0), mat=cell, parent=frame, bevel=0.003)
    kit.box("panel_frame", (0.34, 0.24, 0.008), (0.05, 0, 0.944), (0, math.radians(-30), 0), mat=alu, parent=frame, bevel=0.002)
    kit.cylinder("whip", 0.004, 0.35, (0.06, 0.05, 1.75), mat=dark, parent=frame)
    kit.cylinder("lamp_base", 0.05, 0.05, (0, 0, 1.62), mat=dark, parent=frame, bevel=0.005)
    light = kit.sphere("beacon_light", 0.045, (0, 0, 1.68), subdivisions=3, mat=lamp, parent=root)

    kit.bake_group(frame, "beacon_mesh")
    return [root, *root.children_recursive, light]
