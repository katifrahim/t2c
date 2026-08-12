"""Direct (history-free) editing of dumb B-rep solids — the parametric-feeling edits the AI
should use on an imported STEP model instead of raw CSG.

A STEP import is one solid with no feature history, so there is no "hole radius" parameter to
change. These operations edit the boundary representation in place with OCCT's own algorithms:
they remove/heal features, resize holes, push/pull faces, hollow, and draft. This is what
commercial "direct / synchronous modelling" does — here the AI is the relationship engine that
picks which faces to edit, and OCCT does the geometry.

Selection (Step 3): every operation targets a face/edge by a point (the center coordinate the
inspect_model description already gives) or by its id, so the AI reliably hits one feature.

Public entry point:
  apply_edits(cq_shape, operations) -> (new_cq_shape, report)
"""

from __future__ import annotations

import math

import cadquery as cq

from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing, BRepAlgoAPI_Cut
from OCP.BRepFeat import BRepFeat_MakePrism
from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid, BRepOffsetAPI_DraftAngle
from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
from OCP.BRepExtrema import BRepExtrema_DistShapeShape
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_ListOfShape
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.gp import gp_Pnt, gp_Vec, gp_Dir, gp_Ax1, gp_Ax2, gp_Ax3, gp_Pln

try:
    from src.step_import import _faces, _SURF, _face_normal_mid, _r, _cylinder_concave
except ImportError:
    from step_import import _faces, _SURF, _face_normal_mid, _r, _cylinder_concave


# =============================================================================
# selection: a described face/edge -> the exact OCCT sub-shape
# =============================================================================
def _center(shape) -> tuple:
    g = GProp_GProps()
    # Surface props for a face, linear for an edge; both expose CentreOfMass.
    st = shape.ShapeType()
    if st == TopAbs_ShapeEnum.TopAbs_FACE:
        BRepGProp.SurfaceProperties_s(shape, g)
    else:
        BRepGProp.LinearProperties_s(shape, g)
    c = g.CentreOfMass()
    return (c.X(), c.Y(), c.Z())


def _edges(shape):
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_EDGE)
    seen = []
    while exp.More():
        seen.append(TopoDS.Edge_s(exp.Current()))
        exp.Next()
    return seen


def _dist(a, b) -> float:
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _dist_to(sub_shape, pt) -> float:
    """Distance from a point to a face/edge's actual surface — more robust than comparing
    centroids, so a point that lies ON the target face resolves to it unambiguously."""
    v = BRepBuilderAPI_MakeVertex(gp_Pnt(*pt)).Vertex()
    d = BRepExtrema_DistShapeShape(v, sub_shape)
    return d.Value() if d.IsDone() else _dist(_center(sub_shape), pt)


def _point_of(ref):
    """A selection ref -> the (x,y,z) the AI is pointing at. Accepts [x,y,z], {"near":[...]}
    or {"at":[...]}."""
    if isinstance(ref, dict):
        p = ref.get("near") or ref.get("at") or ref.get("point")
    else:
        p = ref
    if not (isinstance(p, (list, tuple)) and len(p) == 3):
        raise ValueError(f"selection needs a point [x,y,z] (got {ref!r})")
    return tuple(float(v) for v in p)


def resolve_face(shape, ref):
    """The face whose surface passes closest to the ref point (use a point_on_face)."""
    pt = _point_of(ref)
    fs = _faces(shape)
    if not fs:
        raise ValueError("solid has no faces")
    return min(fs, key=lambda f: _dist_to(f, pt))


def resolve_edge(shape, ref):
    pt = _point_of(ref)
    es = _edges(shape)
    if not es:
        raise ValueError("solid has no edges")
    return min(es, key=lambda e: _dist_to(e, pt))


def _cyl_axis(face):
    """(axis_point, unit_dir, radius) for a cylindrical face, else None."""
    s = BRepAdaptor_Surface(face)
    if _SURF.get(s.GetType()) != "Cylinder":
        return None
    cy = s.Cylinder()
    a = cy.Axis()
    loc, d = a.Location(), a.Direction()
    return ((loc.X(), loc.Y(), loc.Z()), (d.X(), d.Y(), d.Z()), cy.Radius())


def _same_axis(a, b, tol=1e-4):
    """True if two (point,dir,radius) cylinders are coaxial and same radius — i.e. faces of the
    one hole (a hole wall can be split into several faces)."""
    (pa, da, ra), (pb, db, rb) = a, b
    if abs(ra - rb) > 1e-3:
        return False
    if abs(abs(da[0] * db[0] + da[1] * db[1] + da[2] * db[2]) - 1) > tol:
        return False
    # distance from pb to the line (pa, da)
    w = [pb[i] - pa[i] for i in range(3)]
    dot = sum(w[i] * da[i] for i in range(3))
    perp = [w[i] - dot * da[i] for i in range(3)]
    return (perp[0] ** 2 + perp[1] ** 2 + perp[2] ** 2) ** 0.5 < tol * 10


def _hole_faces(shape, ref):
    """All cylindrical faces of the hole the ref points at (coaxial, equal radius)."""
    face = resolve_face(shape, ref)
    axis = _cyl_axis(face)
    if axis is None:
        # ref may be a circular EDGE of the hole → use the nearest cylinder face instead
        pt = _point_of(ref)
        cyl = [f for f in _faces(shape) if _cyl_axis(f)]
        if not cyl:
            raise ValueError("no cylindrical hole found near that point")
        face = min(cyl, key=lambda f: _dist_to(f, pt))
        axis = _cyl_axis(face)
    group = [f for f in _faces(shape) if _cyl_axis(f) and _same_axis(_cyl_axis(f), axis)]
    return group, axis


def _hole_groups(shape):
    """Every hole in the solid as a coaxial group of concave cylinder faces:
    [{faces, axis_pt, axis_dir, radius}]. A "hole set" (bolt pattern) is several groups that
    share a radius — the design-intent relationship we propagate edits across."""
    groups = []
    for f in _faces(shape):
        ax = _cyl_axis(f)
        if ax is None:
            continue
        if _cylinder_concave(f, BRepAdaptor_Surface(f)) is False:
            continue                                   # convex → boss/outer wall, not a hole
        for g in groups:
            if _same_axis((g["axis_pt"], g["axis_dir"], g["radius"]), ax):
                g["faces"].append(f)
                break
        else:
            groups.append({"faces": [f], "axis_pt": ax[0], "axis_dir": ax[1], "radius": ax[2]})
    return groups


# =============================================================================
# geometry helpers
# =============================================================================
def _solid_of(shape):
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    if exp.More():
        return TopoDS.Solid_s(exp.Current())
    return shape


def _volume(shape) -> float:
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    return g.Mass()


def _bbox_range(shapes, axis_pt, axis_dir):
    """Min/max projection of a set of faces onto an axis — the hole's extent along its axis."""
    lo, hi = math.inf, -math.inf
    for sh in shapes:
        bb = Bnd_Box()
        BRepBndLib.Add_s(sh, bb)
        xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
        for corner in ((xmin, ymin, zmin), (xmax, ymax, zmax),
                       (xmin, ymin, zmax), (xmax, ymax, zmin)):
            t = sum((corner[i] - axis_pt[i]) * axis_dir[i] for i in range(3))
            lo, hi = min(lo, t), max(hi, t)
    return lo, hi


def _heal(shape):
    """Merge co-planar/co-cylindrical faces split by an edit, so the result reads as one clean
    face (what a direct modeller shows after a move)."""
    try:
        u = ShapeUpgrade_UnifySameDomain(shape, True, True, True)
        u.Build()
        return u.Shape()
    except Exception:
        return shape


# =============================================================================
# operations
# =============================================================================
def remove_feature(shape, refs):
    """Delete features (holes, bosses, fillets, chamfers) and heal the gap by extending the
    neighbouring faces — OCCT BRepAlgoAPI_Defeaturing."""
    faces = TopTools_ListOfShape()
    for ref in refs:
        faces.Append(resolve_face(shape, ref))
    df = BRepAlgoAPI_Defeaturing()
    df.SetShape(shape)
    df.AddFacesToRemove(faces)
    df.Build()
    if df.HasErrors() if hasattr(df, "HasErrors") else False:
        raise ValueError("defeaturing failed")
    return _solid_of(df.Shape())


def resize_hole(shape, ref, diameter, scope="matching"):
    """Change a hole's diameter, and — by default — every other hole of the same diameter with
    it (a bolt pattern is one design decision, so the AI does not track the set by hand).

    Each hole is removed + healed, then re-cut on its own axis over its own depth at the new
    size. scope="matching" edits the whole equal-diameter set; scope="one" edits only the
    selected hole. Returns (new_shape, note)."""
    _sel, (sel_pt, sel_dir, r0) = _hole_faces(shape, ref)
    groups = _hole_groups(shape)
    if scope == "one":
        targets = [g for g in groups
                   if _same_axis((g["axis_pt"], g["axis_dir"], g["radius"]), (sel_pt, sel_dir, r0))]
    else:
        targets = [g for g in groups if abs(g["radius"] - r0) < 1e-4]   # the whole set
    if not targets:
        raise ValueError("could not identify the hole to resize")
    # Record each hole's axis + depth BEFORE removing it, then defeature them all at once.
    recut, faces = [], TopTools_ListOfShape()
    for g in targets:
        lo, hi = _bbox_range(g["faces"], g["axis_pt"], g["axis_dir"])
        recut.append((g["axis_pt"], g["axis_dir"], lo, hi))
        for f in g["faces"]:
            faces.Append(f)
    df = BRepAlgoAPI_Defeaturing()
    df.SetShape(shape)
    df.AddFacesToRemove(faces)
    df.Build()
    result = _solid_of(df.Shape())
    for axis_pt, axis_dir, lo, hi in recut:            # re-cut each at the new diameter
        m = 0.01 * (hi - lo + 1)
        start = gp_Pnt(*[axis_pt[i] + (lo - m) * axis_dir[i] for i in range(3)])
        cyl = BRepPrimAPI_MakeCylinder(gp_Ax2(start, gp_Dir(*axis_dir)),
                                       diameter / 2.0, (hi - lo) + 2 * m).Shape()
        result = BRepAlgoAPI_Cut(result, cyl).Shape()
    n = len(targets)
    note = (f"resized {n} holes of Ø{round(r0 * 2, 3)} together (matching set) to Ø{diameter}"
            if n > 1 else f"resized 1 hole to Ø{diameter}")
    return _solid_of(_heal(result)), note


def push_pull_face(shape, ref, distance):
    """Move a planar face along its normal by `distance` (add material when positive, remove
    when negative) — the core direct-modelling gesture, via BRepFeat_MakePrism."""
    face = resolve_face(shape, ref)
    _p, n = _face_normal_mid(face)
    if n is None:
        raise ValueError("face has no well-defined normal")
    fuse = distance >= 0
    d = gp_Dir(n.X(), n.Y(), n.Z())
    if not fuse:
        d = gp_Dir(-n.X(), -n.Y(), -n.Z())
    mk = BRepFeat_MakePrism(shape, face, face, d, 1 if fuse else 0, True)
    mk.Perform(abs(distance))
    if not mk.IsDone():
        raise ValueError("push/pull failed")
    return _solid_of(_heal(mk.Shape()))


def shell_solid(shape, refs, thickness):
    """Hollow the solid, opening it at the given faces (negative thickness = wall inside) —
    OCCT BRepOffsetAPI_MakeThickSolid."""
    faces = TopTools_ListOfShape()
    for ref in refs:
        faces.Append(resolve_face(shape, ref))
    mt = BRepOffsetAPI_MakeThickSolid()
    mt.MakeThickSolidByJoin(shape, faces, -abs(thickness), 1e-3)
    return _solid_of(mt.Shape())


def draft_face(shape, ref, angle_deg, neutral_point=None, pull=(0, 0, 1)):
    """Taper a face by `angle_deg` about a neutral plane — OCCT BRepOffsetAPI_DraftAngle.
    neutral_point defaults to the face center; pull is the mould-pull direction."""
    face = resolve_face(shape, ref)
    npt = neutral_point or _center(face)
    pull_dir = gp_Dir(*pull)
    plane = gp_Pln(gp_Ax3(gp_Pnt(*npt), pull_dir))
    da = BRepOffsetAPI_DraftAngle(shape)
    da.Add(face, pull_dir, math.radians(angle_deg), plane)
    da.Build()
    if not da.IsDone():
        raise ValueError("draft failed")
    return _solid_of(da.Shape())


_OPS = {
    "remove_feature": lambda s, o: remove_feature(s, o["faces"]),
    "resize_hole":    lambda s, o: resize_hole(s, o.get("edge") or o.get("face"), o["diameter"],
                                               o.get("scope", "matching")),
    "push_pull_face": lambda s, o: push_pull_face(s, o["face"], o["distance"]),
    "offset_face":    lambda s, o: push_pull_face(s, o["face"], o["distance"]),
    "shell":          lambda s, o: shell_solid(s, o["faces"], o["thickness"]),
    "draft_face":     lambda s, o: draft_face(s, o["face"], o["angle_deg"],
                                              o.get("neutral_point"), o.get("pull", (0, 0, 1))),
}


def apply_edits(cq_shape, operations):
    """Apply a list of direct-edit operations to a cadquery Shape/Workplane. Returns
    (new_cadquery_Shape, report). Raises on the first bad operation (with its index)."""
    shape = cq_shape.val().wrapped if hasattr(cq_shape, "val") else (
        cq_shape.wrapped if hasattr(cq_shape, "wrapped") else cq_shape)
    v0 = _volume(shape)
    notes = []
    for i, op in enumerate(operations):
        kind = op.get("op")
        if kind not in _OPS:
            raise ValueError(f"operation {i}: unknown op {kind!r}; "
                             f"choose from {sorted(_OPS)}")
        try:
            res = _OPS[kind](shape, op)
            shape, note = res if isinstance(res, tuple) else (res, None)
            if note:
                notes.append(note)
        except Exception as e:
            raise ValueError(f"operation {i} ({kind}) failed: {e}") from e
    result = cq.Shape.cast(shape)
    report = {
        "valid": bool(result.isValid()),
        "volume_before": _r(v0, 1),
        "volume_after": _r(_volume(shape), 1),
    }
    if notes:
        report["notes"] = notes            # e.g. "resized 4 holes together (matching set)"
    return result, report
