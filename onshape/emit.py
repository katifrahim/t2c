"""Emit MCP tool-call steps from the engine-neutral IR.

Output matches the `templates.steps` contract (web/lib/template-extract.js):
    [{ "step": "Step N", "toolName": "...", "input": {operations, init_params, store_as} }]

Strategy: fuse a sketch with the solid op that consumes it into ONE workplane_api
call (init on the plane → draw the profile → extrude/revolve), which is how the
agent itself builds and keeps each step self-contained. Profiles with inner loops
rely on CadQuery's even-odd rule to become holes (verified: concentric circles →
annulus).
"""
from __future__ import annotations

from onshape import ir


def _plane_init(plane: ir.Plane) -> dict:
    if plane.is_canonical():
        return {"plane": plane.name}
    return {"plane": {"_type": "Plane", "origin": plane.origin,
                      "xDir": plane.x_dir, "normal": plane.normal}}


def _curve_ops(curves: list[ir.Curve]) -> list[dict]:
    """One closed loop of curves → workplane 2D operations."""
    ops: list[dict] = []
    for c in curves:
        d = c.data
        if c.kind == "circle":
            cx, cy = d["center"]
            ops.append({"method": "moveTo", "args": [cx, cy]})
            ops.append({"method": "circle", "args": [d["radius"]]})
        elif c.kind == "line":
            ops.append({"method": "lineTo", "args": list(d["end"])})
        elif c.kind == "arc":
            # 3-point arc: through a midpoint to the endpoint.
            ops.append({"method": "threePointArc", "args": [list(d["mid"]), list(d["end"])]})
    return ops


def _profile_ops(profiles: list[ir.Profile]) -> list[dict]:
    """All regions of a sketch → 2D ops. Circles are self-contained; open chains of
    line/arc are wrapped moveTo(start)…close so each region is a closed wire."""
    ops: list[dict] = []
    for prof in profiles:
        curves = prof.curves
        if len(curves) == 1 and curves[0].kind == "circle":
            ops += _curve_ops(curves)
            continue
        if not curves:
            continue
        start = curves[0].data.get("start")
        if start:
            ops.append({"method": "moveTo", "args": list(start)})
        ops += _curve_ops(curves)
        ops.append({"method": "close"})
    return ops


_COMBINE = {"new": False, "add": True, "cut": "cut", "intersect": "i"}


def _emit_extrude(sketch: ir.Sketch, ex: ir.Extrude, store_as: str) -> dict:
    ops = _profile_ops(sketch.profiles)
    ex_op = {"method": "extrude", "params": {
        "until": ex.distance, "combine": _COMBINE.get(ex.op, False), "both": ex.symmetric,
    }}
    if ex.taper is not None:
        ex_op["params"]["taper"] = ex.taper
    ops.append(ex_op)
    return {"operations": ops, "init_params": _plane_init(sketch.plane), "store_as": store_as}


def _emit_revolve(sketch: ir.Sketch, rev: ir.Revolve, store_as: str) -> dict:
    ops = _profile_ops(sketch.profiles)
    ops.append({"method": "revolve", "params": {
        "angleDegrees": rev.angle,
        "axisStart": rev.axis_start, "axisEnd": rev.axis_end,
        "combine": _COMBINE.get(rev.op, False),
    }})
    return {"operations": ops, "init_params": _plane_init(sketch.plane), "store_as": store_as}


def emit_model(model: ir.Model) -> list[dict]:
    """Walk the IR, fusing each sketch into the op that consumes it, and produce the
    `templates.steps` array."""
    steps: list[dict] = []
    by_ref = {op.source.get("id"): op for op in model.ops if isinstance(op, ir.Sketch)}

    def add(tool: str, payload: dict):
        steps.append({"step": f"Step {len(steps) + 1}", "toolName": tool, "input": payload})

    for i, op in enumerate(model.ops):
        if isinstance(op, ir.Sketch):
            continue  # emitted when consumed
        name = f"body_{i}"
        if isinstance(op, ir.Extrude):
            sk = by_ref.get(op.profile_ref)
            if sk is None:
                continue
            add("workplane_api", _emit_extrude(sk, op, name))
        elif isinstance(op, ir.Revolve):
            sk = by_ref.get(op.profile_ref)
            if sk is None:
                continue
            add("workplane_api", _emit_revolve(sk, op, name))
    return steps
