"""Normalize an Onshape Part Studio (features + solved sketches) into the IR.

Reads the raw `/features` and `/sketches?includeGeometry=true` responses. Sketch
geometry is already solved (2D coordinates in the sketch's local frame, in metres);
feature dimensions come from parametric expressions. Feature types we can't yet
translate are recorded on `Model.unsupported` (never silently dropped) so the
pipeline can rollback-truncate to the largest verifiable prefix.
"""
from __future__ import annotations

import math
import re

from onshape import ir

MM = 1e3  # metres -> millimetres

# Onshape length units -> millimetres.
_UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "meter": 1000.0, "millimeter": 1.0,
            "centimeter": 10.0, "in": 25.4, "inch": 25.4, "ft": 304.8, "foot": 304.8, "yd": 914.4}
_NUM_UNIT = re.compile(r"^\s*(-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*([a-zA-Z]*)\s*$")

# Onshape operationType enum -> IR boolean op.
_OP = {"NEW": "new", "ADD": "add", "REMOVE": "cut", "INTERSECT": "intersect"}


def _msg(x: dict) -> dict:
    return x.get("message", x)


def parse_length_mm(expr: str | None) -> float | None:
    """'1.80 mm' / '0.5 in' / '25' -> millimetres. None if it isn't a plain
    number+unit (e.g. references a variable) — the caller then flags it."""
    if not expr:
        return None
    m = _NUM_UNIT.match(expr)
    if not m:
        return None
    val, unit = float(m.group(1)), m.group(2).lower()
    if not unit:
        return val  # bare number: Onshape default length unit is mm in these dumps
    if unit not in _UNIT_MM:
        return None
    return val * _UNIT_MM[unit]


def _params(feat: dict) -> dict:
    """parameterId -> parameter message dict."""
    out = {}
    for p in _msg(feat).get("parameters", []):
        pm = _msg(p)
        if pm.get("parameterId"):
            out[pm["parameterId"]] = pm
    return out


def _enum(p: dict | None):
    return p.get("value") if p else None


def _bool(p: dict | None) -> bool:
    return bool(p.get("value")) if p else False


# --- sketch geometry -------------------------------------------------------------
def _xy(v: dict) -> list[float]:
    return [v["x"] * MM, v["y"] * MM]


def _point_map(entities: list[dict]) -> dict:
    return {
        e["sketchEntityId"]: _xy(_msg(e)["position2d"])
        for e in entities
        if e.get("sketchEntityType") == "skPoint" and "position2d" in _msg(e)
    }


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _reverse(curve: ir.Curve) -> ir.Curve:
    """Same curve traversed the other way (start<->end; arc midpoint unchanged)."""
    d = curve.data
    if curve.kind == "arc":
        return ir.Curve("arc", {"start": d["end"], "mid": d["mid"], "end": d["start"]})
    return ir.Curve("line", {"start": d["end"], "end": d["start"]})


def _assemble_loops(segments: list[ir.Curve], tol: float = 1e-4) -> list[list[ir.Curve]]:
    """Chain curves into closed loops, REORIENTING each so its start->end follows the
    traversal direction. Without this, a curve matched by its end would emit a
    zero-length lineTo/threePointArc (OCCT: 'GC_MakeArcOfCircle - no result')."""
    remaining = list(segments)
    loops: list[list[ir.Curve]] = []
    while remaining:
        cur = remaining.pop(0)
        loop = [cur]
        loop_start, cur_end = cur.data["start"], cur.data["end"]
        while remaining and _dist(cur_end, loop_start) > tol:
            for i, c in enumerate(remaining):
                if _dist(c.data["start"], cur_end) < tol:
                    loop.append(c); cur_end = c.data["end"]; remaining.pop(i); break
                if _dist(c.data["end"], cur_end) < tol:
                    rc = _reverse(c); loop.append(rc); cur_end = rc.data["end"]; remaining.pop(i); break
            else:
                break  # open chain; leave as-is
        loops.append(loop)
    return loops


def _arc_curve(em: dict, g: dict, s: list[float], e2: list[float]) -> ir.Curve:
    """3-point arc: midpoint from the solved parameter range (reliable direction)."""
    center = _xy(g["center2d"])
    r = g["radius"] * MM
    a0, a1 = em.get("startParameter"), em.get("endParameter")
    if a0 is not None and a1 is not None:
        am = (a0 + a1) / 2
        mid = [center[0] + r * math.cos(am), center[1] + r * math.sin(am)]
    else:  # fall back to the chord's perpendicular bulge
        mx, my = (s[0] + e2[0]) / 2, (s[1] + e2[1]) / 2
        vx, vy = mx - center[0], my - center[1]
        n = math.hypot(vx, vy) or 1.0
        mid = [center[0] + r * vx / n, center[1] + r * vy / n]
    return ir.Curve("arc", {"start": s, "mid": mid, "end": e2})


def _sketch_profiles(sk: dict) -> list[ir.Profile]:
    ents = sk.get("entities", [])
    pts = _point_map(ents)
    profiles: list[ir.Profile] = []
    segments: list[ir.Curve] = []
    for e in ents:
        em = _msg(e)
        t = e.get("sketchEntityType")
        if em.get("isConstruction"):
            continue
        g = em.get("geometry", {})
        if t == "skCircle":
            c = _xy(g["center2d"])
            profiles.append(ir.Profile([ir.Curve("circle", {"center": c, "radius": g["radius"] * MM})]))
        elif t == "skLineSegment":
            s, e2 = pts.get(em["startPointId"]), pts.get(em["endPointId"])
            if s and e2:
                segments.append(ir.Curve("line", {"start": s, "end": e2}))
        elif t == "skArc":
            s, e2 = pts.get(em["startPointId"]), pts.get(em["endPointId"])
            if s and e2:
                segments.append(_arc_curve(em, g, s, e2))
    for loop in _assemble_loops(segments):
        profiles.append(ir.Profile(loop))
    return profiles


# --- feature translation ---------------------------------------------------------
# --- rollback cap-face -> exact per-extrude profiles ----------------------------
def _project_2d(p_mm: list[float], matrix: list[float]) -> list[float]:
    """World point (mm) -> the sketch's local 2D frame (mm), dropping the normal."""
    ox, oy, oz = matrix[3] * MM, matrix[7] * MM, matrix[11] * MM
    xd = (matrix[0], matrix[4], matrix[8])
    yd = (matrix[1], matrix[5], matrix[9])
    dx, dy, dz = p_mm[0] - ox, p_mm[1] - oy, p_mm[2] - oz
    return [dx * xd[0] + dy * xd[1] + dz * xd[2], dx * yd[0] + dy * yd[1] + dz * yd[2]]


def _edge_to_curve_2d(pts: list[list[float]]) -> ir.Curve:
    """3 sampled 2D points (param 0/0.5/1) -> line | arc | circle Curve."""
    p0, pm, p1 = pts[0], pts[1], pts[2]
    if math.hypot(p0[0] - p1[0], p0[1] - p1[1]) < 1e-4:  # closed edge = full circle
        center = [(p0[0] + pm[0]) / 2, (p0[1] + pm[1]) / 2]
        return ir.Curve("circle", {"center": center,
                                    "radius": math.hypot(p0[0] - pm[0], p0[1] - pm[1]) / 2})
    area2 = abs((pm[0] - p0[0]) * (p1[1] - p0[1]) - (pm[1] - p0[1]) * (p1[0] - p0[0]))
    if area2 < 1e-6:  # collinear
        return ir.Curve("line", {"start": p0, "end": p1})
    return ir.Curve("arc", {"start": p0, "mid": pm, "end": p1})


def _profile_sig(prof: ir.Profile):
    pts = []
    for c in prof.curves:
        for k in ("start", "end", "center", "mid"):
            if k in c.data:
                pts.append((round(c.data[k][0], 3), round(c.data[k][1], 3)))
    return tuple(sorted(pts))


def caps_to_profiles(faces: list[dict], matrix: list[float]) -> list[ir.Profile]:
    """Rollback-resolved cap faces -> exact 2D regions (outer loops + holes),
    deduplicated across the start/end caps."""
    ndir = (matrix[2], matrix[6], matrix[10])
    profiles: list[ir.Profile] = []
    seen = set()
    for face in faces:
        n = face.get("n", [0, 0, 0])
        if abs(n[0] * ndir[0] + n[1] * ndir[1] + n[2] * ndir[2]) < 0.9:
            continue  # side wall, not a cap
        circles, segs = [], []
        for edge in face.get("edges", []):
            if len(edge) < 3:
                continue
            c = _edge_to_curve_2d([_project_2d(p, matrix) for p in edge])
            (circles if c.kind == "circle" else segs).append(c)
        loops = _assemble_loops(segs) if segs else []
        for prof in [ir.Profile(loop) for loop in loops] + [ir.Profile([c]) for c in circles]:
            sig = _profile_sig(prof)
            if sig and sig not in seen:
                seen.add(sig)
                profiles.append(prof)
    return profiles


def _sketch_op(feat: dict, sketch: dict | None) -> ir.Sketch:
    m = _msg(feat)
    plane = ir.Plane.from_matrix(sketch.get("sketchMatrix") if sketch else None)
    profiles = _sketch_profiles(sketch) if sketch else []
    return ir.Sketch(source={"id": m["featureId"], "name": m.get("name"), "type": "newSketch"},
                     plane=plane, profiles=profiles)


def _extrude_op(feat: dict, sketch_ids: set[str], last_sketch: str | None):
    m = _msg(feat)
    P = _params(feat)
    end_bound = _enum(P.get("endBound"))
    if end_bound not in ("BLIND", None):  # SYMMETRIC handled via `symmetric` flag
        return None, f"endBound={end_bound}"
    depth = parse_length_mm((P.get("depth") or {}).get("expression"))
    if depth is None:
        return None, "unresolved depth expression"
    if _bool(P.get("oppositeDirection")):
        depth = -depth
    # link to the sketch it consumes
    ref = None
    for q in (P.get("entities") or {}).get("queries", []):
        fid = q.get("featureId")
        if fid in sketch_ids:
            ref = fid; break
    ref = ref or last_sketch
    op = ir.Extrude(
        source={"id": m["featureId"], "name": m.get("name"), "type": "extrude"},
        profile_ref=ref or "",
        distance=depth,
        symmetric=_bool(P.get("symmetric")),
        op=_OP.get(_enum(P.get("operationType")), "new"),
    )
    return op, None


def _round_op(feat: dict, kind: str, targets: dict):
    """fillet/chamfer -> IR op with edge points from FeatureScript-resolved faces."""
    m = _msg(feat)
    faces = targets.get(m["featureId"]) or []
    points = [f["at"] for f in faces if isinstance(f, dict) and "at" in f]
    if not points:
        return None, "no resolved edges (FeatureScript)"
    P = _params(feat)
    if kind == "fillet":
        r = parse_length_mm((P.get("radius") or {}).get("expression"))
        if r is None:  # fall back to the created torus/cylinder radius
            rs = [f.get("r") for f in faces if f.get("r")]
            r = sum(rs) / len(rs) if rs else None
        if r is None:
            return None, "unresolved fillet radius"
        return ir.Fillet(source={"id": m["featureId"], "name": m.get("name"), "type": "fillet"},
                         edge_points=points, radius=r), None
    d = parse_length_mm((P.get("width") or P.get("length") or {}).get("expression"))
    if d is None:
        return None, "unresolved chamfer distance"
    return ir.Chamfer(source={"id": m["featureId"], "name": m.get("name"), "type": "chamfer"},
                      edge_points=points, distance=d), None


def _extrude_use_counts(feats: list, sketch_ids: set) -> dict:
    """How many extrudes consume each sketch (for detecting shared, multi-region
    sketches we can't yet split by region)."""
    counts: dict[str, int] = {}
    last = None
    for f in feats:
        m = _msg(f)
        if m.get("suppressed"):
            continue
        if m.get("featureType") == "newSketch":
            last = m.get("featureId")
        elif m.get("featureType") == "extrude":
            ref = None
            for q in (_params(f).get("entities") or {}).get("queries", []):
                if q.get("featureId") in sketch_ids:
                    ref = q["featureId"]; break
            ref = ref or last
            if ref:
                counts[ref] = counts.get(ref, 0) + 1
    return counts


def _parse_angle_deg(expr: str | None) -> float | None:
    if not expr:
        return None
    m = re.match(r"^\s*(-?\d+(?:\.\d+)?)\s*(deg|rad|degree|radian)?\s*$", expr)
    if not m:
        return None
    v = float(m.group(1))
    return math.degrees(v) if (m.group(2) or "").startswith("rad") else v


_BOOL_OP = {"UNION": "union", "SUBTRACTION": "cut", "INTERSECTION": "intersect"}


def _pattern_op(feat: dict, axes: dict):
    m = _msg(feat)
    P = _params(feat)
    if _enum(P.get("patternType")) not in ("PART", None):
        return None, f"patternType={_enum(P.get('patternType'))}"
    count = parse_length_mm((P.get("instanceCount") or {}).get("expression"))
    angle = _parse_angle_deg((P.get("angle") or {}).get("expression"))
    if not count or angle is None:
        return None, "unresolved pattern count/angle"
    ax = axes.get(m["featureId"]) or {}
    return ir.CircularPattern(
        source={"id": m["featureId"], "name": m.get("name"), "type": "circularPattern"},
        count=int(round(count)), angle=angle,
        equal_space=bool((P.get("equalSpace") or {}).get("value", True)),
        axis_origin=ax.get("origin", [0, 0, 0]), axis_dir=ax.get("dir", [0, 0, 1]),
    ), None


def _boolean_op(feat: dict):
    m = _msg(feat)
    op = _BOOL_OP.get(_enum(_params(feat).get("operationType")), "union")
    return ir.Boolean(source={"id": m["featureId"], "name": m.get("name"),
                              "type": "booleanBodies"}, op=op), None


def normalize(features: dict, sketches: dict, url: str = "", targets: dict | None = None,
              axes: dict | None = None, caps: dict | None = None) -> ir.Model:
    feats = features.get("features", [])
    sk_by_fid = {s["featureId"]: s for s in sketches.get("sketches", [])}
    sketch_ids = set(sk_by_fid)
    targets = targets or {}
    axes = axes or {}
    caps = caps or {}
    model = ir.Model(source_url=url)
    sketch_ops: dict[str, ir.Sketch] = {}
    use_counts = _extrude_use_counts(feats, sketch_ids)
    last_sketch: str | None = None

    for i, f in enumerate(feats):
        m = _msg(f)
        ftype, fid, fname = m.get("featureType"), m.get("featureId"), m.get("name")
        if m.get("suppressed"):
            continue
        if ftype == "newSketch":
            sk = _sketch_op(f, sk_by_fid.get(fid))
            model.ops.append(sk)
            sketch_ops[fid] = sk
            last_sketch = fid
            continue
        if ftype == "extrude":
            op, why = _extrude_op(f, sketch_ids, last_sketch)
            if op:
                sk = sketch_ops.get(op.profile_ref)
                matrix = (sk_by_fid.get(op.profile_ref) or {}).get("sketchMatrix")
                if fid in caps and matrix:  # exact regions from the extrude's rollback
                    profs = caps_to_profiles(caps[fid], matrix)
                    if profs:
                        op.profiles = profs
                    else:
                        op, why = None, "rollback cap resolution produced no profile"
                elif sk is None or not sk.profiles:
                    op, why = None, "referenced sketch has no reconstructable profile (text/empty)"
                elif use_counts.get(op.profile_ref, 0) > 1:
                    op, why = None, "sketch shared by multiple extrudes (needs rollback caps)"
        elif ftype in ("fillet", "chamfer"):
            op, why = _round_op(f, ftype, targets)
        elif ftype == "circularPattern":
            op, why = _pattern_op(f, axes)
        elif ftype == "booleanBodies":
            op, why = _boolean_op(f)
        else:
            op, why = None, "feature type not yet supported"
        if op:
            model.ops.append(op)
        else:
            model.unsupported.append({"index": i, "type": ftype, "name": fname, "reason": why})
    return model
