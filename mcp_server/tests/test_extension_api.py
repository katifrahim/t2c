"""Self-validating tests for the extension_api plugin layer (gears + mechanical
parts). Importing the server installs the plugins + copy dispatcher; these tests
prove every family builds real geometry, the tool ops work, the fastener-hole
methods work in workplane_api, and core behaviour is unaffected.

No async plugin needed — tool coroutines are driven with asyncio.run()."""
import asyncio
import json

import cadquery as cq
import pytest

from src.t2c_mcp import (
    EXT_AVAILABLE, _EXT_CATALOG, _build_ext_part,
    extension_api, workplane_api,
)

pytestmark = pytest.mark.skipif(not EXT_AVAILABLE, reason="extension plugins not installed")


def _geom_measure(obj):
    """A positive size proxy for any built part (volume / assembly children)."""
    if isinstance(obj, cq.Assembly):
        return len(obj.children)
    if isinstance(obj, cq.Workplane):
        return obj.val().Volume()
    if hasattr(obj, "Volume"):
        return obj.Volume()
    if hasattr(obj, "cq_object"):
        return _geom_measure(obj.cq_object)
    raise AssertionError(f"un-measurable result {type(obj).__name__}")


# One representative, known-good part per family.
REPRESENTATIVES = {
    "gear":     ("SpurGear", {"module": 1, "teeth_number": 20, "width": 5, "bore_d": 5}),
    "fastener": ("SocketHeadCapScrew", {"size": "M3-0.5", "fastener_type": "iso4762", "length": 16, "simple": True}),
    "bearing":  ("SingleRowDeepGrooveBallBearing", {"size": "M8-22-7", "bearing_type": "SKT"}),
    "thread":   ("IsoThread", {"major_diameter": 6, "pitch": 1, "length": 8, "external": True}),
    "sprocket": ("Sprocket", {"num_teeth": 16, "chain_pitch": 12.7, "roller_diameter": 7.75, "clearance": 0.1, "thickness": 3}),
    "chain":    ("Chain", {"spkt_teeth": [16, 16], "positive_chain_wrap": [True, True],
                           "spkt_locations": [(0, 0, 0), (60, 0, 0)], "chain_pitch": 12.7, "roller_diameter": 7.75}),
}


def test_catalog_covers_all_families():
    families = {e["family"] for e in _EXT_CATALOG.values()}
    assert families >= {"gear", "fastener", "bearing", "thread", "sprocket", "chain"}
    assert len(_EXT_CATALOG) >= 40


@pytest.mark.parametrize("family", list(REPRESENTATIVES))
def test_build_every_family(family):
    part, params = REPRESENTATIVES[family]
    obj = _build_ext_part({"_type": part, "params": params})
    assert _geom_measure(obj) > 0


# Every gear type builds valid geometry through the plugin's Workplane.gear() builder.
GEAR_CASES = {
    "SpurGear": {"module": 1, "teeth_number": 19, "width": 5, "bore_d": 5},
    "HerringboneGear": {"module": 1, "teeth_number": 19, "width": 5, "helix_angle": 20, "bore_d": 5},
    "RingGear": {"module": 1, "teeth_number": 24, "width": 5, "rim_width": 3},
    "BevelGear": {"module": 1, "teeth_number": 19, "cone_angle": 45, "face_width": 4},
    "RackGear": {"module": 1, "length": 30, "width": 5, "height": 6},
    "Worm": {"module": 1, "lead_angle": 5, "length": 20, "bore_d": 4, "n_threads": 1},
    "PlanetaryGearset": {"module": 1, "sun_teeth_number": 12, "planet_teeth_number": 9,
                         "width": 5, "rim_width": 3, "n_planets": 3},
}


@pytest.mark.parametrize("gear,params", list(GEAR_CASES.items()))
def test_all_gear_types(gear, params):
    obj = _build_ext_part({"_type": gear, "params": params})
    assert obj.val().Volume() > 0


def test_helical_is_spur_with_helix_angle():
    """No separate HelicalGear class — a helix_angle turns a spur gear helical."""
    obj = _build_ext_part({"_type": "SpurGear",
                           "params": {"module": 1, "teeth_number": 19, "width": 5, "helix_angle": 20, "bore_d": 5}})
    assert obj.val().Volume() > 0


def _call(**kw):
    return json.loads(asyncio.run(extension_api(**kw)))


def test_tool_list_and_options():
    listed = _call(op="list")
    assert listed["status"] == "success"
    assert set(listed["parts_by_family"]) >= {"gear", "fastener", "bearing", "thread"}

    opts = _call(op="options", part="SocketHeadCapScrew")
    assert opts["status"] == "success"
    # sizes/types are read live from the part's own tables, not hardcoded
    assert "iso4762" in opts["standard_types"]
    assert any("size" in p for p in opts["params"])


def test_tool_build_stores_and_returns():
    r = _call(op="build", part="SpurGear",
              params={"module": 1, "teeth_number": 20, "width": 6, "bore_d": 5}, store_as="pinion")
    assert r["status"] == "success"
    assert r["name"] == "pinion"
    assert r["properties"]["volume"] > 0


def test_tool_rejects_unknown_part():
    r = _call(op="build", part="NotARealPart", params={})
    assert r["status"] == "error"


def test_fastener_hole_in_workplane():
    """A built fastener drives a matching clearance hole in workplane_api via {_ref}."""
    _call(op="build", part="SocketHeadCapScrew",
          params={"size": "M3-0.5", "fastener_type": "iso4762", "length": 16, "simple": True}, store_as="screw1")
    plain = cq.Workplane("XY").box(20, 20, 5).val().Volume()
    r = json.loads(asyncio.run(workplane_api(
        init_params={"plane": "XY"},
        operations=[
            {"method": "box", "args": [20, 20, 5]},
            {"method": "faces", "args": [">Z"]}, {"method": "workplane"},
            {"method": "clearanceHole", "params": {"fastener": {"_ref": "screw1"}, "counterSunk": False}},
        ], store_as="plate")))
    assert r["status"] == "success"
    assert 0 < r["properties"]["volume"] < plain   # a hole was removed


def test_threaded_hole_real_thread():
    """The copy dispatcher lets the real-thread threadedHole path work (regression)."""
    _call(op="build", part="SocketHeadCapScrew",
          params={"size": "M3-0.5", "fastener_type": "iso4762", "length": 16, "simple": True}, store_as="screw2")
    r = json.loads(asyncio.run(workplane_api(
        init_params={"plane": "XY"},
        operations=[
            {"method": "box", "args": [20, 20, 10]},
            {"method": "faces", "args": [">Z"]}, {"method": "workplane"},
            {"method": "threadedHole",
             "params": {"fastener": {"_ref": "screw2"}, "depth": 6, "simple": False, "counterSunk": False}},
        ], store_as="tapped")))
    assert r["status"] == "success"
    assert r["properties"]["volume"] > 0


def test_core_behaviour_unaffected():
    """Base-shape ops must behave exactly as stock despite the loaded plugins."""
    v = cq.Workplane("XY").box(20, 20, 20).edges("|Z").fillet(2).faces(">Z").shell(-1.5).val().Volume()
    assert round(v, 2) == 2588.80
