"""Self-validating tests for direct (history-free) editing of dumb solids (src/direct_edit.py)
and the edit_model MCP tool. Each test builds a solid, edits it the way the AI would (selecting
a feature by a point on it), and checks the geometry actually changed and stayed valid."""
import asyncio
import json

import cadquery as cq

from src.direct_edit import apply_edits, resolve_face, _cyl_axis
from src.step_import import describe_shape
from src.t2c_mcp import edit_model, inspect_model, _bind, _store


def _plate_with_hole():
    return cq.Workplane("XY").box(40, 40, 8).faces(">Z").workplane().hole(10)


def _hole_wall_point(cq_obj):
    d = describe_shape(cq_obj)
    face = next(f for f in d["parts"][0]["solids"][0]["faces"] if f["type"] == "Cylinder")
    return face["point_on_face"]


def test_resolver_hits_hole_wall_not_top_face():
    plate = _plate_with_hole()
    pt = _hole_wall_point(plate)                       # a point on the cylindrical wall
    face = resolve_face(plate.val().wrapped, {"near": pt})
    assert _cyl_axis(face) is not None                 # resolved to the hole, not a plane


def test_resize_hole_changes_diameter():
    plate = _plate_with_hole()
    pt = _hole_wall_point(plate)
    result, report = apply_edits(plate, [{"op": "resize_hole", "edge": {"near": pt}, "diameter": 20}])
    assert report["valid"]
    holes = describe_shape(result)["parts"][0]["solids"][0]["features"]["holes"]
    assert holes[0]["diameter"] == 20.0
    assert report["volume_after"] < report["volume_before"]   # bigger hole removes more


def test_remove_feature_deletes_hole():
    plate = _plate_with_hole()
    pt = _hole_wall_point(plate)
    result, report = apply_edits(plate, [{"op": "remove_feature", "faces": [{"near": pt}]}])
    assert report["valid"]
    feats = describe_shape(result)["parts"][0]["solids"][0].get("features", {})
    assert "holes" not in feats                         # hole gone, face healed
    assert report["volume_after"] > report["volume_before"]


def test_push_pull_face_adds_material():
    box = cq.Workplane("XY").box(20, 20, 10)
    result, report = apply_edits(box, [{"op": "push_pull_face", "face": {"near": [0, 0, 5]}, "distance": 5}])
    assert report["valid"]
    assert report["volume_after"] > report["volume_before"]   # top face moved out


def test_shell_hollows_solid():
    box = cq.Workplane("XY").box(20, 20, 10)
    result, report = apply_edits(box, [{"op": "shell", "faces": [{"near": [0, 0, 5]}], "thickness": 2}])
    assert report["valid"]
    assert report["volume_after"] < report["volume_before"]


def test_draft_face_stays_valid():
    box = cq.Workplane("XY").box(20, 20, 10)
    result, report = apply_edits(box, [{"op": "draft_face", "face": {"near": [10, 0, 0]}, "angle_deg": 5}])
    assert report["valid"]


def test_chained_edits():
    plate = _plate_with_hole()
    pt = _hole_wall_point(plate)
    _, report = apply_edits(plate, [
        {"op": "resize_hole", "edge": {"near": pt}, "diameter": 16},
        {"op": "push_pull_face", "face": {"near": [0, 0, 4]}, "distance": 3},
    ])
    assert report["valid"]


def test_bad_op_reports_error():
    box = cq.Workplane("XY").box(10, 10, 10)
    try:
        apply_edits(box, [{"op": "does_not_exist"}])
        assert False, "should have raised"
    except ValueError as e:
        assert "unknown op" in str(e)


def test_edit_model_tool_end_to_end():
    _bind(None)
    _store("plate", _plate_with_hole())
    insp = json.loads(asyncio.run(inspect_model(name="plate", detail="full")))
    wall = next(f for f in insp["description"]["parts"][0]["solids"][0]["faces"]
                if f["type"] == "Cylinder")
    out = json.loads(asyncio.run(edit_model(
        operations=[{"op": "resize_hole", "edge": {"near": wall["point_on_face"]}, "diameter": 20}],
        name="plate", store_as="plate2")))
    assert out["status"] == "success" and out["report"]["valid"]
    chk = json.loads(asyncio.run(inspect_model(name="plate2")))
    assert chk["description"]["parts"][0]["solids"][0]["features"]["holes"][0]["diameter"] == 20.0
