"""Tests for JSON-driven lambda support: {"_func"} callables powering
parametricCurve / parametricSurface, the eachpoint place-a-part form, the
sandbox, and dimension-aware property reporting.

Tool coroutines are driven with asyncio.run(); no async plugin needed."""
import asyncio
import json
import math

import pytest

from src.t2c_mcp import (
    workplane_api, resolve_value, _make_func, _safe_eval_expr,
)


def _wp(ops, **kw):
    return json.loads(asyncio.run(workplane_api(operations=ops, **kw)))


# --- parametricCurve --------------------------------------------------------

def test_parametric_curve_helix():
    """f(t) → (x,y,z): a helix of radius 10, height 20 (trig in radians)."""
    r = _wp([{"method": "parametricCurve", "params": {
        "func": {"_func": {"params": ["t"],
                           "expr": "[10*cos(2*pi*t), 10*sin(2*pi*t), 20*t]"}},
        "N": 200}}], store_as="helix")
    assert r["status"] == "success"
    bb = r["properties"]["bounding_box"]
    assert bb["xmin"] == pytest.approx(-10, abs=0.05)
    assert bb["xmax"] == pytest.approx(10, abs=0.05)
    assert bb["zmax"] == pytest.approx(20, abs=0.05)
    # A 1D wire reports length, not a phantom volume.
    assert "volume" not in r["properties"]
    assert r["properties"]["length"] > 60


def test_parametric_curve_closed_extrudes_to_solid():
    """A closed parametric profile makes a valid wire that extrudes to a solid."""
    r = _wp([
        {"method": "parametricCurve", "params": {
            "func": {"_func": {"params": ["t"],
                               "expr": "[20*cos(2*pi*t), 20*sin(2*pi*t)]"}},
            "N": 120, "makeWire": True}},
        {"method": "extrude", "args": [10]},
    ], store_as="disc")
    assert r["status"] == "success"
    # ~ pi * 20^2 * 10
    assert r["properties"]["volume"] == pytest.approx(math.pi * 400 * 10, rel=0.02)


# --- parametricSurface ------------------------------------------------------

def test_parametric_surface_sphere_area():
    """f(u,v) → (x,y,z): a sphere shell; area ~ 4*pi*r^2 (single u,v range)."""
    r = _wp([{"method": "parametricSurface", "params": {
        "func": {"_func": {"params": ["u", "v"],
                           "expr": "[15*sin(pi*v)*cos(2*pi*u), "
                                   "15*sin(pi*v)*sin(2*pi*u), 15*cos(pi*v)]"}},
        "N": 30, "start": 0, "stop": 1}}], store_as="sphere")
    assert r["status"] == "success"
    assert r["properties"]["area"] == pytest.approx(4 * math.pi * 225, rel=0.02)
    assert "volume" not in r["properties"]


# --- eachpoint (place a stored part at each stack point) ---------------------

def test_eachpoint_places_part_at_each_point():
    """eachpoint with {"_ref"} + combine replicates a stored solid on the stack."""
    _wp([{"method": "box", "args": [2, 2, 2]}], store_as="unit")
    r = _wp([
        {"method": "rarray", "args": [10, 10, 3, 2]},
        {"method": "eachpoint", "params": {"arg": {"_ref": "unit"}, "combine": True}},
    ], init_params={"plane": "XY"}, store_as="grid")
    assert r["status"] == "success"
    assert r["properties"]["volume"] == pytest.approx(6 * 8, rel=1e-3)


# --- output validation ------------------------------------------------------

@pytest.mark.parametrize("expr", ["[1, 2, 3, 4, 5]", "42", "['a', 'b', 'c']"])
def test_func_bad_return_gives_clear_error(expr):
    """A callable must return 2 or 3 numbers; anything else errors clearly."""
    r = _wp([{"method": "parametricCurve", "params": {
        "func": {"_func": {"params": ["t"], "expr": expr}}, "N": 20}}])
    assert r["status"] == "error"
    assert "_func expr" in r["error"]


# --- sandbox ----------------------------------------------------------------

def test_func_sandbox_blocks_escape():
    """simpleeval blocks attribute-access / dunder escapes in _func."""
    f = _make_func({"params": ["t"], "expr": "().__class__.__bases__"})
    with pytest.raises(Exception):
        f(0.0)


def test_expr_sandbox_blocks_escape():
    with pytest.raises(Exception):
        _safe_eval_expr("().__class__.__bases__[0].__subclasses__()")


# --- _expr regression (degrees kept after the simpleeval retrofit) ----------

def test_expr_degrees_preserved():
    assert _safe_eval_expr("cos(60)") == pytest.approx(0.5)
    assert resolve_value({"_expr": "sqrt(2)*10"}) == pytest.approx(14.1421, abs=1e-3)
