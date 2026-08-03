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
        if not val.Solids():      # a wire/face/empty compound is not a solid body
            return None           # (Wire.Volume() misleadingly returns its perimeter)
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
        # Search runs each candidate through the real workplane_api, but the viewer
        # tessellation (_show) on every trial is pure waste (hundreds of renders) — and
        # heavy enough to OOM. No-op it; reconstruction only needs the geometry.
        _mcp._show = lambda *a, **k: None
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

    def _run_candidate(self, cand: "Candidate", record: bool):
        """Apply a candidate's payloads (skip-chaining if requested); return the applied
        payloads and the resulting live-body list. Raises on hard failure of a
        non-skip candidate."""
        if not cand.skip_failures:
            for p in cand.payloads:
                self._apply(p)
                if record:
                    self.steps.append({"step": f"Step {len(self.steps) + 1}",
                                       "toolName": "workplane_api", "input": p})
            return cand.live_after
        last = cand.target
        for p in cand.payloads:
            p = dict(p); p["start_from"] = last
            try:
                self._apply(p)
            except Exception:  # noqa: BLE001 — this edge can't take the op; skip it
                continue
            last = p["store_as"]
            if record:
                self.steps.append({"step": f"Step {len(self.steps) + 1}",
                                   "toolName": "workplane_api", "input": p})
        return [last if x == cand.target else x for x in (cand.base_live or [])]

    def try_candidate(self, cand: "Candidate") -> tuple[State | None, list[str] | None]:
        """Run a candidate on a snapshot; return (resulting live State, live_after) then
        roll back. (None, None) if the candidate fails outright."""
        snap = self.snapshot()
        try:
            live = self._run_candidate(cand, record=False)
            return self.state_of(live), live
        except Exception:  # noqa: BLE001
            return None, None
        finally:
            self.restore(snap)

    def commit(self, cand: "Candidate"):
        self.live = list(self._run_candidate(cand, record=True))


# --- crash/hang-isolated engine (persistent worker subprocess) -------------------
# A bad candidate (invalid boolean, degenerate fillet) can hang OCCT uninterruptibly
# or blow memory. In-process, that kills the whole search. So candidates run in a
# persistent child: the parent enforces a wall-clock timeout, and on hang/crash kills
# the child, respawns it, and replays the committed steps to rebuild state. One child
# amortizes process cost across all trials (vs spawn-per-candidate).
def _child_apply(payload: dict):
    out = asyncio.run(_mcp.workplane_api(ctx=None, **payload))
    r = json.loads(out) if isinstance(out, str) else out
    if r.get("status") != "success":
        raise RuntimeError(r.get("error") or r.get("message") or "step failed")


def _child_run(sess, cand: dict) -> list[str]:
    """Apply a candidate dict in the child; return resulting live names."""
    if not cand["skip_failures"]:
        for p in cand["payloads"]:
            _child_apply(p)
        return cand["live_after"]
    last = cand["target"]
    for p in cand["payloads"]:
        p = dict(p); p["start_from"] = last
        try:
            _child_apply(p)
        except Exception:  # noqa: BLE001
            continue
        last = p["store_as"]
    return [last if x == cand["target"] else x for x in (cand["base_live"] or [])]


def _child_main(conn):
    _mcp._sessions.pop(_mcp._LOCAL_SID, None)
    _mcp._show = lambda *a, **k: None
    sess = _mcp._get_session(_mcp._LOCAL_SID)
    while True:
        try:
            kind, arg = conn.recv()
        except EOFError:
            break
        if kind == "stop":
            break
        if kind == "apply":  # a committed step (state persists)
            try:
                _child_apply(arg); conn.send(("ok", None))
            except Exception as e:  # noqa: BLE001
                conn.send(("err", str(e)[:200]))
        elif kind == "try":  # trial on a snapshot; state rolled back after
            snap = (dict(sess.state), dict(sess.counters), sess.current)
            try:
                live = _child_run(sess, arg)
                st = State(bodies=[fp for n in live
                                   if (fp := fingerprint(sess.state.get(n))) is not None])
                conn.send(("ok", (st, live)))
            except Exception as e:  # noqa: BLE001
                conn.send(("rej", str(e)[:120]))
            finally:
                sess.state.clear(); sess.state.update(snap[0])
                sess.counters.clear(); sess.counters.update(snap[1])
                sess.current = snap[2]


class WorkerEngine:
    """Same interface as Engine (name/live/steps/try_candidate/commit) but every trial
    runs in a crash- and hang-isolated child process."""

    def __init__(self, timeout: float = 20.0):
        import multiprocessing as mp
        self._mp = mp.get_context("spawn")
        self.timeout = timeout
        self.live: list[str] = []
        self.steps: list[dict] = []
        self._committed: list[dict] = []   # payloads to replay after a respawn
        self._n = 0
        self._start()

    def _start(self):
        self._pconn, cconn = self._mp.Pipe()
        self.proc = self._mp.Process(target=_child_main, args=(cconn,), daemon=True)
        self.proc.start()
        for p in self._committed:            # rebuild committed state in the fresh child
            self._pconn.send(("apply", p))
            self._pconn.recv()

    def _restart(self):
        try:
            self.proc.terminate(); self.proc.join(3)
        except Exception:  # noqa: BLE001
            pass
        self._start()

    def name(self) -> str:
        self._n += 1
        return f"body{self._n}"

    def _cand_dict(self, cand: "Candidate") -> dict:
        return {"payloads": cand.payloads, "skip_failures": cand.skip_failures,
                "target": cand.target, "base_live": cand.base_live,
                "live_after": cand.live_after}

    def try_candidate(self, cand: "Candidate"):
        self._pconn.send(("try", self._cand_dict(cand)))
        if not self._pconn.poll(self.timeout):    # hang -> kill, respawn, reject
            self._restart()
            return None, None
        tag, val = self._pconn.recv()
        if tag == "ok":
            return val
        return None, None                          # rejected (bad geometry)

    def commit(self, cand: "Candidate"):
        live = cand.live_after
        if not cand.skip_failures:
            for p in cand.payloads:
                self._pconn.send(("apply", p)); self._pconn.recv()
                self._committed.append(p)
                self.steps.append({"step": f"Step {len(self.steps) + 1}",
                                   "toolName": "workplane_api", "input": p})
        else:
            last = cand.target
            for p in cand.payloads:
                p = dict(p); p["start_from"] = last
                self._pconn.send(("apply", p))
                tag, _ = self._pconn.recv()
                if tag != "ok":
                    continue
                last = p["store_as"]
                self._committed.append(p)
                self.steps.append({"step": f"Step {len(self.steps) + 1}",
                                   "toolName": "workplane_api", "input": p})
            live = [last if x == cand.target else x for x in (cand.base_live or [])]
        self.live = list(live)

    def close(self):
        try:
            self._pconn.send(("stop", None)); self.proc.join(2)
        except Exception:  # noqa: BLE001
            pass


# --- candidate generators (translators) ------------------------------------------
@dataclass
class Candidate:
    """A way to realize a feature: the payloads to run + the resulting live-body names.
    `label` is for diagnostics (which convention won). When `skip_failures` is set the
    payloads are a resilient chain over `target` — each is start_from the last SUCCESS
    and failures are skipped (per-edge fillet/chamfer on topology OCCT can't do at once);
    `base_live` is the live set the chain result substitutes into."""
    payloads: list[dict]
    live_after: list[str]
    label: str = ""
    skip_failures: bool = False
    target: str | None = None
    base_live: list[str] | None = None


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
            if op == "new" or not base_live:
                cands.append(Candidate([make], base_live + [tool], f"{pname}|{dname}|new"))
                continue
            if op == "add":  # ADD may stay a separate body — let the oracle decide
                cands.append(Candidate([make], base_live + [tool], f"{pname}|{dname}|add-sep"))
            meth = {"add": "union", "cut": "cut", "intersect": "intersect"}[op]
            for ti, target in enumerate(base_live):  # ...or merge into an existing body
                res = engine.name()
                boolean = {"operations": [{"method": meth, "args": [{"_ref": tool}]}],
                           "start_from": target, "store_as": res}
                new_live = [res if x == target else x for x in base_live]
                cands.append(Candidate([make, boolean], new_live, f"{pname}|{dname}|{op}->{ti}"))
            if op in ("cut", "intersect") and len(base_live) > 1:
                # a REMOVE spanning several bodies (patterned holes): cut the one tool
                # from EVERY body — non-intersecting bodies are unchanged (a no-op).
                payloads, new_live = [make], []
                for target in base_live:
                    res = engine.name()
                    payloads.append({"operations": [{"method": meth, "args": [{"_ref": tool}]}],
                                     "start_from": target, "store_as": res})
                    new_live.append(res)
                cands.append(Candidate(payloads, new_live, f"{pname}|{dname}|{op}-all"))
    return cands


def pattern_candidates(engine: Engine, count: int, angle: float, equal_space: bool,
                       axes: list[tuple[list[float], list[float]]],
                       base_live: list[str]) -> list[Candidate]:
    """Circular pattern: rotate-copy a source body into `count` instances. Neither the
    source body NOR the axis is hand-picked — every (live body, candidate axis) pair is
    offered and the oracle selects the combination whose copies land where Onshape's do.
    `axes` is a small set of (origin, direction) covering the common cases (principal
    directions through the origin / the pattern's own centre)."""
    k = max(int(count), 1)
    step = (angle / count) if equal_space else angle
    cands: list[Candidate] = []
    for ai, (a0, ad) in enumerate(axes):
        a1 = [a0[0] + ad[0], a0[1] + ad[1], a0[2] + ad[2]]
        for si, src in enumerate(base_live):
            payloads, new = [], []
            for j in range(1, k):
                copy = engine.name()
                payloads.append({"operations": [{"method": "rotate", "args": [a0, a1, j * step]}],
                                 "start_from": src, "store_as": copy})
                new.append(copy)
            if payloads:
                cands.append(Candidate(payloads, base_live + new, f"pattern src{si} axis{ai} x{k}"))
    return cands


def boolean_candidates(engine: Engine, op: str, base_live: list[str]) -> list[Candidate]:
    """Boolean of multiple bodies. Union-all is the common case (washer merges 5->1);
    for cut/intersect we offer base-vs-rest. The oracle confirms operand grouping."""
    if len(base_live) < 2:
        return []
    meth = {"union": "union", "cut": "cut", "intersect": "intersect"}.get(op, "union")
    res = engine.name()
    ops = [{"method": meth, "args": [{"_ref": b}]} for b in base_live[1:]]
    return [Candidate([{"operations": ops, "start_from": base_live[0], "store_as": res}],
                      [res], f"{op}-all")]


def round_candidates(engine: Engine, kind: str, points: list[list[float]], amount: float,
                     base_live: list[str]) -> list[Candidate]:
    """fillet/chamfer: select target edges by 3D fingerprint (NearestToPoint), apply the
    op. Two candidates PER target body — all-at-once, and per-edge-skipping (OCCT often
    fails the combined pass on messy topology but succeeds edge-by-edge). skip_failures
    lets the per-edge candidate drop only the edges OCCT rejects."""
    from onshape.emit import _point_selector
    meth = "fillet" if kind == "fillet" else "chamfer"
    cands: list[Candidate] = []
    for ti, target in enumerate(base_live):
        res = engine.name()
        all_ops = [{"method": "edges", "args": [_point_selector(points)]},
                   {"method": meth, "args": [amount]}]
        cands.append(Candidate([{"operations": all_ops, "start_from": target, "store_as": res}],
                               [res if x == target else x for x in base_live],
                               f"{kind}-all->{ti}"))
        # per-edge: each edge its own step (re-selected by point), chained; skip failures
        payloads, cur = [], target
        for pi, p in enumerate(points):
            nxt = engine.name()
            payloads.append({"operations": [
                {"method": "edges", "args": [{"_type": "NearestToPointSelector", "pnt": p}]},
                {"method": meth, "args": [amount]}], "start_from": cur, "store_as": nxt})
            cur = nxt
        cands.append(Candidate(payloads, [], f"{kind}-seq->{ti}",
                               skip_failures=True, target=target, base_live=list(base_live)))
    return cands


def pick(engine: Engine, cands: list[Candidate], target: State,
         **tol) -> tuple[Candidate | None, bool]:
    """Return (candidate, exact) — the candidate whose resulting live State matches the
    oracle (exact=True), else the closest by total-volume error (exact=False), else
    (None, False)."""
    best, best_err = None, float("inf")
    for c in cands:
        st, _ = engine.try_candidate(c)
        if st is None:
            continue
        if st.matches(target, **tol):
            return c, True
        err = abs(st.total_volume - target.total_volume)
        if err < best_err:
            best, best_err = c, err
    return best, False


# --- the full closed-loop driver -------------------------------------------------
def reconstruct(api, ps, verbose: bool = True, dump: set | None = None):
    """Reconstruct a whole Part Studio feature-by-feature, each verified against the
    oracle. Returns (steps, report) where report[i] = (index, type, name, label, exact).
    No feature's conventions are hand-decided: every feature offers candidates and the
    oracle picks. A feature that no candidate matches is flagged (later: B-rep fallback)."""
    from onshape.normalize import (_msg, _params, _enum, _sketch_profiles, caps_to_profiles,
                                   _cap_distance, parse_length_mm, _parse_angle_deg,
                                   _extrude_op, _BOOL_OP)
    from onshape.extract import resolve_extrude_caps, resolve_created_faces
    from onshape.oracle import body_states

    feats = api.features(ps).get("features", [])
    sk = {s["featureId"]: s for s in api.sketches(ps).get("sketches", [])}
    sketch_ids = set(sk)
    oracle = body_states(api, ps, len(feats))

    extrudes = [(_msg(f)["featureId"], i + 1) for i, f in enumerate(feats)
                if _msg(f).get("featureType") == "extrude" and not _msg(f).get("suppressed")]
    caps = resolve_extrude_caps(api, ps, extrudes)
    mod_ids = [_msg(f)["featureId"] for f in feats
               if _msg(f).get("featureType") in ("fillet", "chamfer") and not _msg(f).get("suppressed")]
    edge_pts = resolve_created_faces(api, ps, mod_ids)

    eng = WorkerEngine()
    last_sketch = None
    report: list[tuple] = []

    for i, f in enumerate(feats):
        m = _msg(f)
        ft, fid, nm = m.get("featureType"), m.get("featureId"), m.get("name") or ""
        if m.get("suppressed"):
            continue
        tgt = oracle.get(i + 1)
        cands: list[Candidate] = []

        if ft == "newSketch":
            last_sketch = fid
            report.append((i, ft, nm, "sketch (no solid)", True))
            if verbose:
                print(f"  --  f{i:2d} {ft:15s} {nm[:20]:20s}")
            continue
        elif ft == "extrude":
            op, _why = _extrude_op(f, sketch_ids, last_sketch)
            ref = op.profile_ref
            matrix = (sk.get(ref) or {}).get("sketchMatrix")
            plane = explicit_plane(matrix) if matrix else ir.Plane(name="XY")
            depth = op.distance
            sources: list[tuple[str, list]] = []
            if ref in sk:
                sources.append(("sk", _sketch_profiles(sk[ref])))
            if fid in caps and matrix:
                sources.append(("caps", caps_to_profiles(caps[fid], matrix)))
                if depth is None:
                    d = _cap_distance(caps[fid], matrix)
                    depth = abs(d) if d else None
            for _src, profs in sources:
                if profs:
                    cands += extrude_candidates(eng, plane, profs, depth, op.op, eng.live)
        elif ft == "circularPattern":
            P = _params(f)
            count = parse_length_mm((P.get("instanceCount") or {}).get("expression"))
            angle = _parse_angle_deg((P.get("angle") or {}).get("expression"))
            eq = bool((P.get("equalSpace") or {}).get("value", True))
            if count and angle is not None:
                # Candidate axes: principal directions through the origin and through the
                # pattern's own centre (mean of the resulting bodies' centroids lies ON a
                # circular pattern's axis). The oracle selects the real one.
                pts = [[0.0, 0.0, 0.0]]
                if tgt and tgt.bodies:
                    cs = [b.centroid for b in tgt.bodies]
                    pts.append([sum(c[k] for c in cs) / len(cs) for k in range(3)])
                axes = [(o, d) for o in pts
                        for d in ([1, 0, 0], [0, 1, 0], [0, 0, 1])]
                cands = pattern_candidates(eng, int(round(count)), angle, eq, axes, eng.live)
        elif ft == "booleanBodies":
            bop = _BOOL_OP.get(_enum(_params(f).get("operationType")), "union")
            cands = boolean_candidates(eng, bop, eng.live)
        elif ft in ("fillet", "chamfer"):
            faces = edge_pts.get(fid) or []
            points = [x["at"] for x in faces if isinstance(x, dict) and "at" in x]
            P = _params(f)
            if ft == "fillet":
                amt = parse_length_mm((P.get("radius") or {}).get("expression"))
                if amt is None:
                    rs = [x.get("r") for x in faces if x.get("r")]
                    amt = sum(rs) / len(rs) if rs else None
            else:
                amt = parse_length_mm((P.get("width") or P.get("length") or {}).get("expression"))
            if points and amt:
                cands = round_candidates(eng, ft, points, amt, eng.live)

        if dump and i in dump and cands:
            print(f"    [dump f{i}] oracle target: nB={len(tgt.bodies) if tgt else '?'} "
                  f"totV={tgt.total_volume if tgt else '?'}")
            for c in cands:
                st, _ = eng.try_candidate(c)
                if st is None:
                    print(f"      {c.label:32s} -> FAIL/reject")
                else:
                    print(f"      {c.label:32s} -> nB={len(st.bodies)} totV={st.total_volume:.2f}"
                          + ("  <-- MATCH" if (tgt and st.matches(tgt)) else ""))
        win, exact = pick(eng, cands, tgt) if (cands and tgt is not None) else (None, False)
        if win:
            eng.commit(win)
        label = win.label if win else "no candidate"
        report.append((i, ft, nm, label, bool(win and exact)))
        if verbose:
            flag = "OK " if (win and exact) else ("~~ " if win else "XX ")
            print(f"  {flag}f{i:2d} {ft:15s} {nm[:20]:20s} -> {label}"
                  + ("" if exact else "   [NOT EXACT]"))
    steps = list(eng.steps)
    eng.close()
    return steps, report


# --- CLI -------------------------------------------------------------------------
def _cli(argv: list[str]) -> int:
    """python -m onshape.recon <part-studio-url> [--out FILE.json] [--name NAME]

    Reconstructs the model through the closed loop, verifies the emitted steps
    end-to-end against Onshape's mass-properties, and writes the templates.steps."""
    if not argv:
        print(_cli.__doc__)
        return 2
    from onshape.client import Onshape, parse_url
    url = argv[0]
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    name = argv[argv.index("--name") + 1] if "--name" in argv else None

    api = Onshape()
    ps = parse_url(url)
    steps, report = reconstruct(api, ps)
    matched = sum(1 for r in report if r[4])
    print(f"\nfeatures matched: {matched}/{len(report)}   steps: {len(steps)}")

    verified, deltas = None, None
    if steps:
        from onshape.verify import run_steps_guarded, fetch_ground_truth, compare, Geometry
        try:
            props = run_steps_guarded(steps, timeout=90)
            res = compare(Geometry.from_mcp_props(props), fetch_ground_truth(ps, api))
            verified, deltas = res.ok, res.deltas
            print(f"end-to-end verified: {res.ok} | {res.reason}")
        except Exception as e:  # noqa: BLE001
            verified = False
            print(f"end-to-end verify failed: {str(e)[:160]}")

    if out:
        record = {
            "model": name, "url": url, "verified": verified, "verify": deltas,
            "features_total": len(report), "features_matched": matched,
            "n_steps": len(steps), "steps": steps,
            "report": [{"index": i, "type": t, "name": n, "how": lbl, "exact": ex}
                       for (i, t, n, lbl, ex) in report],
        }
        json.dump(record, open(out, "w"), indent=1)
        print(f"wrote {out}")
    return 0 if verified in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
