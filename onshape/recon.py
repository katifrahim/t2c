"""Closed-loop reconstruction engine.

Instead of hand-coding each feature's conventions and hoping, we generate a small set
of candidate op-sequences per feature — enumerating the genuinely ambiguous choices
(extrude direction ±, which loops form a region, symmetric or not, which body an
op targets) — execute each through the REAL t2c_mcp engine, and keep the candidate
whose resulting solids match Onshape's own geometry at that feature (the oracle). So
conventions are DISCOVERED per feature, not encoded, and any error is caught at the
feature that causes it. This is what makes translation universal instead of a stream
of per-model fixes.

This module holds the engine + the sketch/extrude translators (the parametric core).
Pattern/boolean/fillet/mirror/... plug in the same way: each yields candidates; the
oracle picks.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from dataclasses import dataclass, field

from onshape import ir
from onshape.emit import _profile_ops, _plane_init  # profile curves -> workplane ops
from onshape.oracle import Body, State

_MCP_SRC = os.path.join(os.path.dirname(__file__), "..", "mcp_server", "src")
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)
import t2c_mcp as _mcp  # noqa: E402


# --- fingerprinting a reconstructed body (same shape as an oracle Body) ----------
def fingerprint(wp) -> Body | None:
    """CadQuery Workplane -> oracle Body (mm), or None if it holds no solid."""
    try:
        val = wp.val()
    except Exception:  # noqa: BLE001
        return None
    if val is None or not hasattr(val, "Volume"):
        return None
    try:
        vol = val.Volume()
        area = sum(f.Area() for f in val.Faces())
        bb = val.BoundingBox()
        c = val.Center()
    except Exception:  # noqa: BLE001
        return None
    return Body(volume=vol, area=area, centroid=[c.x, c.y, c.z],
                bbox_min=[bb.xmin, bb.ymin, bb.zmin], bbox_max=[bb.xmax, bb.ymax, bb.zmax])


# --- explicit sketch plane (NO canonicalization — keeps the true frame, so extrude
# direction is unambiguous rather than being recovered by luck) -------------------
def explicit_plane(matrix: list[float]) -> ir.Plane:
    o = [matrix[3] * 1e3, matrix[7] * 1e3, matrix[11] * 1e3]
    x = [matrix[0], matrix[4], matrix[8]]
    n = [matrix[2], matrix[6], matrix[10]]
    return ir.Plane(origin=[round(v, 9) for v in o],
                    x_dir=[round(v, 9) for v in x], normal=[round(v, 9) for v in n])


# --- the engine: run steps through real t2c_mcp, with snapshot/restore for search -
class Engine:
    """Drives the real workplane_api against a persistent session. `live` tracks the
    mcp names of the current top-level solids (what the oracle State is compared to)."""

    def __init__(self):
        _mcp._sessions.pop(_mcp._LOCAL_SID, None)
        self.sess = _mcp._get_session(_mcp._LOCAL_SID)
        self.live: list[str] = []
        self.steps: list[dict] = []
        self._n = 0

    def name(self) -> str:
        self._n += 1
        return f"body{self._n}"

    def snapshot(self):
        return (dict(self.sess.state), dict(self.sess.counters), self.sess.current, list(self.live))

    def restore(self, snap):
        s = self.sess
        s.state.clear(); s.state.update(snap[0])
        s.counters.clear(); s.counters.update(snap[1])
        s.current = snap[2]
        self.live = list(snap[3])

    def _apply(self, payload: dict) -> dict:
        """Execute one workplane_api call on the live session; return its result."""
        out = asyncio.run(_mcp.workplane_api(ctx=None, **payload))
        r = json.loads(out) if isinstance(out, str) else out
        if r.get("status") != "success":
            raise RuntimeError(r.get("error") or r.get("message") or "step failed")
        return r

    def state_of(self, names: list[str]) -> State:
        bodies = []
        for n in names:
            wp = self.sess.state.get(n)
            fp = fingerprint(wp) if wp is not None else None
            if fp is not None:
                bodies.append(fp)
        return State(bodies=bodies)

    def try_candidate(self, payloads: list[dict], live_after: list[str]) -> State | None:
        """Run a candidate (list of workplane_api payloads) on a snapshot; return the
        resulting live-body State, then roll back. None if any payload errors."""
        snap = self.snapshot()
        try:
            for p in payloads:
                self._apply(p)
            return self.state_of(live_after)
        except Exception:  # noqa: BLE001
            return None
        finally:
            self.restore(snap)

    def commit(self, payloads: list[dict], live_after: list[str]):
        for p in payloads:
            self._apply(p)
            self.steps.append({"step": f"Step {len(self.steps) + 1}",
                               "toolName": "workplane_api", "input": p})
        self.live = list(live_after)


# --- candidate generators (translators) ------------------------------------------
@dataclass
class Candidate:
    """A way to realize a feature: the payloads to run + the resulting live-body names.
    `label` is for diagnostics (which convention won)."""
    payloads: list[dict]
    live_after: list[str]
    label: str = ""


def profile_variants(sketch_profiles: list[ir.Profile]) -> list[tuple[str, list[ir.Profile]]]:
    """Region-selection variants for an extrude's 2D profile. The winning one is picked
    by the oracle, so we don't need to KNOW whether a sketch is a disc, an annulus, or
    a multi-region shape — we offer the sensible options and let the geometry decide."""
    out: list[tuple[str, list[ir.Profile]]] = []
    if not sketch_profiles:
        return out
    # 1) all regions together (concentric loops -> holes via even-odd): the annulus case
    out.append(("all-regions", sketch_profiles))
    # 2) each region alone (a plain single-region extrude, or picking one of many)
    if len(sketch_profiles) > 1:
        for i, p in enumerate(sketch_profiles):
            out.append((f"region[{i}]", [p]))
    return out


def extrude_candidates(engine: Engine, plane: ir.Plane, profiles: list[ir.Profile],
                       depth: float | None, op: str, base_live: list[str]) -> list[Candidate]:
    """Enumerate {region-set} x {distance ±, symmetric} x {target body, for add/cut}.
    depth may be None (unknown) -> the sign/magnitude falls to search over caps later;
    here we search sign when a magnitude is known."""
    init = _plane_init(plane)
    mags = [depth] if depth else []
    dists: list[tuple[str, float, bool]] = []
    for m in mags:
        dists.append((f"+{m}", abs(m), False))
        dists.append((f"-{m}", -abs(m), False))
        dists.append((f"±{m}", abs(m), True))  # symmetric
    cands: list[Candidate] = []
    for pname, profs in profile_variants(profiles):
        prof_ops = _profile_ops(profs)
        for dname, dist, sym in dists:
            ex = {"method": "extrude", "params": {"until": dist, "combine": False, "both": sym}}
            tool = engine.name()
            make = {"operations": prof_ops + [ex], "init_params": init, "store_as": tool}
            if op == "new":
                cands.append(Candidate([make], base_live + [tool], f"{pname}|{dname}|new"))
            else:  # add/cut/intersect: the tool solid is booleaned into a target body
                meth = {"add": "union", "cut": "cut", "intersect": "intersect"}[op]
                for ti, target in enumerate(base_live):
                    res = engine.name()
                    boolean = {"operations": [{"method": meth, "args": [{"_ref": tool}]}],
                               "start_from": target, "store_as": res}
                    new_live = [res if x == target else x for x in base_live]
                    cands.append(Candidate([make, boolean], new_live,
                                           f"{pname}|{dname}|{op}->{ti}"))
    return cands


def pick(engine: Engine, cands: list[Candidate], target: State) -> Candidate | None:
    """Return the candidate whose resulting live State matches the oracle target, or the
    closest by total-volume error if none match exactly (caller decides to accept)."""
    best, best_err = None, float("inf")
    for c in cands:
        st = engine.try_candidate(c.payloads, c.live_after)
        if st is None:
            continue
        if st.matches(target):
            return c
        err = abs(st.total_volume - target.total_volume)
        if err < best_err:
            best, best_err = c, err
    return best  # best-effort (may not be within tolerance)
