"""Self-validating tests for durable-snapshot pruning + restore fidelity.

These guard the two things that must never break:
  1. RESUME/REFERENCE — explicitly-named models and the active model survive a
     snapshot→restore round-trip, so the agent can select_model / _ref them.
  2. VIEWER — the restored active model (`sess.current`) is present and re-showable,
     which is exactly what /session/import does to keep the viewer showing the last
     model of a reopened chat.

Pruning only sheds OLD auto-named intermediates from the durable copy; the live
session is never touched. Tool coroutines are driven with asyncio.run()."""
import asyncio
import json
import pickle
import zlib

import cadquery as cq

import src.t2c_mcp as m
from src.t2c_mcp import (
    _bind, _store, _auto_name, _get, _show, _snapshot, _restore_into,
    _prune_for_snapshot, SNAPSHOT_KEEP_RECENT, _session_export, select_model,
)


def _box():
    return cq.Workplane("XY").box(1, 1, 1)


class _Req:
    """Minimal stand-in for a Starlette request for the /session/export route."""
    def __init__(self, sid):
        self.query_params = {"session": sid}
        self.headers = {}


def _fresh(sid):
    """A clean, bound session (as _bind does at every tool entry)."""
    m._sessions.pop(sid, None)
    return _bind(sid)


def test_prune_keeps_named_current_and_recent_drops_old_auto():
    sess = _fresh("t-prune")
    for nm in ("alpha", "beta", "gamma"):           # explicit store_as names
        _store(nm, _box())
    autos = []
    for _ in range(SNAPSHOT_KEEP_RECENT + 6):        # many auto-named intermediates
        n = _auto_name("workplane"); _store(n, _box()); autos.append(n)
    sess.current = autos[-1]

    pruned = _prune_for_snapshot(sess)

    assert {"alpha", "beta", "gamma"} <= set(pruned)          # named survive (reference)
    assert sess.current in pruned                             # active survives (viewer)
    names = list(sess.state.keys())
    for n in names[-SNAPSHOT_KEEP_RECENT:]:                   # recency window survives
        assert n in pruned
    old_autos = autos[: len(autos) - SNAPSHOT_KEEP_RECENT]
    assert any(n not in pruned for n in old_autos)            # old intermediates dropped
    assert len(pruned) < len(sess.state)                      # it actually shrank
    assert len(sess.state) == len(names)                     # live session untouched


def test_current_is_never_pruned_even_if_old_and_auto():
    sess = _fresh("t-cur")
    old_auto = _auto_name("workplane"); _store(old_auto, _box())   # first, oldest
    for _ in range(SNAPSHOT_KEEP_RECENT + 3):                       # push it out of recency
        _store(_auto_name("workplane"), _box())
    sess.current = old_auto                                         # active = the oldest auto
    pruned = _prune_for_snapshot(sess)
    assert old_auto in pruned                                       # still kept — viewer safe


def test_roundtrip_preserves_named_and_active_and_feeds_viewer():
    sess = _fresh("t-rt")
    _store("base", _box())
    for _ in range(SNAPSHOT_KEEP_RECENT + 4):
        _store(_auto_name("workplane"), _box())
    sess.current = "base"                                     # active is the named model
    blob = _snapshot(sess)

    sess2 = _fresh("t-rt2")
    before = sess2.viewer["version"]
    n = _restore_into(sess2, blob)
    assert n == len(sess2.state)
    assert "base" in sess2.state                             # named model restored (reference)
    assert sess2.current == "base"                          # active preserved
    assert _get("base").val().Volume() > 0                   # geometry intact + reference works

    # Mirror _session_import: re-show the active model → viewer payload updates.
    _show(sess2.state[sess2.current])
    assert sess2.viewer["payload"] is not None
    assert sess2.viewer["version"] > before


def test_counters_survive_so_auto_naming_continues_without_collision():
    sess = _fresh("t-cnt")
    for _ in range(3):
        _store(_auto_name("workplane"), _box())
    sess.current = "workplane_3"
    blob = _snapshot(sess)
    sess2 = _fresh("t-cnt2")
    _restore_into(sess2, blob)
    nxt = _auto_name("workplane")                            # bound session is sess2
    assert nxt == "workplane_4"                              # continues, no collision
    assert nxt not in {k for k in sess2.state}


def test_legacy_blob_without_auto_names_restores_intact():
    # A pre-change snapshot has no "auto_names" key. Everything must restore and be
    # treated as explicit (nothing wrongly pruned on a later save).
    sess = _fresh("t-legacy")
    _store("part1", _box())
    _store("part2", _box())
    legacy = zlib.compress(pickle.dumps(
        {"counters": {}, "current": "part2", "objects": dict(sess.state)}))
    sess2 = _fresh("t-legacy2")
    _restore_into(sess2, legacy)
    assert set(sess2.state) == {"part1", "part2"}
    assert sess2.current == "part2"
    assert sess2.auto_names == set()                         # no autos → nothing pruned later


# ── skip-when-unchanged (#2): /session/export returns 304 so the web layer skips ──

def test_export_304_when_unchanged_200_when_changed():
    sid = "t-skip"
    _fresh(sid)
    _store("a", _box())                                              # rev bump
    assert asyncio.run(_session_export(_Req(sid))).status_code == 200   # first export
    assert asyncio.run(_session_export(_Req(sid))).status_code == 304   # unchanged → skip
    assert asyncio.run(_session_export(_Req(sid))).status_code == 304   # still unchanged
    _store("b", _box())                                              # CAD state changed
    assert asyncio.run(_session_export(_Req(sid))).status_code == 200   # export again
    assert asyncio.run(_session_export(_Req(sid))).status_code == 304   # then clean again


def test_empty_session_still_404_not_304():
    sid = "t-empty"
    _fresh(sid)                                                     # placeholder only, no stored objects
    assert asyncio.run(_session_export(_Req(sid))).status_code == 404


def test_select_model_marks_snapshot_dirty():
    # Changing the ACTIVE model must re-export, so a reopened chat shows the right one.
    _fresh("local")                                                # select_model(ctx=None) binds "local"
    _store("first", _box())
    _store("second", _box())
    assert asyncio.run(_session_export(_Req("local"))).status_code == 200
    assert asyncio.run(_session_export(_Req("local"))).status_code == 304
    assert json.loads(asyncio.run(select_model("first")))["status"] == "success"
    assert asyncio.run(_session_export(_Req("local"))).status_code == 200   # dirty again


def test_restored_session_is_clean_no_immediate_reexport():
    sid = "t-clean"
    src = _fresh("t-clean-src")
    _store("base", _box())
    blob = _snapshot(src)
    sess = _fresh(sid)
    _restore_into(sess, blob)
    # Freshly restored state equals what's stored → no wasteful immediate re-upload.
    assert asyncio.run(_session_export(_Req(sid))).status_code == 304
    _store("more", _box())
    assert asyncio.run(_session_export(_Req(sid))).status_code == 200   # real change re-exports
