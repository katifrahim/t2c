#!/usr/bin/env python3
"""Reconstruct the model a modeller built and export it to STEP for human review.

The loop runs t2c as a stdio server that dies with the agent, so the built model is gone
by the time we could grab it. But calls.N.json is the exact ordered list of t2c tool calls,
so we replay them through the real t2c_mcp engine (same approach as onshape/verify.py) and
export the active model. Best-effort: a call that failed for the modeller is skipped, not
fatal — we just want the final geometry the modeller ended up with.

Runs the replay in a spawn child with a timeout, because a bad OCCT op can hang
uninterruptibly from Python. With a 3rd arg it also writes inspect_model(detail="standard")
geometry of the reconstructed model (exact bbox / features / per-part placement) for the judge.
Usage: export_model.py <calls.N.json> <out.step> [geometry.json]
"""
import asyncio, inspect, json, multiprocessing as mp, os, sys

_MCP_SRC = os.path.join(os.path.dirname(__file__), "..", "mcp_server", "src")  # THIS worktree's src
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)


async def _replay_and_export(calls, out, geom_out=None):
    import t2c_mcp as _mcp
    import cadquery as cq
    _mcp._sessions.pop(_mcp._LOCAL_SID, None)                 # fresh workspace
    ran = 0
    for c in calls:
        name = (c.get("tool") or "").rsplit("__", 1)[-1]       # mcp__t2c__workplane_api -> workplane_api
        fn = getattr(_mcp, name, None)
        if not inspect.iscoroutinefunction(fn):
            continue
        try:
            await fn(ctx=None, **(c.get("input") or {}))
            ran += 1
        except Exception:
            continue                                           # best-effort: keep going
    sess = _mcp._sessions.get(_mcp._LOCAL_SID)
    if not sess or not getattr(sess, "current", None):
        raise RuntimeError(f"no active model after replaying {ran} calls")
    obj = sess.state[sess.current]
    try:
        _mcp.ensure_ap242_schema()                             # richest STEP schema (matches /export)
    except Exception:
        pass
    if isinstance(obj, _mcp.Assembly):
        try:
            obj.export(out, exportType="STEP")                 # rich: parts/colors/hierarchy
        except Exception:
            cq.exporters.export(obj.toCompound(), out, exportType="STEP")  # geometry-only fallback
    else:
        shape = obj.val() if hasattr(obj, "val") else obj
        cq.exporters.export(shape, out, exportType="STEP")
    if geom_out:                                             # exact geometry for the judge (best-effort)
        try:
            g = await _mcp.inspect_model(ctx=None, detail="standard")
            open(geom_out, "w").write(g if isinstance(g, str) else json.dumps(g, indent=2))
        except Exception:
            pass
    return ran, sess.current


def _worker(calls, out, geom_out, q):
    try:
        ran, active = asyncio.run(_replay_and_export(calls, out, geom_out))
        q.put(("ok", f"replayed {ran} calls, active='{active}'"))
    except Exception as e:
        q.put(("err", str(e)[:200]))


def main():
    if len(sys.argv) < 3:
        print("usage: export_model.py <calls.json> <out.step> [geometry.json]"); return 1
    calls_path, out = sys.argv[1], sys.argv[2]
    geom_out = sys.argv[3] if len(sys.argv) > 3 else None
    calls = json.load(open(calls_path)).get("calls", [])
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    p = ctx.Process(target=_worker, args=(calls, out, geom_out, q))
    p.start(); p.join(240)
    if p.is_alive():
        p.terminate(); p.join()
        print("CAD export: TIMEOUT (OCCT hang) — no file written"); return 1
    status, info = q.get() if not q.empty() else ("err", "no result")
    ok = status == "ok" and os.path.exists(out) and os.path.getsize(out) > 0
    print(f"CAD export: {status} — {info}" + (f" -> {out} ({os.path.getsize(out)} bytes)" if ok else ""))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
