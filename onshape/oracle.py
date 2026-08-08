"""The ground-truth oracle: Onshape's exact geometry at every feature checkpoint.

This is the spine of the closed-loop reconstruction. For each rollback index we ask
Onshape's own kernel for the geometry of every solid body present — volume, surface
area, centroid, bounding box. The reconstruction engine compares its candidate ops
against these per-feature, so an error is caught AT the feature that causes it (not as
a mysterious end-of-run delta), and ambiguous conventions (direction, region, boolean
operands) are resolved by picking the candidate whose geometry matches the oracle.

All values are millimetres / mm^2 / mm^3. Uses the disk-cached rollback featurescript
calls (see client.py), so after one mining pass the whole oracle is available offline.
"""
from __future__ import annotations

from dataclasses import dataclass

from onshape.client import Onshape, PartStudio
from onshape.extract import _unwrap

# Per-solid geometry at a rollback state. evBox3d gives a tight axis-aligned box; a
# body's (volume, area, centroid, box) together form a fingerprint strong enough to
# tell reconstruction candidates apart without touching either kernel's opaque ids.
_FS_BODIES = """function(context is Context, queries){
  var out = [];
  for (var b in evaluateQuery(context, qBodyType(qEverything(EntityType.BODY), BodyType.SOLID))){
    var c = evApproximateCentroid(context, {"entities": b});
    var bb = evBox3d(context, {"topology": b, "tight": true});
    out = append(out, {
      "vol":  evVolume(context, {"entities": b}) / (millimeter*millimeter*millimeter),
      "area": evArea(context, {"entities": b}) / (millimeter*millimeter),
      "c":    [c[0]/millimeter, c[1]/millimeter, c[2]/millimeter],
      "min":  [bb.minCorner[0]/millimeter, bb.minCorner[1]/millimeter, bb.minCorner[2]/millimeter],
      "max":  [bb.maxCorner[0]/millimeter, bb.maxCorner[1]/millimeter, bb.maxCorner[2]/millimeter]
    });
  }
  return out;
}"""


@dataclass
class Body:
    """One solid body's geometry fingerprint at a checkpoint (mm)."""
    volume: float
    area: float
    centroid: list[float]
    bbox_min: list[float]
    bbox_max: list[float]

    @staticmethod
    def from_fs(d: dict) -> "Body":
        return Body(volume=d["vol"], area=d["area"], centroid=d["c"],
                    bbox_min=d["min"], bbox_max=d["max"])

    def matches(self, other: "Body", *, vol_tol=1e-3, area_tol=2e-3, bbox_tol=0.05) -> bool:
        """True if two bodies are the same solid within tolerance (relative on
        volume/area, absolute mm on the box AND centroid). The centroid check
        disambiguates placement — two solids can share volume/area/bbox yet sit in
        different spots (e.g. a boss on the left vs the right of a symmetric base),
        which would otherwise let a mis-placed candidate pass and corrupt later features."""
        def rel(a, b):
            return abs(a - b) / max(abs(b), 1e-9)
        if rel(self.volume, other.volume) > vol_tol or rel(self.area, other.area) > area_tol:
            return False
        if any(abs(a - b) > bbox_tol for a, b in zip(self.centroid, other.centroid)):
            return False
        return all(abs(a - b) <= bbox_tol
                   for a, b in zip(self.bbox_min + self.bbox_max,
                                   other.bbox_min + other.bbox_max))


@dataclass
class State:
    """The whole solid set at a checkpoint — a multiset of Body fingerprints. This is
    what the reconstruction is verified against after each feature."""
    bodies: list[Body]

    @property
    def total_volume(self) -> float:
        return sum(b.volume for b in self.bodies)

    def matches(self, other: "State", **tol) -> bool:
        """Same set of solids (order-independent, greedy 1:1 match). Robust to bodies
        being renumbered/reordered between the two kernels."""
        if len(self.bodies) != len(other.bodies):
            return False
        pool = list(other.bodies)
        for b in self.bodies:
            for i, o in enumerate(pool):
                if b.matches(o, **tol):
                    pool.pop(i)
                    break
            else:
                return False
        return True


def body_states(api: Onshape, ps: PartStudio, n_features: int) -> dict[int, State]:
    """{rollbackBarIndex: State} for indices 0..n_features. Index i is the state AFTER
    feature i-1 (i.e. rollback bar just past feature i-1); index 0 is the empty start.
    Best-effort per index so one failed checkpoint doesn't sink the whole oracle."""
    out: dict[int, State] = {}
    for rb in range(0, n_features + 1):
        try:
            res = api.call(f"{ps.path}/featurescript?rollbackBarIndex={rb}",
                           {"script": _FS_BODIES, "queries": {}})
            bodies = [Body.from_fs(d) for d in (_unwrap(res.get("result")) or [])]
            out[rb] = State(bodies=bodies)
        except Exception:  # noqa: BLE001 — a missing checkpoint is tolerable; caller sees the gap
            continue
    return out
