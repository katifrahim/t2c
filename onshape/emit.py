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
    # Prefer the extrude's own rollback-resolved regions; fall back to the sketch.
    ops = _profile_ops(ex.profiles if ex.profiles is not None else sketch.profiles)
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
    """One 3D point -> NearestToPointSelector; several -> SumSelector tree.
    Near-duplicate points are collapsed (they'd make a degenerate selector)."""
    uniq: list[list[float]] = []
    for p in points:
        if not any(abs(p[0] - q[0]) < 1e-4 and abs(p[1] - q[1]) < 1e-4
                   and abs(p[2] - q[2]) < 1e-4 for q in uniq):
            uniq.append(p)
    sel = {"_type": "NearestToPointSelector", "pnt": uniq[0]}
    for p in uniq[1:]:
        sel = {"_type": "SumSelector", "left": sel,
               "right": {"_type": "NearestToPointSelector", "pnt": p}}
    return sel


def _dist3(a, b) -> float:
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2) ** 0.5


def _rotate_pt(p, origin, axis, deg):
    import math
    nrm = (axis[0] ** 2 + axis[1] ** 2 + axis[2] ** 2) ** 0.5 or 1.0
    ux, uy, uz = axis[0] / nrm, axis[1] / nrm, axis[2] / nrm
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    v = [p[0] - origin[0], p[1] - origin[1], p[2] - origin[2]]
    dot = ux * v[0] + uy * v[1] + uz * v[2]
    cx, cy, cz = uy * v[2] - uz * v[1], uz * v[0] - ux * v[2], ux * v[1] - uy * v[0]
    r = [v[0] * c + cx * s + ux * dot * (1 - c),
         v[1] * c + cy * s + uy * dot * (1 - c),
         v[2] * c + cz * s + uz * dot * (1 - c)]
    return [r[0] + origin[0], r[1] + origin[1], r[2] + origin[2]]


def _new_centroids(before, after, tol=0.6):
    """'after' centroids not matching any 'before' centroid (newly created)."""
    return [c for c in after if not any(_dist3(c, b) < tol for b in before)]


class _Bodies:
    """Live bodies as [onshape_centroid, mcp_name], tracked across features."""

    def __init__(self):
        self.items: list[list] = []
        self._n = 0

    def name(self) -> str:
        self._n += 1
        return f"body{self._n}"

    def nearest(self, centroid):
        best, bd = None, 1e18
        for it in self.items:
            d = _dist3(it[0], centroid)
            if d < bd:
                bd, best = d, it
        return best

    def rekey(self, after: list):
        """Re-assign each live body the nearest unused 'after' centroid (bodies shift
        when material is added/cut)."""
        used = set()
        for it in self.items:
            best, bi, bd = None, -1, 1e18
            for i, c in enumerate(after):
                if i not in used and _dist3(it[0], c) < bd:
                    bd, best, bi = _dist3(it[0], c), c, i
            if best is not None:
                it[0] = best
                used.add(bi)


def emit_model(model: ir.Model) -> list[dict]:
    """Produce `templates.steps`, using the rollback body-flow (when present) to
    target each add/cut/pattern/union at the correct bodies. Falls back to simple
    most-recent-body semantics without a flow."""
    steps: list[dict] = []
    by_ref = {op.source.get("id"): op for op in model.ops if isinstance(op, ir.Sketch)}
    flow = model.body_flow or {}
    B = _Bodies()

    def add(payload: dict):
        steps.append({"step": f"Step {len(steps) + 1}", "toolName": "workplane_api",
                      "input": payload})

    def boolean_into(item, method: str, tool: str):
        if item is None:
            return
        body = B.name()
        add({"operations": [{"method": method, "args": [{"_ref": tool}]}],
             "start_from": item[1], "store_as": body})
        item[1] = body

    for op in model.ops:
        if isinstance(op, ir.Sketch):
            continue
        i = op.source.get("index")
        before = flow.get(i, []) if flow else []
        after = flow.get(i + 1, []) if (flow and i is not None) else []

        if isinstance(op, (ir.Extrude, ir.Revolve)):
            sk = by_ref.get(op.profile_ref)
            if sk is None:
                continue
            tool = B.name()
            add(_emit_extrude(sk, op, tool) if isinstance(op, ir.Extrude)
                else _emit_revolve(sk, op, tool))
            newc = _new_centroids(before, after)
            if op.op == "new" or not B.items:
                B.items.append([newc[0] if newc else [0, 0, 0], tool])
            elif len(after) > len(before):  # ADD that made a separate body
                B.items.append([newc[0] if newc else [0, 0, 0], tool])
            elif op.op == "add":  # merged into the body it touches
                boolean_into(B.nearest(newc[0]) if newc else (B.items[-1] if B.items else None),
                             "union", tool)
            else:  # cut / intersect: apply to every body (non-overlap is a no-op)
                for it in list(B.items):
                    boolean_into(it, _BOOL[op.op], tool)
            if after:
                B.rekey(after)

        elif isinstance(op, ir.CircularPattern) and B.items:
            _emit_pattern(op, B, add, before, after)
            if after:
                B.rekey(after)

        elif isinstance(op, ir.Boolean) and len(B.items) > 1:
            base, body = B.items[0], B.name()
            add({"operations": [{"method": _BOOL.get(op.op, "union"), "args": [{"_ref": it[1]}]}
                                for it in B.items[1:]],
                 "start_from": base[1], "store_as": body})
            B.items = [[after[0] if after else base[0], body]]

        elif isinstance(op, (ir.Fillet, ir.Chamfer)) and op.edge_points and B.items:
            body, meth = B.name(), ("fillet" if isinstance(op, ir.Fillet) else "chamfer")
            amt = op.radius if isinstance(op, ir.Fillet) else op.distance
            add({"operations": [{"method": "edges", "args": [_point_selector(op.edge_points)]},
                                {"method": meth, "args": [amt]}],
                 "start_from": B.items[-1][1], "store_as": body})
            B.items[-1][1] = body
    return steps


def _emit_pattern(op: ir.CircularPattern, B: _Bodies, add, before, after):
    """Rotate-copy the source body into `count` instances about the axis; the source
    is the live body whose rotations land on the newly-created centroids."""
    k = op.count if op.count > 1 else 1
    step = (op.angle / op.count) if op.equal_space else op.angle
    a0, ad = op.axis_origin, op.axis_dir
    a1 = [a0[0] + ad[0], a0[1] + ad[1], a0[2] + ad[2]]
    new = _new_centroids(before, after)

    def emit_copies(item):
        for j in range(1, k):
            copy = B.name()
            add({"operations": [{"method": "rotate", "args": [a0, a1, j * step]}],
                 "start_from": item[1], "store_as": copy})
            B.items.append([_rotate_pt(item[0], a0, ad, j * step), copy])

    if new and B.items:
        for it in list(B.items):
            rots = [_rotate_pt(it[0], a0, ad, j * step) for j in range(1, k)]
            if all(any(_dist3(r, c) < 0.6 for c in new) for r in rots):
                emit_copies(it)
                return
    emit_copies(B.items[-1])  # fallback: pattern the most-recent body
