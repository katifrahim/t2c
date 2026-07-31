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


# Every extrude/revolve builds an ISOLATED solid (combine=False); booleans between
# solids are emitted as explicit steps against the running "current" body. This one
# uniform model handles multi-extrude add/cut bodies and (later) booleanBodies.
_BOOL = {"add": "union", "cut": "cut", "intersect": "intersect"}


def _emit_extrude(sketch: ir.Sketch, ex: ir.Extrude, store_as: str) -> dict:
    ops = _profile_ops(sketch.profiles)
    ex_op = {"method": "extrude", "params": {"until": ex.distance, "combine": False,
                                             "both": ex.symmetric}}
    if ex.taper is not None:
        ex_op["params"]["taper"] = ex.taper
    ops.append(ex_op)
    return {"operations": ops, "init_params": _plane_init(sketch.plane), "store_as": store_as}


def _emit_revolve(sketch: ir.Sketch, rev: ir.Revolve, store_as: str) -> dict:
    ops = _profile_ops(sketch.profiles)
    ops.append({"method": "revolve", "params": {
        "angleDegrees": rev.angle, "axisStart": rev.axis_start,
        "axisEnd": rev.axis_end, "combine": False,
    }})
    return {"operations": ops, "init_params": _plane_init(sketch.plane), "store_as": store_as}


def _point_selector(points: list[list[float]]) -> dict:
    """One 3D point -> NearestToPointSelector; several -> SumSelector tree."""
    sel = {"_type": "NearestToPointSelector", "pnt": points[0]}
    for p in points[1:]:
        sel = {"_type": "SumSelector", "left": sel,
               "right": {"_type": "NearestToPointSelector", "pnt": p}}
    return sel


def emit_model(model: ir.Model) -> list[dict]:
    """Walk the IR and produce the `templates.steps` array.

    Tracks a LIST of live bodies. Extrude NEW appends a body; ADD/CUT/INTERSECT
    boolean the new tool into the most-recent body; circular pattern appends rotated
    copies of the most-recent body; booleanBodies unions all live bodies into one;
    fillet/chamfer modify the most-recent body. The last stored object is the final
    model (what verification reads)."""
    steps: list[dict] = []
    by_ref = {op.source.get("id"): op for op in model.ops if isinstance(op, ir.Sketch)}
    n = 0

    def add(payload: dict):
        steps.append({"step": f"Step {len(steps) + 1}", "toolName": "workplane_api",
                      "input": payload})

    def name():
        nonlocal n
        n += 1
        return f"body{n}"

    bodies: list[str] = []  # live bodies, most-recent last

    for op in model.ops:
        if isinstance(op, ir.Sketch):
            continue  # emitted when consumed
        if isinstance(op, (ir.Extrude, ir.Revolve)):
            sk = by_ref.get(op.profile_ref)
            if sk is None:
                continue
            tool = name()
            add(_emit_extrude(sk, op, tool) if isinstance(op, ir.Extrude)
                else _emit_revolve(sk, op, tool))
            if op.op == "new" or not bodies:
                bodies.append(tool)
            else:  # boolean the tool into the most-recent body
                body = name()
                add({"operations": [{"method": _BOOL[op.op], "args": [{"_ref": tool}]}],
                     "start_from": bodies[-1], "store_as": body})
                bodies[-1] = body
        elif isinstance(op, ir.CircularPattern) and bodies:
            target = bodies[-1]
            k = op.count if op.count > 1 else 1
            step_ang = (op.angle / op.count) if op.equal_space else op.angle
            a0 = op.axis_origin
            a1 = [a0[0] + op.axis_dir[0], a0[1] + op.axis_dir[1], a0[2] + op.axis_dir[2]]
            for i in range(1, k):  # instance 0 is the original body
                copy = name()
                add({"operations": [{"method": "rotate", "args": [a0, a1, i * step_ang]}],
                     "start_from": target, "store_as": copy})
                bodies.append(copy)
        elif isinstance(op, ir.Boolean) and bodies:
            if len(bodies) > 1:
                body = name()
                ops = [{"method": _BOOL.get(op.op, "union"), "args": [{"_ref": b}]}
                       for b in bodies[1:]]
                add({"operations": ops, "start_from": bodies[0], "store_as": body})
                bodies = [body]
        elif isinstance(op, (ir.Fillet, ir.Chamfer)) and op.edge_points and bodies:
            body = name()
            meth = "fillet" if isinstance(op, ir.Fillet) else "chamfer"
            amt = op.radius if isinstance(op, ir.Fillet) else op.distance
            add({"operations": [{"method": "edges", "args": [_point_selector(op.edge_points)]},
                                {"method": meth, "args": [amt]}],
                 "start_from": bodies[-1], "store_as": body})
            bodies[-1] = body
    return steps
