"""Self-validating tests for the STEP parser (src/step_import.py).

Each test generates its own STEP file with CadQuery, reads it back through the parser, and
checks the structured description reports the right geometry: primitive shape, dimensions,
holes, fillets, and — for assemblies — part names, colors, and placement. Tests are hermetic
(files land in tmp_path), so they need no committed fixtures."""
import cadquery as cq
import pytest

from src.step_import import read_step, describe_shape, _read_header


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


# --- MCP integration: /import route + inspect_model tool ---------------------
import asyncio
import json

from src.t2c_mcp import (
    _import, _export, _session_adopt, _get_session,
    inspect_model, workplane_api, _bind,
)


class _FakeReq:
    """Minimal Starlette-request stand-in for the /import and /export handlers."""
    def __init__(self, body: bytes = b"", query: dict | None = None):
        self.headers = {}
        self.query_params = query or {}   # no session -> local session (also used by the tools)
        self._body = body

    async def body(self):
        return self._body


def test_import_route_edits_and_inspects(tmp_path):
    p = _write(cq.Workplane("XY").box(30, 20, 10), tmp_path / "up.step")
    resp = asyncio.run(_import(_FakeReq(open(p, "rb").read())))
    data = json.loads(resp.body)
    assert data["status"] == "ok" and data["obj_type"] == "Workplane"
    assert data["converted_to_ap242"] is True    # AP214 upload normalised to AP242
    assert data["description"]["parts"][0]["solids"][0]["primitive"] == "box"

    # Edit the imported model in place, then inspect the result.
    name = data["name"]
    r = json.loads(asyncio.run(workplane_api(
        operations=[{"method": "edges", "args": ["|Z"]},
                    {"method": "fillet", "args": [2]}],
        start_from=name, store_as="edited")))
    assert r["status"] == "success"
    ins = json.loads(asyncio.run(inspect_model(name="edited")))
    assert ins["description"]["summary"]["round_or_fillet_count"] == 1


def test_import_route_rejects_empty_body():
    resp = asyncio.run(_import(_FakeReq(b"")))
    assert resp.status_code == 400


def test_import_route_rejects_unsupported_format():
    # A non-STEP payload (no ISO-10303-21 marker) must be refused with 415.
    resp = asyncio.run(_import(_FakeReq(b"%PDF-1.7 not a step file")))
    assert resp.status_code == 415
    assert ".step" in json.loads(resp.body)["supported"][0]


def test_import_then_adopt_moves_model_to_chat_id(tmp_path):
    # Import into a temporary local session (a brand-new, unsaved chat)…
    p = _write(cq.Workplane("XY").box(12, 8, 4), tmp_path / "a.step")
    resp = asyncio.run(_import(_FakeReq(open(p, "rb").read(),
                                        query={"session": "__LOCALID_x", "name": "m"})))
    assert json.loads(resp.body)["status"] == "ok"
    assert "m" in _get_session("__LOCALID_x").state

    # …first message promotes the chat: the model must move to the persistent chat id,
    # so the AI (whose tools run under the chat id) can still see and edit it.
    ad = asyncio.run(_session_adopt(_FakeReq(query={"from": "__LOCALID_x", "to": "chat_1"})))
    assert json.loads(ad.body)["status"] == "ok"
    assert "m" in _get_session("chat_1").state
    assert not _get_session("__LOCALID_x").state          # local session released
    assert _get_session("chat_1").current == "m"

    # Idempotent: re-adopting an emptied local session is a no-op.
    ad2 = asyncio.run(_session_adopt(_FakeReq(query={"from": "__LOCALID_x", "to": "chat_1"})))
    assert json.loads(ad2.body)["status"] == "noop"


def test_export_step_is_ap242(tmp_path):
    _bind("exp")
    asyncio.run(workplane_api(operations=[{"method": "box", "args": [10, 10, 10]}],
                              store_as="m"))
    resp = asyncio.run(_export(_FakeReq(query={"fmt": "step"})))
    assert "AP242_MANAGED" in open(resp.path).read()   # exported as AP242


# --- PMI / header ------------------------------------------------------------
def test_pmi_absent_is_graceful(tmp_path):
    # A plain part carries no PMI; the reader must return empty without error.
    p = _write(cq.Workplane("XY").box(10, 10, 10), tmp_path / "np.step")
    obj, meta = read_step(p)
    assert meta["pmi"] == {}
    assert "pmi" not in describe_shape(obj, meta)


import os

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def test_real_world_onshape_ap242():
    # A genuine third-party AP242 file (exported from Onshape, written by ST-Developer, not
    # OCCT) — proves the parser handles real CAD, not just CadQuery-generated geometry.
    obj, meta = read_step(os.path.join(FIXTURES, "onshape_ap242.step"))
    d = describe_shape(obj, meta)
    assert meta["header"]["application_protocol"] == "AP242"
    s = d["parts"][0]["solids"][0]
    assert "Cylinder" in s["face_types"] and "Plane" in s["face_types"]
    assert d["summary"]["hole_count"] >= 1          # has through holes
    assert meta["pmi"] == {}                          # this model carries no semantic PMI


def test_ap242_schema_detected(tmp_path):
    # AP242's MIM part-number is 10303-442; it must not be misread as the AP.
    f = tmp_path / "h.stp"
    f.write_text(
        "ISO-10303-21;\nHEADER;\nFILE_NAME('x','',(''),(''),'','','');\n"
        "FILE_SCHEMA(('AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF "
        "{ 1 0 10303 442 1 1 4 }'));\nENDSEC;\n")
    assert _read_header(str(f))["application_protocol"] == "AP242"
