"""Offline tests for normalize -> emit -> execute (real MCP engine, no network).

Uses the washer's saved raw API response for the circle path and a synthetic
square for the line-loop assembler; both are checked against analytic volumes.
"""
import json
import math
import os

import pytest

from onshape import ir
from onshape.emit import emit_model
from onshape.normalize import _sketch_profiles, normalize, parse_length_mm

# Saved raw Onshape API dump used only as a data-schema fixture (Onshape's own
# response format), not any prior translation logic.
_WASHER_RAW = os.path.join(os.path.dirname(__file__), "..", "..", "..",
                           "onshape-exp", "washer.raw.json")


def _run(steps):
    from onshape.verify import run_steps
    return run_steps(steps)


def test_parse_length_mm():
    assert parse_length_mm("1.80 mm") == pytest.approx(1.8)
    assert parse_length_mm("0.5 in") == pytest.approx(12.7)
    assert parse_length_mm("2 cm") == pytest.approx(20.0)
    assert parse_length_mm("5") == pytest.approx(5.0)
    assert parse_length_mm("#width/2") is None      # variable -> flagged
    assert parse_length_mm(None) is None


def test_line_loop_assembler_square():
    def pt(i, x, y):
        return {"sketchEntityType": "skPoint", "sketchEntityId": i,
                "position2d": {"x": x / 1000, "y": y / 1000}}

    def ln(i, a, b):
        return {"sketchEntityType": "skLineSegment", "sketchEntityId": i,
                "startPointId": a, "endPointId": b, "geometry": {}}

    sq = {"entities": [pt("p1", 0, 0), pt("p2", 20, 0), pt("p3", 20, 20), pt("p4", 0, 20),
                       ln("l1", "p1", "p2"), ln("l2", "p2", "p3"),
                       ln("l3", "p3", "p4"), ln("l4", "p4", "p1")]}
    profs = _sketch_profiles(sq)
    assert len(profs) == 1
    assert [c.kind for c in profs[0].curves] == ["line"] * 4

    sk = ir.Sketch(source={"id": "sq"}, plane=ir.Plane(name="XY"), profiles=profs)
    ex = ir.Extrude(source={"id": "e"}, profile_ref="sq", distance=3.0, op="new")
    props = _run(emit_model(ir.Model(ops=[sk, ex])))
    assert props["volume"] == pytest.approx(20 * 20 * 3)


@pytest.mark.skipif(not os.path.exists(_WASHER_RAW), reason="washer fixture unavailable")
def test_washer_annulus_from_real_data():
    raw = json.load(open(_WASHER_RAW))
    model = normalize(raw["features"], raw["sketches"], url="washer")
    sk1 = next(o for o in model.ops if isinstance(o, ir.Sketch))
    ex1 = next(o for o in model.ops if isinstance(o, ir.Extrude))
    assert ex1.profile_ref == sk1.source["id"]           # extrude linked to its sketch
    assert [len(p.curves) for p in sk1.profiles] == [1, 1]  # two circles

    props = _run(emit_model(ir.Model(ops=[sk1, ex1])))
    expected = math.pi * (38.1**2 - 28.5**2) * 1.80        # annulus
    assert props["volume"] == pytest.approx(expected, rel=1e-6)


def test_loop_reorientation_scrambled_and_reversed():
    # Same square, but segments given out of order AND some reversed. The assembler
    # must still form one closed loop whose consecutive curves connect head-to-tail
    # (the bug that produced degenerate zero-length arcs/lines).
    from onshape.normalize import _assemble_loops, _dist

    def seg(a, b):
        return ir.Curve("line", {"start": list(a), "end": list(b)})

    scrambled = [seg((20, 20), (20, 0)),   # reversed right edge
                 seg((0, 0), (20, 0)),     # bottom
                 seg((0, 20), (0, 0)),     # reversed left edge
                 seg((20, 20), (0, 20))]   # reversed top
    loops = _assemble_loops(scrambled)
    assert len(loops) == 1
    loop = loops[0]
    assert len(loop) == 4
    for a, b in zip(loop, loop[1:]):
        assert _dist(a.data["end"], b.data["start"]) < 1e-6   # head-to-tail
    assert _dist(loop[-1].data["end"], loop[0].data["start"]) < 1e-6  # closed


def test_reverse_curve_keeps_arc_midpoint():
    from onshape.normalize import _reverse

    arc = ir.Curve("arc", {"start": [0, 0], "mid": [1, 1], "end": [2, 0]})
    r = _reverse(arc)
    assert r.data["start"] == [2, 0] and r.data["end"] == [0, 0]
    assert r.data["mid"] == [1, 1]


def test_unsupported_features_recorded():
    raw = json.load(open(_WASHER_RAW))
    model = normalize(raw["features"], raw["sketches"], url="washer")
    kinds = {u["type"] for u in model.unsupported}
    assert {"circularPattern", "booleanBodies", "fillet"} <= kinds
