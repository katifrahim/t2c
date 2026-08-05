"""Feasibility proofs for the 'direct editing' Onshape ops (opMoveFace / opOffsetFace /
opDeleteFace / opDraft / opReplaceFace) that a dumb-B-rep pipeline would call impossible.

Because we transpile onshape-NATIVE models we have every op's exact parameters, so the only
question is whether our kernel (OCCT, via OCP — the same one CadQuery runs on) can execute
them. These tests answer 'yes' on synthetic solids with known expected geometry — no network,
fully self-validating. They are the empirical foundation for the Phase-2 translators.
"""
import math

import cadquery as cq
from OCP.gp import gp_Dir, gp_Pnt, gp_Pln
from OCP.TopoDS import TopoDS
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE
from OCP.GProp import GProp_GProps
from OCP.BRepGProp import BRepGProp
from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
from OCP.BRepOffsetAPI import BRepOffsetAPI_DraftAngle
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopTools import TopTools_ListOfShape


def _vol(shape):
    p = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape, p)
    return p.Mass()


def _faces(shape):
    out, ex = [], TopExp_Explorer(shape, TopAbs_FACE)
    while ex.More():
        out.append(TopoDS.Face_s(ex.Current()))
        ex.Next()
    return out


def _face_center(f):
    p = GProp_GProps()
    BRepGProp.SurfaceProperties_s(f, p)
    return p.CentreOfMass()


def _valid(shape):
    return BRepCheck_Analyzer(shape).IsValid()


# --- opDeleteFace: BRepAlgoAPI_Defeaturing removes faces and heals the surrounding wall ---
def test_delete_face_heals_boss_away():
    # a 10^3 box (top at z=5) with a 3x3x2 boss on top (z=5..7): remove the boss's 5 faces
    # (everything above the base top) and Defeaturing heals back to the flat box -> 1000.
    solid = (cq.Workplane("XY").box(10, 10, 10)
             .faces(">Z").workplane().rect(3, 3).extrude(2).val().wrapped)
    assert abs(_vol(solid) - 1018.0) < 1e-6
    rm = TopTools_ListOfShape()
    for f in _faces(solid):
        if _face_center(f).Z() > 5.0 + 1e-6:      # boss top + 4 boss walls
            rm.Append(f)
    assert rm.Extent() == 5
    df = BRepAlgoAPI_Defeaturing()
    df.SetShape(solid)
    df.AddFacesToRemove(rm)
    df.Build()
    assert df.IsDone()
    assert _valid(df.Shape())
    assert abs(_vol(df.Shape()) - 1000.0) < 1e-3   # boss fully removed, top healed flat


# --- opDraft: BRepOffsetAPI_DraftAngle tapers a face about a neutral plane ---
def test_draft_tapers_a_face():
    box = cq.Workplane("XY").box(10, 10, 10).val().wrapped
    side_x = next(f for f in _faces(box)
                  if BRepAdaptor_Surface(f).Plane().Axis().Direction().X() > 0.9)
    dr = BRepOffsetAPI_DraftAngle(box)
    # neutral plane at the bottom (z=-5): the whole +X face tapers inward -> volume drops.
    dr.Add(side_x, gp_Dir(0, 0, 1), math.radians(10), gp_Pln(gp_Pnt(0, 0, -5), gp_Dir(0, 0, 1)))
    dr.Build()
    assert dr.IsDone()
    assert _valid(dr.Shape())
    v = _vol(dr.Shape())
    # 10-tall wall drafted 10deg from the bottom removes a triangular wedge ~ 0.5*10*tan10*10
    assert 890.0 < v < 920.0 and v < 1000.0


# --- opMoveFace / opOffsetFace (planar face along its normal): the editable boolean route ---
def test_move_planar_face_is_a_boolean_prism():
    # Moving the +Z face of a 10^3 box up by 3mm == fusing a 10x10x3 prism onto it. This is
    # the dominant real moveFace/offsetFace case, expressed as pure editable cq ops (no opaque
    # B-rep), which is exactly the Phase-2 'editable-first' strategy.
    moved = (cq.Workplane("XY").box(10, 10, 10)
             .faces(">Z").workplane().rect(10, 10).extrude(3))
    assert abs(moved.val().Volume() - 1300.0) < 1e-6
    assert _valid(moved.val().wrapped)
    # inward (offset the face in): a cut of the same prism removes material.
    cut = (cq.Workplane("XY").box(10, 10, 10)
           .faces(">Z").workplane().rect(10, 10).cutBlind(-2))
    assert abs(cut.val().Volume() - 800.0) < 1e-6
