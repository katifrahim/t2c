"""Offline tests for the closed-loop core: oracle matching, fingerprinting, explicit
planes, and candidate generation. No network — pure logic + a couple of CadQuery solids."""
import cadquery as cq

from onshape import ir
from onshape.oracle import Body, State
from onshape.recon import (explicit_plane, fingerprint, profile_variants,
                           extrude_candidates, Engine)


def _body(v, a=100.0, c=(0, 0, 0), lo=(0, 0, 0), hi=(1, 1, 1)):
    return Body(volume=v, area=a, centroid=list(c), bbox_min=list(lo), bbox_max=list(hi))


def test_body_matches_within_tolerance():
    b = _body(1000.0)
    assert b.matches(_body(1000.0))
    assert b.matches(_body(1000.5))            # 0.05% < default 0.1% vol tol
    assert not b.matches(_body(1100.0))        # 10% off
    assert not b.matches(_body(1000.0, hi=(1, 1, 5)))  # bbox disagrees


def test_state_matches_is_order_independent():
    a = State([_body(10, lo=(0, 0, 0), hi=(1, 1, 1)), _body(20, lo=(5, 5, 5), hi=(6, 6, 6))])
    b = State([_body(20, lo=(5, 5, 5), hi=(6, 6, 6)), _body(10, lo=(0, 0, 0), hi=(1, 1, 1))])
    assert a.matches(b)
    assert not a.matches(State([_body(10, lo=(0, 0, 0), hi=(1, 1, 1))]))  # missing a body


def test_explicit_plane_keeps_direction():
    # A -Z-normal sketch at z=0: canonicalization would collapse it to "XY" (+Z) and
    # flip extrude direction; the explicit plane must preserve normal = -Z.
    mtx = [1, 0, 0, 0,
           0, 1, 0, 0,
           0, 0, -1, 0,
           0, 0, 0, 1]  # row-major; col 2 (normal) = (0,0,-1)
    p = explicit_plane(mtx)
    assert p.name is None                 # NOT canonicalized
    assert p.normal == [0, 0, -1]
    assert p.origin == [0, 0, 0]


def test_fingerprint_of_a_box():
    fp = fingerprint(cq.Workplane("XY").box(10, 10, 10))
    assert abs(fp.volume - 1000.0) < 1e-6
    assert abs(fp.area - 600.0) < 1e-6     # 6 faces * 100
    assert fp.matches(_body(1000.0, a=600.0, lo=(-5, -5, -5), hi=(5, 5, 5)))


def test_fingerprint_none_without_solid():
    assert fingerprint(cq.Workplane("XY")) is None      # empty
    assert fingerprint(cq.Workplane("XY").circle(1)) is None  # a wire, no solid


def test_profile_variants_annulus_offers_all_and_each():
    inner = ir.Profile([ir.Curve("circle", {"center": [0, 0], "radius": 5})])
    outer = ir.Profile([ir.Curve("circle", {"center": [0, 0], "radius": 10})])
    vs = profile_variants([inner, outer])
    labels = [name for name, _ in vs]
    assert labels[0] == "all-regions"      # the annulus candidate is offered first
    assert "region[0]" in labels and "region[1]" in labels


def _circle_edge(r):
    # a closed boundary edge sampled at params 0/0.5/1 (p0==p1) -> classified as a circle
    return [[r, 0, 0], [-r, 0, 0], [r, 0, 0]]


def test_caps_to_regions_groups_loops_by_face():
    from onshape.normalize import caps_to_regions
    identity = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1]
    # two annular cap faces (two concentric rings): each face carries its own 2 circles
    faces = [
        {"n": [0, 0, 1], "edges": [_circle_edge(2.45), _circle_edge(3.5)]},
        {"n": [0, 0, 1], "edges": [_circle_edge(7.0), _circle_edge(7.75)]},
    ]
    regions = caps_to_regions(faces, identity)
    assert len(regions) == 2                 # grouped by face, not flattened to 4 circles
    assert all(len(loops) == 2 for loops in regions)   # each region = outer + hole loop
    # a side wall (normal perpendicular to extrude dir) is ignored
    assert caps_to_regions(faces + [{"n": [1, 0, 0], "edges": [_circle_edge(1.0)]}],
                           identity) == regions


def test_extrude_candidates_search_space():
    # a NEW extrude on an empty world: region variants x sign/symmetric, all 'new'
    eng = Engine()
    profs = [ir.Profile([ir.Curve("circle", {"center": [0, 0], "radius": 10})])]
    cands = extrude_candidates(eng, ir.Plane(name="XY"), profs, depth=2.0, op="new",
                               base_live=[])
    labels = {c.label for c in cands}
    assert "all-regions|+2.0|new" in labels
    assert "all-regions|-2.0|new" in labels      # the sign the oracle will choose from
    assert all(c.label.endswith("new") for c in cands)
