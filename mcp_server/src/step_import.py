"""Parse a STEP file (AP203 / AP214 / AP242) into a structured, LLM-legible description.

A STEP file stores the *shape* (a boundary representation: faces, edges, surfaces) — not
the *recipe* (sketch -> extrude -> fillet) that built it. The recipe is discarded on export
and no CAD system writes AP242's optional feature history. So we extract 100% of the geometry
deterministically with the OCCT kernel and hand the model a tiered JSON description. Any
parametric recipe is left for the LLM to infer. We build no feature-recognition engine.

Two public functions:
  read_step(path)            -> (cq_object, meta)   import + assembly/name/color/header
  describe_shape(obj, meta)  -> dict                the structured description

The description is deliberately tiered so large parts stay inside an LLM context window:
  summary  -> always present  (units, source, bbox, part list, primitive/hole/round counts)
  parts    -> per part        (name, color, placement, bbox, volume)
  solids   -> per solid       (primitive label, holes, rounds, and — under a size budget —
                               the full face/edge list with surface/curve types + dimensions)
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict

import cadquery as cq

from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_Writer, STEPControl_StepModelType
from OCP.Interface import Interface_Static
from OCP.TDocStd import TDocStd_Document
from OCP.XCAFApp import XCAFApp_Application
from OCP.XCAFDoc import (
    XCAFDoc_DocumentTool, XCAFDoc_ColorType,
    XCAFDoc_Dimension, XCAFDoc_GeomTolerance, XCAFDoc_Datum,
)
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDF import TDF_LabelSequence, TDF_Label
from OCP.TDataStd import TDataStd_Name
from OCP.Quantity import Quantity_Color
from OCP.TopExp import TopExp_Explorer, TopExp
from OCP.TopAbs import TopAbs_ShapeEnum
from OCP.TopTools import TopTools_IndexedMapOfShape
from OCP.TopoDS import TopoDS
from OCP.BRepAdaptor import BRepAdaptor_Surface, BRepAdaptor_Curve
from OCP.GeomAbs import GeomAbs_SurfaceType, GeomAbs_CurveType
from OCP.BRepGProp import BRepGProp, BRepGProp_Face
from OCP.GProp import GProp_GProps
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepTools import BRepTools
from OCP.gp import gp_Pnt, gp_Vec

# --- enum -> name maps -------------------------------------------------------
_SURF = {getattr(GeomAbs_SurfaceType, f"GeomAbs_{n}"): n for n in (
    "Plane", "Cylinder", "Cone", "Sphere", "Torus", "BezierSurface",
    "BSplineSurface", "SurfaceOfRevolution", "SurfaceOfExtrusion",
    "OffsetSurface", "OtherSurface") if hasattr(GeomAbs_SurfaceType, f"GeomAbs_{n}")}
_CURVE = {getattr(GeomAbs_CurveType, f"GeomAbs_{n}"): n for n in (
    "Line", "Circle", "Ellipse", "Hyperbola", "Parabola", "BezierCurve",
    "BSplineCurve", "OffsetCurve", "OtherCurve") if hasattr(GeomAbs_CurveType, f"GeomAbs_{n}")}

# Default: emit the full face/edge graph only when the solid has at most this many faces.
# Bigger solids fall back to summary + detected features so the description stays legible.
FACE_BUDGET = 200

# The only import format the parser + viewer + AI edit-loop support today: STEP. STEP is the
# neutral B-rep exchange format every CAD system writes; the reader normalises it to AP242.
SUPPORTED_IMPORT_EXTS = (".step", ".stp")

# Above this many parts, describe each part compactly (bbox + volume + face count only, no
# per-face detail or feature detection) so a big assembly stays fast and fits an LLM context.
PART_BUDGET = 40
# And list at most this many parts (the largest) so the description can't balloon the prompt.
PART_LIST_CAP = 80


def looks_like_step(data: bytes) -> bool:
    """A STEP file starts with the ISO-10303-21 marker. Sniff the content so a mislabelled or
    wrong-format upload is rejected regardless of its extension."""
    head = data[:512].lstrip()
    return head.startswith(b"ISO-10303-21")


# =============================================================================
# number / geometry formatting
# =============================================================================
def _r(x, n=3) -> float:
    """Round and kill -0.0 so the JSON reads cleanly."""
    v = round(float(x), n) + 0.0
    return 0.0 if v == 0 else v


def _xyz(p) -> list:
    return [_r(p.X()), _r(p.Y()), _r(p.Z())]


def _pnt(p) -> list:
    return [_r(p.X()), _r(p.Y()), _r(p.Z())]


def _bbox(shape) -> dict:
    bb = Bnd_Box()
    BRepBndLib.Add_s(shape, bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    return {"min": [_r(xmin), _r(ymin), _r(zmin)],
            "max": [_r(xmax), _r(ymax), _r(zmax)],
            "size": [_r(xmax - xmin), _r(ymax - ymin), _r(zmax - zmin)]}


def _volume(shape):
    g = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, g)
    c = g.CentreOfMass()
    return _r(g.Mass()), _pnt(c)


def _face_area(face) -> float:
    g = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, g)
    return _r(g.Mass())


def _edge_length(edge) -> float:
    g = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, g)
    return _r(g.Mass())


def _face_normal_mid(face):
    """Outward normal (points out of the material) at the face's UV midpoint. BRepGProp_Face
    already returns the orientation-aware normal, so no manual flip is needed."""
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    p, n = gp_Pnt(), gp_Vec()
    BRepGProp_Face(face).Normal((u0 + u1) / 2, (v0 + v1) / 2, p, n)
    if n.Magnitude() < 1e-9:
        return None, None
    n.Normalize()
    return p, n


# =============================================================================
# read a STEP file: geometry + assembly tree + names + colors + header
# =============================================================================
def _read_header(path: str) -> dict:
    """Source CAD system, schema (AP203/214/242) and file name from the STEP HEADER —
    a cheap text scan; OCCT does not expose these directly."""
    head = {}
    try:
        with open(path, "r", errors="ignore") as fh:
            txt = fh.read(4000)
        txt = txt.split("ENDSEC", 1)[0]
        # FILE_SCHEMA may wrap across lines (AP242 does), so allow whitespace and span newlines.
        sch = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", txt, re.DOTALL)
        if sch:
            s = sch.group(1)
            head["schema"] = s
            # Prefer the schema *name*, not the "10303 NNN" MIM part-number (AP242's MIM is
            # 10303-442, so that number must not be read as the AP).
            if "MANAGED_MODEL_BASED_3D_ENGINEERING" in s or "AP242" in s:
                head["application_protocol"] = "AP242"
            elif "AUTOMOTIVE_DESIGN" in s or "AP214" in s:
                head["application_protocol"] = "AP214"
            elif "CONFIG_CONTROL_DESIGN" in s or "AP203" in s:
                head["application_protocol"] = "AP203"
        name = re.search(r"FILE_NAME\('([^']*)'", txt)
        if name and name.group(1):
            head["file_name"] = name.group(1)
        # originating system is the 6th field of FILE_NAME
        sysm = re.search(r"FILE_NAME\((?:[^;]*?),\s*\(([^)]*)\),\s*'([^']*)'", txt)
        if sysm and sysm.group(2):
            head["source_system"] = sysm.group(2)
    except Exception:
        pass
    return head


def ensure_ap242_schema() -> None:
    """Latch the STEP writer to AP242 for the current process.

    OCCT's `write.step.schema` static ignores a value set while it is still uninitialised, and
    a fresh writer forces the AP214 default. Constructing one writer first initialises the
    static; then SetIVal(5)=AP242DIS sticks for every writer that follows (including
    CadQuery's own exporters). Cheap and idempotent — safe to call before each export."""
    STEPControl_Writer()
    Interface_Static.SetIVal_s("write.step.schema", 5)  # 5 = AP242DIS


def to_ap242(src: str, dst: str) -> bool:
    """Convert a STEP file to AP242, preserving assembly / names / colors / PMI via XCAF.
    Returns True on success; on any failure the caller keeps the original file."""
    try:
        doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
        XCAFApp_Application.GetApplication_s().InitDocument(doc)
        reader = STEPCAFControl_Reader()
        reader.SetColorMode(True)
        reader.SetNameMode(True)
        reader.SetLayerMode(True)
        if not reader.ReadFile(src):
            return False
        reader.Transfer(doc)
        ensure_ap242_schema()
        writer = STEPCAFControl_Writer()
        writer.Transfer(doc, STEPControl_StepModelType.STEPControl_AsIs)
        writer.Write(dst)
        return os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception:
        return False


def _label_name(lab: TDF_Label):
    a = TDataStd_Name()
    if lab.FindAttribute(TDataStd_Name.GetID_s(), a):
        return a.Get().ToExtString()
    return None


def _label_color(color_tool, lab: TDF_Label):
    for ct in (XCAFDoc_ColorType.XCAFDoc_ColorSurf,
               XCAFDoc_ColorType.XCAFDoc_ColorGen):
        c = Quantity_Color()
        if color_tool.GetColor_s(lab, ct, c):
            return [_r(c.Red(), 4), _r(c.Green(), 4), _r(c.Blue(), 4)]
    return None


def _placement(t, r) -> dict | None:
    """Translation + XYZ-euler degrees, or None for an identity placement."""
    if all(abs(v) < 1e-6 for v in (*t, *r)):
        return None
    return {"translation": [_r(v) for v in t], "rotation_deg_xyz": [_r(v) for v in r]}


def _loc_to_placement(loc) -> dict | None:
    """A placement from any location (TopLoc_Location or cq.Location). cq.Location.toTuple()
    already yields ((tx,ty,tz),(rx,ry,rz)) with rotation in degrees."""
    try:
        t, r = cq.Location(loc).toTuple()
        return _placement(t, r)
    except Exception:
        return None


def _hstr(h):
    """A handle to a TCollection HAsciiString -> str, or None."""
    try:
        return h.ToCString() if h is not None else None
    except Exception:
        try:
            return h.String().ToCString()
        except Exception:
            return None


def _enum_tail(v) -> str:
    """'XCAFDimTolObjects_DimensionType_LinearDistance' -> 'LinearDistance'."""
    return str(v).rsplit("_", 1)[-1]


def _read_pmi(doc) -> dict:
    """Semantic PMI / GD&T — dimensions, geometric tolerances, datums — via the XCAF
    DimTol tool. Best-effort: AP242 carries this (it is the 2D-drawing data), but many
    files have none, so every step is guarded and an empty result is normal."""
    out = {"dimensions": [], "tolerances": [], "datums": []}
    try:
        tool = XCAFDoc_DocumentTool.DimTolTool_s(doc.Main())
    except Exception:
        return {}

    def each(get_labels, attr_cls):
        labels = TDF_LabelSequence()
        try:
            get_labels(labels)
        except Exception:
            return
        for i in range(1, labels.Length() + 1):
            lab = labels.Value(i)
            attr = attr_cls()
            if lab.FindAttribute(attr_cls.GetID_s(), attr):
                try:
                    o = attr.GetObject()
                except Exception:
                    o = None
                if o is not None:
                    yield o

    for o in each(tool.GetDimensionLabels, XCAFDoc_Dimension):
        d = {}
        try:
            d["type"] = _enum_tail(o.GetType())
        except Exception:
            pass
        try:
            arr = o.GetValues()
            if arr is not None:
                d["values"] = [_r(arr.Value(j)) for j in range(arr.Lower(), arr.Upper() + 1)]
        except Exception:
            pass
        try:
            nm = _hstr(o.GetSemanticName())
            if nm:
                d["name"] = nm
        except Exception:
            pass
        try:
            lo, up = o.GetLowerTolValue(), o.GetUpperTolValue()
            if lo or up:
                d["tolerance"] = [_r(lo), _r(up)]
        except Exception:
            pass
        if d:
            out["dimensions"].append(d)

    for o in each(tool.GetGeomToleranceLabels, XCAFDoc_GeomTolerance):
        t = {}
        try:
            t["type"] = _enum_tail(o.GetType())
        except Exception:
            pass
        try:
            t["value"] = _r(o.GetValue())
        except Exception:
            pass
        if t:
            out["tolerances"].append(t)

    for o in each(tool.GetDatumLabels, XCAFDoc_Datum):
        nm = None
        try:
            nm = _hstr(o.GetName())
        except Exception:
            pass
        if nm:
            out["datums"].append(nm)

    return {k: v for k, v in out.items() if v}


def read_step(path: str):
    """Read a STEP file. Returns (cq_object, meta).

    cq_object: a cadquery.Workplane for a single part (fully editable), or a
               cadquery.Assembly for a multi-part file (edited per-part).
    meta:      {"header": {...}, "parts": [{name, color, placement, shape(TopoDS)}]}
               `shape` is the located solid used by describe_shape (ids stay consistent).
    """
    doc = TDocStd_Document(TCollection_ExtendedString("BinXCAF"))
    XCAFApp_Application.GetApplication_s().InitDocument(doc)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    reader.ReadFile(path)
    reader.Transfer(doc)

    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    ct = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    free = TDF_LabelSequence()
    st.GetFreeShapes(free)

    parts = []

    def add_part(name, color, placement, shape):
        parts.append({"name": name, "color": color,
                      "placement": placement, "shape": shape})

    def walk(lab, placement):
        if st.IsAssembly_s(lab):
            comps = TDF_LabelSequence()
            st.GetComponents_s(lab, comps)
            for i in range(1, comps.Length() + 1):
                comp = comps.Value(i)
                cloc = _loc_to_placement(st.GetLocation_s(comp))
                ref = TDF_Label()
                if st.GetReferredShape_s(comp, ref):
                    name = _label_name(comp) or _label_name(ref)
                    color = _label_color(ct, comp) or _label_color(ct, ref)
                    # component may itself be a sub-assembly
                    if st.IsAssembly_s(ref):
                        walk(ref, cloc or placement)
                    else:
                        add_part(name, color, cloc,
                                 st.GetShape_s(comp))  # located instance
        else:
            add_part(_label_name(lab), _label_color(ct, lab), placement,
                     st.GetShape_s(lab))

    for i in range(1, free.Length() + 1):
        walk(free.Value(i), None)

    meta = {"header": _read_header(path), "parts": parts, "pmi": _read_pmi(doc)}

    # Build the editable CadQuery object. Single part -> Workplane; else Assembly.
    if len(parts) == 1 and parts[0]["placement"] is None:
        obj = cq.Workplane(obj=cq.Shape.cast(parts[0]["shape"]))
    else:
        asm = cq.Assembly()
        used = set()                       # STEP files may repeat part names; cq.Assembly needs
        for idx, p in enumerate(parts):    # unique names, so de-duplicate with a numeric suffix.
            nm = p["name"] or f"part_{idx + 1}"
            unique, k = nm, 2
            while unique in used:
                unique = f"{nm}_{k}"
                k += 1
            used.add(unique)
            col = cq.Color(*p["color"]) if p["color"] else None
            asm.add(cq.Shape.cast(p["shape"]), name=unique, color=col)
        obj = asm
    return obj, meta


# =============================================================================
# describe geometry: per-solid faces / edges + cheap analytic enrichment
# =============================================================================
def _surface_params(surf, stype: str) -> dict:
    """Analytic parameters of a face's surface (radius, axis, apex, …)."""
    p = {}
    try:
        if stype == "Plane":
            pl = surf.Plane()
            p["origin"] = _pnt(pl.Location())
            p["normal"] = _xyz(pl.Axis().Direction())
        elif stype == "Cylinder":
            cy = surf.Cylinder()
            p["radius"] = _r(cy.Radius())
            p["diameter"] = _r(cy.Radius() * 2)
            p["axis_point"] = _pnt(cy.Axis().Location())
            p["axis_dir"] = _xyz(cy.Axis().Direction())
        elif stype == "Cone":
            co = surf.Cone()
            p["half_angle_deg"] = _r(co.SemiAngle() * 57.29577951308232)
            p["ref_radius"] = _r(co.RefRadius())
            p["apex"] = _pnt(co.Apex())
            p["axis_dir"] = _xyz(co.Axis().Direction())
        elif stype == "Sphere":
            sp = surf.Sphere()
            p["radius"] = _r(sp.Radius())
            p["center"] = _pnt(sp.Location())
        elif stype == "Torus":
            to = surf.Torus()
            p["major_radius"] = _r(to.MajorRadius())
            p["minor_radius"] = _r(to.MinorRadius())
            p["center"] = _pnt(to.Location())
            p["axis_dir"] = _xyz(to.Axis().Direction())
    except Exception:
        pass
    return p


def _curve_params(curve, ctype: str) -> dict:
    p = {}
    try:
        u0, u1 = curve.FirstParameter(), curve.LastParameter()
        p["start"] = _pnt(curve.Value(u0))
        p["end"] = _pnt(curve.Value(u1))
        if ctype == "Circle":
            ci = curve.Circle()
            p["radius"] = _r(ci.Radius())
            p["diameter"] = _r(ci.Radius() * 2)
            p["center"] = _pnt(ci.Location())
            p["axis_dir"] = _xyz(ci.Axis().Direction())
        elif ctype == "Ellipse":
            el = curve.Ellipse()
            p["major_radius"] = _r(el.MajorRadius())
            p["minor_radius"] = _r(el.MinorRadius())
            p["center"] = _pnt(el.Location())
    except Exception:
        pass
    return p


def _faces(shape):
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    while exp.More():
        yield TopoDS.Face_s(exp.Current())
        exp.Next()


def _edges(shape):
    """Unique edges. A shared edge is visited once by two faces, so deduplicate via a map;
    otherwise every internal edge (and the whole edge_types count) is doubled."""
    m = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_ShapeEnum.TopAbs_EDGE, m)
    for i in range(1, m.Extent() + 1):
        yield TopoDS.Edge_s(m.FindKey(i))


def _cylinder_concave(face, surf) -> bool | None:
    """True if the cylinder wall bounds a hole (outward-of-material normal points toward
    the axis), False for a boss/round/outer wall. None if undecidable."""
    try:
        p, n = _face_normal_mid(face)
        if n is None:
            return None
        ax = surf.Cylinder().Axis()
        a0, ad = ax.Location(), ax.Direction()
        # radial vector from axis to the surface point
        rx, ry, rz = p.X() - a0.X(), p.Y() - a0.Y(), p.Z() - a0.Z()
        dot = rx * ad.X() + ry * ad.Y() + rz * ad.Z()
        rx, ry, rz = rx - dot * ad.X(), ry - dot * ad.Y(), rz - dot * ad.Z()
        return (rx * n.X() + ry * n.Y() + rz * n.Z()) < 0
    except Exception:
        return None


def _cylindrical_features(solid) -> dict:
    """Group cylindrical faces by (radius, axis) and split them into holes (concave) and
    rounds/fillets (convex). Deterministic, no ML — the cheap enrichment the LLM finishes."""
    groups = defaultdict(lambda: {"area": 0.0, "concave": 0, "convex": 0,
                                   "axis_point": None, "axis_dir": None, "radius": None})
    for f in _faces(solid):
        s = BRepAdaptor_Surface(f)
        if _SURF.get(s.GetType()) != "Cylinder":
            continue
        cy = s.Cylinder()
        r = round(cy.Radius(), 3)
        d = cy.Axis().Direction()
        key = (r, round(abs(d.X()), 2), round(abs(d.Y()), 2), round(abs(d.Z()), 2))
        g = groups[key]
        g["radius"] = r
        g["axis_dir"] = _xyz(d)
        g["axis_point"] = _pnt(cy.Axis().Location())
        g["area"] += _face_area(f)
        conc = _cylinder_concave(f, s)
        if conc is True:
            g["concave"] += 1
        elif conc is False:
            g["convex"] += 1
    # A round/fillet is a *small* convex blend. A primitive body wall (e.g. a plain cylinder's
    # side) is convex too but large, so gate rounds by radius vs the solid's smallest extent.
    bb = _bbox(solid)
    min_extent = min(bb["size"]) or 1e9
    holes, rounds = [], []
    for g in groups.values():
        entry = {"radius": g["radius"], "diameter": _r(g["radius"] * 2),
                 "axis_dir": g["axis_dir"], "axis_point": g["axis_point"],
                 "face_count": g["concave"] + g["convex"], "total_area": _r(g["area"])}
        if g["concave"] >= g["convex"]:
            holes.append(entry)
        elif g["radius"] <= 0.4 * min_extent:
            rounds.append(entry)  # else: body geometry, already in face_types
    out = {}
    if holes:
        out["holes"] = sorted(holes, key=lambda h: h["radius"])
    if rounds:
        out["rounds_or_fillets"] = sorted(rounds, key=lambda h: h["radius"])
    return out


# Face-type signature -> primitive label. Order-independent (uses a Counter).
def _primitive(face_types: Counter, edge_types: Counter) -> str | None:
    ft = dict(face_types)
    if ft == {"Plane": 6} and dict(edge_types) == {"Line": 12}:
        return "box"
    if ft == {"Plane": 6}:
        return "box (or prism)"
    if ft == {"Sphere": 1}:
        return "sphere"
    if ft == {"Plane": 2, "Cylinder": 1} or ft == {"Plane": 3, "Cylinder": 1}:
        return "cylinder"
    if ft == {"Plane": 1, "Cone": 1} or ft == {"Cone": 1}:
        return "cone"
    if set(ft) == {"Cylinder", "Plane", "Torus"}:
        return "cylinder with rounded edges"
    return None


def _solid_report(solid, sid: str, budget: int) -> dict:
    face_list = list(_faces(solid))
    ftypes = Counter(_SURF.get(BRepAdaptor_Surface(f).GetType(), "Other") for f in face_list)
    etypes = Counter(_CURVE.get(BRepAdaptor_Curve(e).GetType(), "Other") for e in _edges(solid))
    vol, center = _volume(solid)
    rep = {
        "id": sid,
        "volume": vol,
        "center": center,
        "bbox": _bbox(solid),
        "face_count": len(face_list),
        "face_types": dict(ftypes),
        "edge_types": dict(etypes),
    }
    prim = _primitive(ftypes, etypes)
    if prim:
        rep["primitive"] = prim
    feats = _cylindrical_features(solid)
    if feats:
        rep["features"] = feats
    # Full face/edge graph only when small enough to stay legible.
    if len(face_list) <= budget:
        faces = []
        for i, f in enumerate(face_list):
            s = BRepAdaptor_Surface(f)
            t = _SURF.get(s.GetType(), "Other")
            p, n = _face_normal_mid(f)
            fd = {"id": f"{sid}/F{i}", "type": t, "area": _face_area(f)}
            fd.update(_surface_params(s, t))
            if n is not None:
                fd["normal"] = _xyz(n)
                fd["point_on_face"] = _pnt(p)
            faces.append(fd)
        edges = []
        for i, e in enumerate(_edges(solid)):
            c = BRepAdaptor_Curve(e)
            t = _CURVE.get(c.GetType(), "Other")
            ed = {"id": f"{sid}/E{i}", "type": t, "length": _edge_length(e)}
            ed.update(_curve_params(c, t))
            edges.append(ed)
        rep["faces"] = faces
        rep["edges"] = edges
    else:
        rep["detail_omitted"] = (
            f"{len(face_list)} faces exceed the {budget}-face budget; "
            "see primitive/features/face_types for the shape")
    return rep


def _solids(shape):
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_SOLID)
    out = []
    while exp.More():
        out.append(TopoDS.Solid_s(exp.Current()))
        exp.Next()
    return out


def _face_count(shape) -> int:
    exp = TopExp_Explorer(shape, TopAbs_ShapeEnum.TopAbs_FACE)
    n = 0
    while exp.More():
        n += 1
        exp.Next()
    return n


def _solid_summary(solid, sid: str) -> dict:
    """Cheap per-solid summary (bbox + face count only, no volume/GProp) for parts in a large
    assembly — GProp volume is ~18 ms/solid and would dominate a 1000-part import."""
    bb = _bbox(solid)
    center = [_r((bb["min"][i] + bb["max"][i]) / 2) for i in range(3)]
    return {"id": sid, "bbox": bb, "center": center, "face_count": _face_count(solid)}


def _parts_from_object(obj) -> list:
    """Parts (name/color/placement/shape) from a live CadQuery object when no STEP metadata
    is supplied — so describe_shape also works on AI-built models, including assemblies."""
    if isinstance(obj, cq.Assembly):
        parts = []

        def rec(node, parent_loc):
            loc = parent_loc * node.loc
            if node.obj is not None:
                shp = node.obj.val() if hasattr(node.obj, "val") else node.obj
                if isinstance(shp, cq.Shape):
                    t, r = loc.toTuple()
                    col = list(node.color.toTuple()[:3]) if node.color else None
                    parts.append({"name": node.name, "color": col,
                                  "placement": _placement(t, r),
                                  "shape": shp.located(loc).wrapped})
            for ch in node.children:
                rec(ch, loc)

        rec(obj, cq.Location())
        return parts
    shape = obj.wrapped if hasattr(obj, "wrapped") else (
        obj.val().wrapped if hasattr(obj, "val") else obj)
    return [{"name": None, "color": None, "placement": None, "shape": shape}]


def describe_shape(obj, meta: dict | None = None, budget: int = FACE_BUDGET) -> dict:
    """Structured, tiered description of a CAD object (imported STEP or AI-built).

    obj:  a cadquery Workplane / Shape / Assembly, or a raw TopoDS shape.
    meta: the dict from read_step (header + parts). Optional; when absent the parts are
          derived from the object itself.
    """
    # Resolve the parts to describe: prefer STEP metadata (names/colors/placement).
    parts = (meta or {}).get("parts") or _parts_from_object(obj)
    compact = len(parts) > PART_BUDGET     # big assembly → per-part summary only

    all_solids = []
    part_reports = []
    for pi, p in enumerate(parts):
        shape = p["shape"]
        solids = _solids(shape) or [shape]
        sreps = []
        for si, sol in enumerate(solids):
            sid = f"P{pi}" if len(parts) > 1 else "S"
            sid = f"{sid}/{si}" if len(solids) > 1 else sid
            sreps.append(_solid_summary(sol, sid) if compact else _solid_report(sol, sid, budget))
            all_solids.append(sol)
        pr = {"name": p["name"] or f"part_{pi + 1}", "bbox": _bbox(shape),
              "solids": sreps}
        if p["color"]:
            pr["color_rgb"] = p["color"]
        if p["placement"]:
            pr["placement"] = p["placement"]
        if not compact:                    # GProp volume is the per-part bottleneck; skip it big
            vol, ctr = _volume(shape)
            pr["volume"] = vol
            pr["center"] = ctr
        part_reports.append(pr)

    # whole-model bbox
    whole = Bnd_Box()
    for s in all_solids:
        BRepBndLib.Add_s(s, whole)
    xmin, ymin, zmin, xmax, ymax, zmax = whole.Get()

    n_holes = sum(len(sr.get("features", {}).get("holes", []))
                  for pr in part_reports for sr in pr["solids"])
    n_rounds = sum(len(sr.get("features", {}).get("rounds_or_fillets", []))
                   for pr in part_reports for sr in pr["solids"])
    prims = [sr["primitive"] for pr in part_reports for sr in pr["solids"]
             if "primitive" in sr]

    summary = {
        "part_count": len(part_reports),
        "solid_count": len(all_solids),
        "bbox": {"min": [_r(xmin), _r(ymin), _r(zmin)],
                 "max": [_r(xmax), _r(ymax), _r(zmax)],
                 "size": [_r(xmax - xmin), _r(ymax - ymin), _r(zmax - zmin)]},
        "hole_count": n_holes,
        "round_or_fillet_count": n_rounds,
    }
    if prims:
        summary["primitives"] = prims
    if compact:
        summary["detail_level"] = (
            f"compact: {len(part_reports)} parts exceed the {PART_BUDGET}-part budget, so each "
            "part shows bbox + volume + face count only (no per-face detail or feature detection)")
    if meta and meta.get("header"):
        summary["source"] = meta["header"]
    summary["units"] = "mm"  # OCCT normalises STEP lengths to millimetres on read
    pmi = (meta or {}).get("pmi")
    if pmi:
        summary["pmi_counts"] = {k: len(v) for k, v in pmi.items()}

    # In a big assembly, list only the largest parts so the description can't balloon the prompt.
    if compact and len(part_reports) > PART_LIST_CAP:
        part_reports.sort(key=lambda pr: -(pr["bbox"]["size"][0] * pr["bbox"]["size"][1]
                                           * pr["bbox"]["size"][2]))
        omitted = len(part_reports) - PART_LIST_CAP
        part_reports = part_reports[:PART_LIST_CAP]
        summary["parts_shown"] = (f"{PART_LIST_CAP} largest of {summary['part_count']} parts; "
                                  f"{omitted} smaller parts omitted")

    result = {
        "note": ("Geometry (boundary representation) read from the CAD file. It is the exact "
                 "shape and all dimensions, not the build recipe — infer sketches/extrudes/"
                 "fillets from this if you need to rebuild it."),
        "summary": summary,
        "parts": part_reports,
    }
    if pmi:  # semantic PMI / GD&T straight off the drawing (AP242)
        result["pmi"] = pmi
    return result
