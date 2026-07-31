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

# Created-face centroids + radius per feature id, all in millimetres, computed
# server-side. `%s` is filled with a FeatureScript array literal of feature ids.
_FS_CREATED = """function(context is Context, queries){
  var res = {};
  var ids = %s;
  for (var fid in ids){
    var faces = [];
    for (var f in evaluateQuery(context, qCreatedBy(makeId(fid), EntityType.FACE))){
      var c = evApproximateCentroid(context, {"entities": f});
      var s = evSurfaceDefinition(context, {"face": f});
      var r = 0 * meter;
      if (s is Cylinder) { r = s.radius; }
      else if (s is Torus) { r = s.minorRadius; }
      faces = append(faces, { "at": [c[0]/millimeter, c[1]/millimeter, c[2]/millimeter],
                              "r": r/millimeter });
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
