"""Self-validating tests for the STEP parser (src/step_import.py).

Each test generates its own STEP file with CadQuery, reads it back through the parser, and
checks the structured description reports the right geometry: primitive shape, dimensions,
holes, fillets, and — for assemblies — part names, colors, and placement. Tests are hermetic
(files land in tmp_path), so they need no committed fixtures."""
import cadquery as cq
import pytest

from src.step_import import read_step, describe_shape


def _write(wp_or_asm, path):
    if isinstance(wp_or_asm, cq.Assembly):
        wp_or_asm.export(str(path))
    else:
        cq.exporters.export(wp_or_asm, str(path))
    return str(path)


def test_box_primitive(tmp_path):
    p = _write(cq.Workplane("XY").box(30, 20, 10), tmp_path / "box.step")
    obj, meta = read_step(p)
    d = describe_shape(obj, meta)
    assert isinstance(obj, cq.Workplane)             # single part -> editable Workplane
    s = d["parts"][0]["solids"][0]
    assert s["primitive"] == "box"
    assert s["edge_types"] == {"Line": 12}           # unique edges (not doubled)
    assert d["summary"]["bbox"]["size"] == [30.0, 20.0, 10.0]
    assert d["summary"]["hole_count"] == 0
    assert d["summary"]["source"]["application_protocol"] in ("AP203", "AP214", "AP242")


def test_hole_and_fillet(tmp_path):
    plate = (cq.Workplane("XY").box(40, 40, 8)
             .faces(">Z").workplane().hole(10)      # Ø10 through hole
             .edges("|Z").fillet(3))                # r3 corner fillets
    p = _write(plate, tmp_path / "plate.step")
    obj, meta = read_step(p)
    d = describe_shape(obj, meta)
    feats = d["parts"][0]["solids"][0]["features"]
    assert d["summary"]["hole_count"] == 1
    assert feats["holes"][0]["diameter"] == pytest.approx(10.0, abs=1e-3)
    assert d["summary"]["round_or_fillet_count"] == 1
    assert feats["rounds_or_fillets"][0]["radius"] == pytest.approx(3.0, abs=1e-3)


def test_cylinder_body_not_mislabeled(tmp_path):
    # A plain cylinder's side wall is convex but is the body, not a fillet.
    p = _write(cq.Workplane("XY").cylinder(10, 5), tmp_path / "cyl.step")
    obj, meta = read_step(p)
    d = describe_shape(obj, meta)
    assert d["summary"]["round_or_fillet_count"] == 0
    assert d["parts"][0]["solids"][0]["primitive"] == "cylinder"


def test_assembly_names_colors_placement(tmp_path):
    a = cq.Assembly()
    a.add(cq.Workplane().box(20, 20, 20), name="base", color=cq.Color("red"))
    a.add(cq.Workplane().cylinder(10, 5), name="pin", color=cq.Color("blue"),
          loc=cq.Location((0, 0, 15)))
    p = _write(a, tmp_path / "asm.step")
    obj, meta = read_step(p)
    d = describe_shape(obj, meta)
    assert isinstance(obj, cq.Assembly)              # multi-part -> Assembly
    assert d["summary"]["part_count"] == 2
    parts = {pr["name"]: pr for pr in d["parts"]}
    assert set(parts) == {"base", "pin"}
    assert parts["base"]["color_rgb"][0] == pytest.approx(1.0)   # red
    assert parts["pin"]["color_rgb"][2] == pytest.approx(1.0)    # blue
    assert parts["pin"]["placement"]["translation"] == [0.0, 0.0, 15.0]


def test_describe_built_object_without_meta(tmp_path):
    # describe_shape must also work on an AI-built object (no STEP metadata).
    built = cq.Workplane("XY").box(10, 10, 10)
    d = describe_shape(built)
    assert d["summary"]["solid_count"] == 1
    assert d["parts"][0]["solids"][0]["primitive"] == "box"


def test_face_budget_omits_detail(tmp_path):
    p = _write(cq.Workplane("XY").box(20, 20, 20), tmp_path / "b.step")
    obj, meta = read_step(p)
    d = describe_shape(obj, meta, budget=2)           # 6 faces > budget
    s = d["parts"][0]["solids"][0]
    assert "faces" not in s and "detail_omitted" in s
    assert s["primitive"] == "box"                    # summary still present
