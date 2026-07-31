"""FeatureScript resolution: turn a modifier feature's opaque edge references into
concrete 3D points that emission can select with NearestToPointSelector.

For each fillet/chamfer/etc. we ask Onshape's own engine which FACES that feature
created and where their centroids are (mm). A fillet/chamfer face sits on the
rounded/bevelled edge, so the nearest edge to that centroid in the reconstructed
(pre-modifier) solid is the edge the feature acted on — the bridge that decouples us
from both engines' opaque topological naming.
"""
from __future__ import annotations

from onshape.client import Onshape, PartStudio

# One representative point PER created face, plus radius, in millimetres. The point
# is the midpoint of the face's LONGEST boundary edge -- a tangent line for a
# straight-edge fillet/chamfer, a boundary circle for a circular one -- so it always
# lies near the real edge and never on the axis (evApproximateCentroid gives the axis
# center for surfaces of revolution, which mis-selects circular edges). `%s` is a
# FeatureScript array literal of feature ids.
_FS_CREATED = """function(context is Context, queries){
  var res = {};
  var ids = %s;
  for (var fid in ids){
    var faces = [];
    for (var f in evaluateQuery(context, qCreatedBy(makeId(fid), EntityType.FACE))){
      var s = evSurfaceDefinition(context, {"face": f});
      var r = 0 * meter;
      if (s is Cylinder) { r = s.radius; }
      else if (s is Torus) { r = s.minorRadius; }
      var best = undefined;
      var bestLen = -1 * meter;
      for (var e in evaluateQuery(context, qAdjacent(f, AdjacencyType.EDGE, EntityType.EDGE))){
        var L = evLength(context, {"entities": e});
        if (L > bestLen){
          bestLen = L;
          var tl = evEdgeTangentLines(context, {"edge": e, "parameters": [0.5]});
          best = [tl[0].origin[0]/millimeter, tl[0].origin[1]/millimeter, tl[0].origin[2]/millimeter];
        }
      }
      if (best != undefined){ faces = append(faces, { "at": best, "r": r/millimeter }); }
    }
    res[fid] = faces;
  }
  return res;
}"""


def _unwrap(v):
    """Plain JSON out of a FeatureScript eval result (BTFSValue* wrappers)."""
    if not isinstance(v, dict):
        return v
    t = v.get("btType", "")
    if "ValueMap" in t and "Entry" not in t:
        return {_unwrap(e["key"]): _unwrap(e["value"]) for e in v["value"]}
    if "ValueArray" in t:
        return [_unwrap(x) for x in v["value"]]
    if "Value" in t:  # scalar wrappers (Number/String/WithUnits/...)
        return v.get("value")
    return v


# Per-extrude created planar faces, with each boundary edge sampled at parameters
# 0/0.5/1 (3 points classify any line/arc/circle). Evaluated at the extrude's own
# rollback so its faces are clean and unfragmented by later features.
_FS_CAPS = """function(context is Context, queries){
  var out = [];
  for (var f in evaluateQuery(context, qCreatedBy(makeId("%s"), EntityType.FACE))){
    var s = evSurfaceDefinition(context, {"face": f});
    if (!(s is Plane)) { continue; }
    var edges = [];
    for (var e in evaluateQuery(context, qAdjacent(f, AdjacencyType.EDGE, EntityType.EDGE))){
      var tl = evEdgeTangentLines(context, {"edge": e, "parameters": [0, 0.5, 1]});
      var pts = [];
      for (var t in tl){ pts = append(pts, [t.origin[0]/millimeter, t.origin[1]/millimeter, t.origin[2]/millimeter]); }
      edges = append(edges, pts);
    }
    out = append(out, {"n": [s.normal[0], s.normal[1], s.normal[2]], "edges": edges});
  }
  return out;
}"""


def resolve_extrude_caps(api: Onshape, ps: PartStudio, extrudes: list[tuple]) -> dict:
    """{featureId: [{"n": [x,y,z], "edges": [[p0,pmid,p1] mm, ...]}, ...]}.

    `extrudes` is a list of (featureId, rollback_index) — the index just AFTER the
    extrude, so its created faces exist and aren't yet fragmented. Best-effort:
    missing/failed entries are simply absent (the extrude then falls back)."""
    out: dict = {}
    for fid, rb in extrudes:
        try:
            res = api.call(f"{ps.path}/featurescript?rollbackBarIndex={rb}",
                           {"script": _FS_CAPS % fid, "queries": {}})
            faces = _unwrap(res.get("result")) or []
            if faces:
                out[fid] = faces
        except Exception:  # noqa: BLE001
            continue
    return out


def resolve_created_faces(api: Onshape, ps: PartStudio, feature_ids: list[str]) -> dict:
    """{featureId: [{"at": [x,y,z] mm, "r": mm}, ...]} for the given features.

    Returns {} on any FeatureScript failure — resolution is best-effort and a
    missing entry simply means that feature can't be reconstructed (flagged later).
    """
    if not feature_ids:
        return {}
    ids = "[" + ", ".join('"%s"' % f for f in feature_ids) + "]"
    try:
        res = api.featurescript(ps, _FS_CREATED % ids)
        return _unwrap(res.get("result", {})) or {}
    except Exception:  # noqa: BLE001
        return {}
