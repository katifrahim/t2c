"""
CadQuery Specialized MCP Server.
Tools: workplane_api, sketch_api, assembly_api, query_docs
"""
# MCP server entry point

from typing import Any, Dict, List, Optional
import inspect
import json
import math
import os
import traceback
import sys

try:
    from ocp_vscode import show, set_port
    set_port(3939)
    OCP_VIEWER_AVAILABLE = True
except ImportError:
    OCP_VIEWER_AVAILABLE = False
    show = None

from mcp.server.fastmcp import FastMCP

import cadquery as cq
from cadquery import (
    Workplane, Sketch, Assembly,
    Vector, Plane, Location, Matrix,
    Vertex, Edge, Wire, Face, Shell, Solid, Compound,
    Color,
)
from cadquery.selectors import (
    NearestToPointSelector, BoxSelector,
    ParallelDirSelector, DirectionSelector, PerpendicularDirSelector,
    TypeSelector, RadiusNthSelector, CenterNthSelector,
    DirectionMinMaxSelector, DirectionNthSelector,
    LengthNthSelector, AreaNthSelector,
    AndSelector, SumSelector, SubtractSelector, InverseSelector,
    StringSyntaxSelector,
)

# =============================================================================
# SERVER
# =============================================================================

mcp = FastMCP(
    name="cadquery_specialized_mcp",
    instructions=(
        "Three dedicated CadQuery tools:\n"
        "  • workplane_api  — 3D modeling via cq.Workplane method chaining\n"
        "  • sketch_api     — 2D profiles via cq.Sketch (face or edge workflows)\n"
        "  • assembly_api   — multi-part assemblies via cq.Assembly add/constrain/solve\n"
        "  • query_docs     — fetch official detailed docs of specific methods and their parameters\n\n"
        "All tools share a persistent object store. Reference stored objects with "
        "{\"_ref\": \"name\"} and construct CadQuery types inline with "
        "{\"_type\": \"Vector\"|\"Plane\"|\"Location\"|\"Color\", ...}.\n\n"
        "Typical workflow:\n"
        "  1. workplane_api → create and store parts\n"
        "  2. sketch_api    → create 2D profiles, use in workplane_api via placeSketch\n"
        "  3. assembly_api  → combine stored parts with constraints"
        "  4. query_docs    → fetch official detailed docs for specific methods causing error or confusion"
    ),
)

DEBUG_MODE = True

# =============================================================================
# STATE  (name → CadQuery object)
# =============================================================================

_state: Dict[str, Any] = {}
_counters: Dict[str, int] = {}
_current: Optional[str] = None


def _store(name: str, obj: Any) -> None:
    global _current
    _state[name] = obj
    _current = name


def _get(name: Optional[str]) -> Any:
    target = name or _current
    if not target:
        raise ValueError("No object name given and no current object set")
    if target not in _state:
        raise ValueError(f"Object '{target}' not found")
    return _state[target]


def _auto_name(prefix: str) -> str:
    _counters[prefix] = _counters.get(prefix, 0) + 1
    return f"{prefix}_{_counters[prefix]}"

# =============================================================================
# REFERENCE & TYPE RESOLUTION
# =============================================================================

# Degree-based trig namespace for {"_expr": "..."} evaluation.
# All angle arguments are in degrees; inverse trig returns degrees.
_MATH_NS: Dict[str, Any] = {
    "cos":   lambda x: math.cos(math.radians(x)),
    "sin":   lambda x: math.sin(math.radians(x)),
    "tan":   lambda x: math.tan(math.radians(x)),
    "acos":  lambda x: math.degrees(math.acos(x)),
    "asin":  lambda x: math.degrees(math.asin(x)),
    "atan":  lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "sqrt":  math.sqrt,
    "pi":    math.pi,
    "e":     math.e,
    "abs":   abs,
    "pow":   pow,
    "ceil":  math.ceil,
    "floor": math.floor,
}


def resolve_value(value: Any) -> Any:
    """Resolve {"_ref": name} → stored object, {"_type": ...} → CadQuery type,
    {"_attr": ...} → attribute access, {"_call": ...} → method call,
    {"_run_on": ...} → run ops on object without storing."""
    if isinstance(value, dict):
        if "_ref" in value:
            return _get(value["_ref"])
        elif "_type" in value:
            return _construct_type(value)
        elif "_attr" in value:
            spec = value["_attr"]
            obj  = resolve_value(spec["obj"])
            return getattr(obj, spec["name"])
        elif "_call" in value:
            spec   = value["_call"]
            obj    = resolve_value(spec["obj"])
            method = getattr(obj, spec["method"])
            if "args" in spec:
                return method(*resolve_value(spec["args"]))
            elif "params" in spec:
                return method(**resolve_value(spec["params"]))
            else:
                return method()
        elif "_run_on" in value:
            spec = value["_run_on"]
            obj  = resolve_value(spec["obj"])
            return _run(obj, spec["ops"])
        elif "_expr" in value:
            return float(eval(value["_expr"], {"__builtins__": {}}, _MATH_NS))
        else:
            return {k: resolve_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_value(v) for v in value]
    return value


def _construct_type(spec: dict) -> Any:
    t = spec["_type"]

    # ── Vector ────────────────────────────────────────────────────────────────
    if t == "Vector":
        return Vector(
            resolve_value(spec.get("x", 0)),
            resolve_value(spec.get("y", 0)),
            resolve_value(spec.get("z", 0)),
        )

    # ── Plane ────────────────────────────────────────────────────────────────
    elif t == "Plane":
        if "name" in spec:
            return Plane.named(spec["name"])
        origin = spec.get("origin", (0, 0, 0))
        xDir   = spec.get("xDir",   (1, 0, 0))
        normal = spec.get("normal", (0, 0, 1))
        if isinstance(origin, list): origin = tuple(origin)
        if isinstance(xDir,   list): xDir   = tuple(xDir)
        if isinstance(normal, list): normal = tuple(normal)
        return Plane(origin=origin, xDir=xDir, normal=normal)

    # ── Location ────────────────────────────────────────────────────────────────
    elif t == "Location":
        if "plane" in spec:
            plane = resolve_value(spec["plane"])
            return Location(plane, resolve_value(spec["vector"])) if "vector" in spec else Location(plane)
        elif "vector" in spec:
            return Location(resolve_value(spec["vector"]))
        return Location(
            (spec.get("x", 0), spec.get("y", 0), spec.get("z", 0)),
            (spec.get("rx", 0), spec.get("ry", 0), spec.get("rz", 0)),
        )

    # ── Color ────────────────────────────────────────────────────────────────
    elif t == "Color":
        if "name" in spec:
            return Color(spec["name"])
        r, g, b, a = spec.get("r", 0), spec.get("g", 0), spec.get("b", 0), spec.get("a", 1)
        # Accept either 0–255 integers or 0.0–1.0 floats; normalise the former.
        if max(r, g, b) > 1:
            r, g, b = r / 255.0, g / 255.0, b / 255.0
        return Color(r, g, b, a)

    # ── Matrix ────────────────────────────────────────────────────────────────
    elif t == "Matrix":
        values = spec.get("values")
        return Matrix(values) if values else Matrix()

    # ── Vertex ────────────────────────────────────────────────────────────────
    elif t == "Vertex":
        if "point" in spec:
            vec = resolve_value(spec["point"])
            return Vertex.makeVertex(*vec.toTuple())
        return Vertex.makeVertex(spec.get("x", 0), spec.get("y", 0), spec.get("z", 0))

    # ── Direction-based selectors ─────────────────────────────────────────────
    # Example: {"_type": "ParallelDirSelector",      "vector": {"_type": "Vector", ...}}
    elif t == "ParallelDirSelector":
        return ParallelDirSelector(resolve_value(spec["vector"]))
    elif t == "DirectionSelector":
        return DirectionSelector(resolve_value(spec["vector"]))
    elif t == "PerpendicularDirSelector":
        return PerpendicularDirSelector(resolve_value(spec["vector"]))

    # ── Point / box selectors ─────────────────────────────────────────────────
    # Example: {"_type": "BoxSelector", "point0": [x,y,z], "point1": [x,y,z], "boundingbox": true}
    elif t == "NearestToPointSelector":
        pnt = resolve_value(spec["pnt"])
        return NearestToPointSelector(pnt)
    elif t == "BoxSelector":
        p0 = resolve_value(spec["point0"])
        p1 = resolve_value(spec["point1"])
        return BoxSelector(p0, p1, spec.get("boundingbox", True))

    # ── Type selector ─────────────────────────────────────────────────────────
    # Example: {"_type": "TypeSelector", "typeString": "CIRCLE"}
    # typeString face options: PLANE, CYLINDER, CONE, SPHERE, TORUS, BEZIER, BSPLINE, REVOLUTION, EXTRUSION, OFFSET, OTHER
    # typeString edge options: LINE, CIRCLE, ELLIPSE, HYPERBOLA, PARABOLA, BEZIER, BSPLINE, OFFSET, OTHER
    elif t == "TypeSelector":
        return TypeSelector(spec["typeString"])

    # ── Nth-by-property selectors ─────────────────────────────────────────────
    # Example: {"_type": "DirectionMinMaxSelector", "vector": {...}, "directionMax": true}
    elif t == "RadiusNthSelector":
        return RadiusNthSelector(spec["n"], spec.get("directionMax", True), spec.get("tolerance", 1e-6))
    elif t == "LengthNthSelector":
        return LengthNthSelector(spec["n"], spec.get("directionMax", True), spec.get("tolerance", 1e-6))
    elif t == "AreaNthSelector":
        return AreaNthSelector(spec["n"], spec.get("directionMax", True), spec.get("tolerance", 1e-6))
    elif t == "CenterNthSelector":
        return CenterNthSelector(resolve_value(spec["vector"]), spec["n"],
                                  spec.get("directionMax", True), spec.get("tolerance", 1e-6))
    elif t == "DirectionNthSelector":
        return DirectionNthSelector(resolve_value(spec["vector"]), spec["n"],
                                     spec.get("directionMax", True), spec.get("tolerance", 1e-6))
    elif t == "DirectionMinMaxSelector":
        return DirectionMinMaxSelector(resolve_value(spec["vector"]),
                                        spec.get("directionMax", True), spec.get("tolerance", 1e-6))

    # ── String syntax selector ────────────────────────────────────────────────
    # Example: {"_type": "StringSyntaxSelector", "selectorString": ">Z"}
    elif t == "StringSyntaxSelector":
        return StringSyntaxSelector(spec["selectorString"])

    # ── Compound selectors (recursive — left/right are also resolved) ─────────
    # Example: {"_type": "AndSelector",      "left": {...}, "right": {...}}
    elif t == "AndSelector":
        return AndSelector(resolve_value(spec["left"]), resolve_value(spec["right"]))
    elif t == "SumSelector":
        return SumSelector(resolve_value(spec["left"]), resolve_value(spec["right"]))
    elif t == "SubtractSelector":
        return SubtractSelector(resolve_value(spec["left"]), resolve_value(spec["right"]))
    elif t == "InverseSelector":
        return InverseSelector(resolve_value(spec["selector"]))

    raise ValueError(f"Unknown _type: '{t}'")

# =============================================================================
# HELPERS
# =============================================================================

def _error(msg: str, tb: str = None) -> str:
    r = {"status": "error", "error": msg}
    if DEBUG_MODE and tb:
        r["traceback"] = tb
    return json.dumps(r)


def _show(obj: Any) -> None:
    if not OCP_VIEWER_AVAILABLE:
        return
    old, sys.stdout = sys.stdout, sys.stderr
    try:
        if isinstance(obj, Assembly):
            show((obj,))
        elif isinstance(obj, Sketch):
            show(Workplane().placeSketch(obj))
        elif hasattr(obj, "wrapped"):
            show(obj)
        else:
            show(obj)
    except Exception:
        pass
    finally:
        sys.stdout = old


def _properties(obj: Any) -> dict:
    props = {}

    if isinstance(obj, Assembly):
        if hasattr(obj, "children"):
            props["children"] = [c.name for c in obj.children]
        if hasattr(obj, "objects"):
            props["object_count"] = len(obj.objects)
        try:
            c = obj.toCompound()
            props["volume"] = c.Volume()
            ctr = c.Center()
            props["center"] = [ctr.x, ctr.y, ctr.z]
        except Exception:
            pass
        return props

    if isinstance(obj, Sketch):
        if hasattr(obj, "_faces"):
            try:
                faces = obj._faces.Faces()
                props["face_count"] = len(faces)
                props["total_area"] = sum(f.Area() for f in faces)
            except Exception:
                pass
        if hasattr(obj, "_edges"):
            try:
                props["edge_count"] = len(obj._edges)
            except Exception:
                pass
        return props

    try:
        shape = obj.val() if hasattr(obj, "val") else obj
        for attr, key in [("Volume", "volume"), ("Area", "area")]:
            if hasattr(shape, attr):
                try: props[key] = getattr(shape, attr)()
                except Exception: pass
        if hasattr(shape, "Center"):
            try:
                c = shape.Center()
                props["center"] = [c.x, c.y, c.z]
            except Exception: pass
        if hasattr(shape, "BoundingBox"):
            try:
                bb = shape.BoundingBox()
                props["bounding_box"] = {
                    "xmin": bb.xmin, "xmax": bb.xmax,
                    "ymin": bb.ymin, "ymax": bb.ymax,
                    "zmin": bb.zmin, "zmax": bb.zmax,
                }
            except Exception: pass
    except Exception:
        pass
    return props


def _obj_type(obj: Any) -> str:
    for cls, name in [
        (Workplane, "Workplane"), (Sketch, "Sketch"), (Assembly, "Assembly"),
        (Solid, "Solid"), (Compound, "Compound"), (Face, "Face"), (Wire, "Wire"),
        (Edge, "Edge"), (Vertex, "Vertex"), (Shell, "Shell"),
        (Vector, "Vector"), (Plane, "Plane"), (Location, "Location"), (Matrix, "Matrix"),
    ]:
        if isinstance(obj, cls):
            return name
    return type(obj).__name__


def _run(obj: Any, operations: List[dict]) -> Any:
    """Apply a list of operations via method chaining."""
    for op in operations:
        method_name = op.get("method")
        if not method_name:
            raise ValueError("Each operation must have a 'method' key")
        if not hasattr(obj, method_name):
            raise AttributeError(f"'{type(obj).__name__}' has no method '{method_name}'")

        method = getattr(obj, method_name)
        raw_args   = op.get("args")    # list → positional call
        raw_params = op.get("params", {})  # dict → keyword call
        raw_kwargs = op.get("kwargs") or {}  # dict → extra keyword args alongside positional args

        if raw_args is not None:
            resolved_args = resolve_value(raw_args)
            resolved_kwargs = resolve_value(raw_kwargs)
            # Assembly.constrain fixed constraints (3-arg form: query, kind, param) require
            # param to be a Python tuple. JSON arrays resolve to lists; Vector needs .toTuple().
            if (method_name == "constrain"
                    and len(resolved_args) == 3
                    and not isinstance(resolved_args[2], str)):
                p = resolved_args[2]
                if isinstance(p, list):
                    resolved_args[2] = tuple(p)
                elif isinstance(p, Vector):
                    resolved_args[2] = p.toTuple()
            obj = method(*resolved_args, **resolved_kwargs)
        elif raw_params:
            obj = method(**resolve_value(raw_params))
        else:
            obj = method()
    return obj

# =============================================================================
# TOOL 1 — workplane_api
# =============================================================================

@mcp.tool(name="workplane_api")
async def workplane_api(
    operations: List[dict],
    init_params: Optional[dict] = None,
    start_from: Optional[str] = None,
    store_as: Optional[str] = None,
) -> str:
    """
    Build 3D models using CadQuery's Workplane fluent API via method chaining.
    
    Underlying 3D modelling engine/ kernel: Open CASCADE Technology (OCCT)
    Modelling method: Boundary Representation (BRep)

    ── COORDINATE SYSTEM & VIEWER ──────────────────────────────────────────────
    CadQuery uses a right-handed XYZ coordinate system:
      X → points right                     (red axis in OCP viewer)
      Y → points into or out of the screen (green axis)
      Z → points up                        (blue axis)

    The OCP viewer (port 3939) renders accordingly:
      • Displays objects to the USER in a 3D GLOBAL coordinate system
      • Default/ Isometric view: +Z is upward, +X is rightward, Y+ is inward, XY plane is the floor
      • User can use their mouse to view the object from any angle/side 
      • Axis colours: X=red, Y=green, Z=blue.

    How named workplanes relate to the viewer (global coordinate system):
      front (XY)  — looks along -Z; extrudes +Z
      back        — looks along +Z; extrudes -Z
      right       — looks along -X; extrudes +X
      left (ZY)   — looks along +X; extrudes -X
      top         — looks along -Y; extrudes +Y
      bottom (XZ) — looks along +Y; extrudes -Y

    ── INIT ────────────────────────────────────────────────────────────────────
    init_params controls the Workplane constructor. Omit for default XY plane. 
        
      These workplane types define the LOCAL coordinate system for the operations that will follow.

      Named plane:
        {"plane": "XY"}
        # All valid names: "XY", "YZ", "ZX", "XZ", "YX", "ZY",
        #                  "front", "back", "left", "right", "top", "bottom"

      Custom Plane:
        {"plane": {"_type": "Plane", "origin": [0,0,10], "xDir": [1,0,0], "normal": [0,0,1]}}

      Correct xDir, normal and derived yDir for each named/ axis-aligned plane:
        XY     → xDir:[1,0,0]    normal:[0,0,1]    yDir:[0,1,0]  (extrudes +Z, same as front)
        YZ     → xDir:[0,1,0]    normal:[1,0,0]    yDir:[0,0,1]  (extrudes +X)
        ZX     → xDir:[0,0,1]    normal:[0,1,0]    yDir:[1,0,0]  (extrudes +Y)
        XZ     → xDir:[1,0,0]    normal:[0,-1,0]   yDir:[0,0,1]  (extrudes -Y, same as bottom)
        YX     → xDir:[0,1,0]    normal:[0,0,-1]   yDir:[1,0,0]  (extrudes -Z)
        ZY     → xDir:[0,0,1]    normal:[-1,0,0]   yDir:[0,1,0]  (extrudes -X, same as left)
        front  → xDir:[1,0,0]    normal:[0,0,1]    yDir:[0,1,0]  (extrudes +Z, same as XY) OK
        back   → xDir:[-1,0,0]   normal:[0,0,-1]   yDir:[0,1,0]  (extrudes -Z) OK
        right  → xDir:[0,0,-1]   normal:[1,0,0]    yDir:[0,1,0]  (extrudes +X) OK
        left   → xDir:[0,0,1]    normal:[-1,0,0]   yDir:[0,1,0]  (extrudes -X, same as ZY) OK
        top    → xDir:[1,0,0]    normal:[0,1,0]    yDir:[0,0,-1] (extrudes +Y) OK
        bottom → xDir:[1,0,0]    normal:[0,-1,0]   yDir:[0,0,1]  (extrudes -Y, same as XZ)
        
      Note: These xDir, normal and yDir values for each plane map the relationship between the local coordinate system (what you are working with) and the global coordinate system (what the user is seeing in the viewer)

      When chaining .faces(">Z").workplane(), the new plane's normal points outward from that face, so extrude, cutBlind, etc go in the expected direction automatically.

    start_from: name of a stored Workplane to continue chaining from.
    store_as:   name under which to store the result (auto-generated if omitted).

    ── OPERATIONS ──────────────────────────────────────────────────────────────
    Each operation: {"method": str, "params": dict}  (keyword args)
                or  {"method": str, "args": list}    (positional args)
    Results are chained — each call's output becomes the next call's receiver.

    combine values (applies to all methods below that accept combine):
      True / 'a'   → fuse result into existing solid on the stack (default)
      False        → return new solid(s) without fusing
      'cut' / 's'  → subtract result from existing solid

    2D Operations
      # profiles:
        rect(xLen: float, yLen: float, centered: Union[bool, Tuple[bool, bool]]=True, forConstruction: bool=False)
	        - Make a rectangle for each item on the stack. 
        circle(radius: float, forConstruction: bool=False)
	        - Make a circle for each item on the stack.
        ellipse(x_radius: float, y_radius: float, rotation_angle: float=0.0, forConstruction: bool=False)
	        - Make an ellipse for each item on the stack.
        polygon(nSides: int, diameter: float, forConstruction: bool=False, circumscribed: bool=False)
	        - Make a polygon for each item on the stack.
        slot2D(length: float, diameter: float, angle: float=0)
	        - Creates a rounded slot for each point on the stack.
      # lines:
        line(xDist: float, yDist: float, forConstruction: bool=False)
	        - Make a line from the current point to the provided point, using dimensions relative to the current point
        lineTo(x: float, y: float, forConstruction: bool=False)
	        - Make a line from the current point to the provided x y coordinate
        vLine(distance: float, forConstruction: bool=False)
	        - Make a vertical line from the current point to the provided distance
        vLineTo(yCoord: float, forConstruction: bool=False)
	        - Make a vertical line from the current point to the provided y coordinate.
        hLine(distance: float, forConstruction: bool=False)
	        - Make a horizontal line from the current point to the provided distance
        hLineTo(xCoord: float, forConstruction: bool=False)
	        - Make a horizontal line from the current point to the provided x coordinate.
        polarLine(distance: float, angle: float, forConstruction: bool=False)
	        - Make a line of the given length, at the given angle from the current point
        polarLineTo(distance: float, angle: float, forConstruction: bool=False)
	        - Make a line from the current point to the given polar coordinates
        spline(listOfXYTuple: Iterable[Union[Tuple[float, float], Tuple[float, float, float], Vector]], tangents: Optional[Sequence[Union[Tuple[float, float], Tuple[float, float, float], Vector]]]=None, periodic: bool=False, parameters: Optional[Sequence[float]]=None, scale: bool=True, tol: float | None=None, forConstruction: bool=False, includeCurrent: bool=False, makeWire: bool=False)
	        - Create a spline interpolated through the provided points (2D or 3D).
            - "tangents" are vectors, controlling the direction the curve is heading at interpolation points.
        polyline(listOfXYTuple: Sequence[Union[Tuple[float, float], Tuple[float, float, float], Vector]], forConstruction: bool=False, includeCurrent: bool=False)
	        - Create a polyline from a list of points (2D or 3D).
            - polyline: series of multiple interconnected line segments
      # arcs/ curves:
        bezier(listOfXYTuple: Iterable[Union[Tuple[float, float], Tuple[float, float, float], Vector]], forConstruction: bool=False, includeCurrent: bool=False, makeWire: bool=False)
	        - Make a cubic Bézier curve by the provided points (2D or 3D). 
        tangentArcPoint(endpoint: Union[Tuple[float, float], Tuple[float, float, float], Vector], forConstruction: bool=False, relative: bool=True)
	        - Draw an arc as a tangent from the end of the current edge to endpoint.  
        sagittaArc(endPoint: Union[Tuple[float, float], Tuple[float, float, float], Vector], sag: float, forConstruction: bool=False)
	        - Draw an arc from the current point to endPoint with an arc defined by the sag (sagitta).
            - sag: distance from the arc center to the arc base
        radiusArc(endPoint: Union[Tuple[float, float], Tuple[float, float, float], Vector], radius: float, forConstruction: bool=False)
	        - Draw an arc from the current point to endPoint with an arc defined by the radius.  
        threePointArc(point1: Union[Tuple[float, float], Tuple[float, float, float], Vector], point2: Union[Tuple[float, float], Tuple[float, float, float], Vector], forConstruction: bool=False)
	        - Draw an arc from the current point, through point1, and ending at point2
        ellipseArc(x_radius: float, y_radius: float, angle1: float=360, angle2: float=360, rotation_angle: float=0.0, sense: Literal[-1, 1]=1, forConstruction: bool=False, startAtCurrent: bool=True, makeWire: bool=False)
	        - Draw an elliptical arc with x and y radiuses either with start point at current point or current point being the center of the arc. 
            - angle1 is starting angle, angle2 is ending angle.
        parametricCurve(func: Callable[[float], Union[Tuple[float, float], Tuple[float, float, float], Vector]], N: int=400, start: float=0, stop: float=1, tol: float=1e-06, minDeg: int=1, maxDeg: int=6, smoothing: Optional[Tuple[float, float, float]]=(1, 1, 1), makeWire: bool=True)
	        - Create a spline curve approximating the provided function of one independent variable. 
            - lambda function required [not supported]
        parametricSurface(func: Callable[[float, float], Union[Tuple[float, float], Tuple[float, float, float], Vector]], N: int=20, start: float=0, stop: float=1, tol: float=0.01, minDeg: int=1, maxDeg: int=6, smoothing: Optional[Tuple[float, float, float]]=(1, 1, 1))
	        - Create a spline surface approximating the provided function of two independent variables. 
            - lambda function required [not supported]
      # arrays:
        rarray(xSpacing: float, ySpacing: float, xCount: int, yCount: int, center: Union[bool, Tuple[bool, bool]]=True)
	        - Creates a rectangular array of points and pushes them onto the stack.
        polarArray(radius: float, startAngle: float, angle: float, count: int, fill: bool=True, rotate: bool=True)
	        - Creates a polar array of points and pushes them onto the stack.
            - polar: circular or arc-like
      # mirrors:
        mirrorX()
	        - Mirror 2D entities around the x axis of the workplane plane. 
        mirrorY()
	        - Mirror 2D entities around the y axis of the workplane plane.
      # other:
        center(x: float, y: float)
	        - Shift local coordinates to the specified location. 
            - important: moves the current local coordinate system's origin to the specified point on the current workplane.
        offset2D(d: float, kind: Literal['arc', 'intersection', 'tangent']='arc', forConstruction: bool=False)
	        - Creates a 2D offset wire.
            - Shrinks or expands a 2D wire from all directions.
        close()
	        - End construction, and attempt to build a closed wire.
            - Important for polyline(), lineTo(), threePointArc() and tangentArcPoint(). Also, methods like extrude can be performed on closed wire but not on opened one.
            - Cannot use the "move" or "moveTo" methods after "close".
        wire(forConstruction: bool=False)
	        - Returns a CQ object with all pending edges connected into a wire.
        move(xDist: float=0, yDist: float=0)
	        - Move the specified distance from the current point, without drawing.
            - Must read docs of this before use.
        moveTo(x: float=0, y: float=0)
	        - Move to the specified point, without drawing.
            - Must read docs of this before use.
        placeSketch(sketches: Sketch)
	        - Place the provided sketch(es) based on the current items on the stack. 
            - Use with sketch_api e.g. [{"_ref": "sketch_1"}]

    3D Operations (requiring a 2D workplane to be active)
      # 2D to 3D:
        extrude(until: Union[float, Literal['next', 'last'], Face], combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True, both: bool=False, taper: float | None=None)
	        - Use all un-extruded wires in the parent chain to create a prismatic solid. 
            - pushes the profile out towards the direction normal to the plane 
        revolve(angleDegrees: float=360.0, axisStart: Union[Tuple[float, float], Tuple[float, float, float], Vector, NoneType]=None, axisEnd: Union[Tuple[float, float], Tuple[float, float, float], Vector, NoneType]=None, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Use all un-revolved wires in the parent chain to create a solid.
            - revolves the profile around a specific axis
        sweep(path: Union[ForwardRef('Workplane'), Wire, Edge], multisection: bool=False, sweepAlongWires: bool | None=None, makeSolid: bool=True, isFrenet: bool=False, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True, transition: Literal['right', 'round', 'transformed']='right', normal: Union[Tuple[float, float], Tuple[float, float, float], Vector, NoneType]=None, auxSpine: Optional[ForwardRef('Workplane')]=None)
	        - Use all un-extruded wires in the parent chain to create a swept solid.
            - pulls the profile along a specific path
            - Use methods like "polyline", "spline", etc to create the path
        loft(ruled: bool=False, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Make a lofted solid, through the set of wires.
            - Use the "workplane" method and its "offset" param after each profile to create a new workplane and build another profile there. Then, perform loft to connect them and form a solid. Also, ruled=False means smooth; ruled=True means not smooth.
        twistExtrude(distance: float, angleDegrees: float, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Extrudes a wire in the direction normal to the plane, but also twists by the specified angle over the length of the extrusion.
            - Dont perform this on circle, because it will just create a cylinder. Also, this can create a hylical, spiral, screw-thread-like or worm-gear-like shape.
        cutBlind(until: Union[float, Literal['next', 'last'], Face], clean: bool=True, both: bool=False, taper: float | None=None)
	        - Use all un-extruded wires in the parent chain to create a prismatic cut of a certain length from existing solid. 
            - If "until" is a float, it should be negative to cut in the opposite direction to the normal of the plane.
        cutThruAll(clean: bool=True, taper: float=0)
	        - Use all un-extruded wires in the parent chain to create a prismatic cut through the entire existing solid. 
      # boolean:
        cut(toCut: Union[ForwardRef('Workplane'), Solid, Compound], clean: bool=True, tol: float | None=None)
	        - Cuts the provided solid from the current solid, IE, perform a solid subtraction. 
            - toCut: {"_ref": "stored_name"}
        union(toUnion: Union[ForwardRef('Workplane'), Solid, Compound, NoneType]=None, clean: bool=True, glue: bool=False, tol: float | None=None)
	        - Unions all of the items on the stack of toUnion with the current solid. 
            - Don't use this as a replacement for the Assembly API!
            - toUnion: {"_ref": "stored_name"}
        combine(clean: bool=True, glue: bool=False, tol: float | None=None)
	        - Attempts to combine all of the items on the stack into a single item.
            - Unlike union, it operates on items already on the stack. Useful after multiple "add" methods.
        intersect(toIntersect: Union[ForwardRef('Workplane'), Solid, Compound], clean: bool=True, tol: float | None=None)
	        - Intersects the provided solid from the current solid. 
            - toIntersect: {"_ref": "stored_name"}
      # holes:
        cboreHole(diameter: float, cboreDiameter: float, cboreDepth: float, depth: float | None=None, clean: bool=True)
	        - Makes a counterbored hole for each item on the stack.
        cskHole(diameter: float, cskDiameter: float, cskAngle: float, depth: float | None=None, clean: bool=True)
	        - Makes a countersunk hole for each item on the stack.
        hole(diameter: float, depth: float | None=None, clean: bool=True)
	        - Makes a simple hole for each item on the stack.
      # primitives:
        box(length: float, width: float, height: float, centered: Union[bool, Tuple[bool, bool, bool]]=True, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Return a 3d box with specified dimensions for each object on the stack. 
        sphere(radius: float, direct: Union[Tuple[float, float], Tuple[float, float, float], Vector]=(0, 0, 1), angle1: float=-90, angle2: float=90, angle3: float=360, centered: Union[bool, Tuple[bool, bool, bool]]=True, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Returns a 3D sphere with the specified radius for each point on the stack. 
        wedge(dx: float, dy: float, dz: float, xmin: float, zmin: float, xmax: float, zmax: float, pnt: Union[Tuple[float, float], Tuple[float, float, float], Vector]=Vector: (0.0, 0.0..., dir: Union[Tuple[float, float], Tuple[float, float, float], Vector]=Vector: (0.0, 0.0..., centered: Union[bool, Tuple[bool, bool, bool]]=True, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Returns a 3D wedge with the specified dimensions for each point on the stack. 
            - A wedge has 2 parallel rectangles. dx, dz determins the dimensions of one rectangle, while xmin, zmin, xmax, zmin determine the dimensions of the second rectangle. dy determines the distance between those rectangles.
            - "loft" method can be a simpler alternative to create complex objects like wedges, cones, etc.
        cylinder(height: float, radius: float, direct: Union[Tuple[float, float, float], Vector]=Vector: (0.0, 0.0..., angle: float=360, centered: Union[bool, Tuple[bool, bool, bool]]=True, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Returns a cylinder with the specified radius and height for each point on the stack 
      # other:
        text(txt: str, fontsize: float, distance: float, cut: bool=True, combine: Union[bool, Literal['cut', 'a', 's']]=False, clean: bool=True, font: str='Arial', fontPath: str | None=None, kind: Literal['regular', 'bold', 'italic']='regular', halign: Literal['center', 'left', 'right']='center', valign: Literal['center', 'top', 'bottom']='center')
	        - Returns a 3D text.
            - Read docs of this before use because its positioning and cutting can be tricky.
      
    3D Operations (without an active 2D workplane)
      # modifiers:
        fillet(radius: float)
	        - Fillets (rounds) a solid on the selected edges.
        chamfer(length: float, length2: float | None=None)
	        - Chamfers (cuts) a solid on the selected edges.
        shell(thickness: float, kind: Literal['arc', 'intersection']='arc')
	        - Remove the selected faces to create a shell of the specified wall-thickness.
        split(args, kwargs)
	        - Splits a solid on the stack into two parts, optionally keeping the separate parts.
        mirror(mirrorPlane: Union[Literal['XY', 'YX', 'XZ', 'ZX', 'YZ', 'ZY'], Tuple[float, float], Tuple[float, float, float], Vector, Face, ForwardRef('Workplane')]='XY', basePointVector: Union[Tuple[float, float], Tuple[float, float, float], Vector, NoneType]=None, union: bool=False)
	        - Mirror a single CQ object.
            - Moves a single CQ object about a specific plane, but on the current workplane. Kinda like moving the object to a different quadrant in the current workplane
        clean()
	        - Cleans the current solid by removing unwanted edges from the faces.
            - Essential after operations like boolean. Helps with accurate wire selection later on.
      # positioning and orientation (selectors + workplane + center can also help with this):
        transformed(rotate: Union[Tuple[float, float], Tuple[float, float, float], Vector]=(0, 0, 0), offset: Union[Tuple[float, float], Tuple[float, float, float], Vector]=(0, 0, 0))
	        - Create a new workplane based on the current one. 
            - important: create a new workplane with offset and rotation based on the current one
        translate(vec: Union[Tuple[float, float], Tuple[float, float, float], Vector])
	        - Returns a copy of all of the items on the stack moved by the specified translation vector. 
            - move ALL stack items by the specified translation vector 
        rotateAboutCenter(axisEndPoint: Union[Tuple[float, float], Tuple[float, float, float], Vector], angleDegrees: float)
	        - Rotates all items on the stack by the specified angle, about the specified axis 
            - rotate ALL stack items around the overall center 
        rotate(axisStartPoint: Union[Tuple[float, float], Tuple[float, float, float], Vector], axisEndPoint: Union[Tuple[float, float], Tuple[float, float, float], Vector], angleDegrees: float)
	        - Returns a copy of all of the items on the stack rotated through and angle around the axis 
            - rotate ALL stack items around an external point 

    Selection → push matching sub-shapes onto the stack (important: use these with the selector _types defined below)
      faces(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select the faces of objects on the stack, optionally filtering the selection.
      edges(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select the edges of objects on the stack, optionally filtering the selection. 
      vertices(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select the vertices of objects on the stack, optionally filtering the selection.
      wires(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select the wires of objects on the stack, optionally filtering the selection.
      solids(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select the solids of objects on the stack, optionally filtering the selection.
      shells(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select the shells of objects on the stack, optionally filtering the selection.
      compounds(selector: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
	        - Select compounds on the stack, optionally filtering the selection.

    Other Workplane Operations
      workplane(offset: float=0.0, invert: bool=False, centerOption: Literal['CenterOfMass', 'ProjectedOrigin', 'CenterOfBoundBox']='ProjectedOrigin', origin: Union[Tuple[float, float], Tuple[float, float, float], Vector, NoneType]=None)
	        - Creates a new 2D workplane, located relative to the first face on the stack. 
            - Important method! 
            - Example #1: Select a face and call workplane(centerOption='CenterOfMass') to create a new workplane with the origin at the face's center; use "center" method afterward to shift the origin from there and perform further operations there.
            - Example #2: Draw a 2D profile, call workplane(offset=n) to create a new parallel workplane displaced along the face normal, draw a second profile, then loft between them.
      add(obj)
	        - Adds an object or a list of objects to the stack
      tag(name: str)
	        - Tags the current CQ object for later reference.
            - Never use hyphens in tag names! It is not supported. Use underscores instead.
            - Example: Tag a solid, then select it's features later using selectors like solids, faces, edges, etc via the "tag" param.
      end(n: int=1)
	        - Return the nth parent of this CQ element
            - Can be used to iterate over items on the current stack and move a specific item to the current selection.
            - If you use "end" 2x on a stack, then the N of your 2nd end cannot go before the position of the 1st end.
      pushPoints(pntList: Iterable[Union[Tuple[float, float], Tuple[float, float, float], Vector, Location]])
	        - Pushes a list of 2D points onto the stack as vertices.
            - Creates an array of custom 2D points and pushed them onto the stack (similar to "rarray" or "polarArray")
      each(callback: Callable[[Union[Vector, Location, Shape, Sketch]], Shape], useLocalCoordinates: bool=False, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Runs the provided function on each value in the stack, and collects the return values into a new CQ object. 
            - lambda function [not supported]
      eachpoint(arg: Union[Shape, ForwardRef('Workplane'), Callable[[Location], Shape]], useLocalCoordinates: bool=False, combine: Union[bool, Literal['cut', 'a', 's']]=False, clean: bool=True)
	        - Same as each(), except arg is translated by the positions on the stack. 
            - lambda function [not supported] or obj
      val()
	        - Return the first value on the stack. 
            - convert workplane obj to shape obj
      # use these after selectors like faces, edges, etc:
        first()
	        - Return the first item on the stack
        last()
	        - Return the last item on the stack.
        item(i: int)
	        - Return the ith item on the stack. 

    Other Operations:
      newObject(objlist: Iterable[Union[Vector, Location, Shape, Sketch]])
	    - Create a new workplane object from this one.
        - Use this with the tag() method to tag different geometric features of a part without affecting/ losing your original chain position/ stack selection.
        - After tagging, use the tagged part in Assembly API's constrain() methods via "partName?tagName" selection string 
      ancestors(kind: Literal['Vertex', 'Edge', 'Wire', 'Face', 'Shell', 'Solid', 'CompSolid', 'Compound'], tag: str | None=None)
        - Select topological ancestors.
      siblings(kind: Literal['Vertex', 'Edge', 'Wire', 'Face', 'Shell', 'Solid', 'CompSolid', 'Compound'], level: int=1, tag: str | None=None)
        - Select topological siblings.
      sketch()
        - Initialize and return a sketch
        - Use "finalize()" method once done with the sketch operations to shift from sketch-api mode to workplane-api mode [NOT RECOMMENDED - USE SKETCH API!]
      consolidateWires()
        - Attempt to consolidate wires on the stack into a single.
      copyWorkplane(obj: ~T)
        - Copies the workplane from obj.
        - Parameters: obj (a CQ object) – an object to copy the workplane from
        - Returns: a CQ object with obj’s workplane
        - Example: Workplane("front").circle(1).extrude(10).copyWorkplane(cq.Workplane("right", origin=(-5, 0, 0)) ).circle(1).extrude(10) # This creates two perpendicular cylinders
      findSolid(searchStack: bool=True, searchParents: bool=True)
        - Finds the first solid object in the chain, searching from the current node backwards through parents until one is found.
      section(height: float=0.0)
        - Slices current solid at the given height.
      toPending()
        - Adds wires/edges to pendingWires/pendingEdges.
      splineApprox(points: Iterable[Union[Tuple[float, float], Tuple[float, float, float], Vector]], tol: float | None=1e-06, minDeg: int=1, maxDeg: int=6, smoothing: Optional[Tuple[float, float, float]]=(1, 1, 1), forConstruction: bool=False, includeCurrent: bool=False, makeWire: bool=False)
        - Create a spline interpolated through the provided points (2D or 3D).
      workplaneFromTagged(name: str)
        - Copies the workplane from a tagged parent.
      export(fname: str, tolerance: float=0.1, angularTolerance: float=0.1, opt: Optional[Dict[str, Any]]=None)
		- Export Workplane to file.
	  exportSvg(fileName: str)
		- Exports the first item on the stack as an SVG file

    ── _ref / _type in params ──────────────────────────────────────────────────
      {"_ref": "name"}
        - fetch stored object
      {"_type": "Vector",   "x":1, "y":2, "z":3}
        - you MUST use this to pass a vector to the selectors mentioned below!
      {"_type": "Plane",    "name": "XY"}
      
      # selectors types (use these inside the "selector" param of selector methods like "faces", "edges", etc):
      {"_type": "ParallelDirSelector",      "vector": {"_type": "Vector", ...}}
        - selects shapes whose normal/direction is parallel to vector
        - applicable to linear edges and planar faces
      {"_type": "DirectionSelector",        "vector": {"_type": "Vector", ...}}
        - selects shapes whose normal/direction matches vector exactly
        - applicable to linear edges and planar faces
      {"_type": "PerpendicularDirSelector", "vector": {"_type": "Vector", ...}}
        - selects shapes whose normal/direction is perpendicular to vector
        - applicable to linear edges and planar faces
      {"_type": "NearestToPointSelector",   "pnt": [x,y,z]}
        - selects the shape nearest the provided point
        - if the shape is a vertex/ point, the distance is used. for other shapes, the center of mass computes which is closest
        - applicable to all types of shapes
      {"_type": "BoxSelector", "point0": [x,y,z], "point1": [x,y,z], "boundingbox": true}
        - selects shapes whose center (boundingbox=false) or bounding box (boundingbox=true) lies in the 3D box defined by 2 points
        - applicable to all types of shapes
      {"_type": "TypeSelector", "typeString": "PLANE"}
        - applicable face typeString: PLANE, CYLINDER, CONE, SPHERE, TORUS, BEZIER, BSPLINE, REVOLUTION, EXTRUSION, OFFSET, OTHER
        - applicable edge typeString: LINE, CIRCLE, ELLIPSE, HYPERBOLA, PARABOLA, BEZIER, BSPLINE, OFFSET, OTHER
      {"_type": "DirectionMinMaxSelector", "vector": {"_type": "Vector", ...}, "directionMax": true, "tolerance": 1e-6}
        - selects the farthest shape (if directionMax=true) or the closest shape (if directionMax=false) along the specified direction vector
        - if the shape is a vertex/ point, the distance is used. for other shapes, the center of mass is used.
        - applicable to all types of shapes
      {"_type": "RadiusNthSelector",    "n": 0, "directionMax": true, "tolerance": 1e-6}
        - groups wire/edges with the same radius and selects the group with the nth radius; n=0 is smallest (directionMax=false) or largest (directionMax=true)
        - applicable to all edges and wires
      {"_type": "LengthNthSelector",    "n": 0, "directionMax": true, "tolerance": 1e-6}
        - groups wire/edges with the same length and selects the group with the nth length; same n/directionMax semantics as RadiusNthSelector
        - applicable to all edges and wires
      {"_type": "AreaNthSelector",      "n": 0, "directionMax": true, "tolerance": 1e-6}
        - groups shapes with the same area and selects the group with the nth area; same n/directionMax semantics as RadiusNthSelector
        - applicable to faces, shells, solids and closed planar wires
      {"_type": "CenterNthSelector",    "vector": {"_type": "Vector", ...}, "n": 0, "directionMax": true, "tolerance": 1e-6}
        - sorts shapes by their center projected onto vector, selects the nth group (sorting order depends on directionMax param)
        - sorts shapes in a list with order determined by the distance of their center from the specified direction vector (order depends on directionMax param). N is used to index this list.
        - applicable to all shapes
      {"_type": "DirectionNthSelector", "vector": {"_type": "Vector", ...}, "n": 0, "directionMax": true, "tolerance": 1e-6}
        - sorts shapes by their normal/direction projected onto vector, selects the nth group
        - filters and sorts shapes parallel to the specified direction and then returns the Nth one
        - applicable to linear edges and planar faces
      {"_type": "AndSelector",      "left": {...}, "right": {...}}
        - intersection: shapes selected by both left and right selectors
      {"_type": "SumSelector",      "left": {...}, "right": {...}}
        - union: shapes selected by either left or right selector
      {"_type": "SubtractSelector", "left": {...}, "right": {...}}
        - difference: shapes selected by left but not right selector
      {"_type": "InverseSelector",  "selector": {...}}
        - complement: shapes NOT selected by selector
      {"_type": "StringSyntaxSelector", "selectorString": ">Z"}
        - DEFAULT selector! It is a combination of ParallelDirSelector, PerpendicularDirSelector, DirectionSelector, DirectionMinMaxSelector, TypeSelector, AndSelector, SumSelector, InverseSelector, SubtractSelector, DirectionNthSelector, CenterNthSelector
        - axis strings: "X", "Y", "Z", "XY", "XZ", "YZ" or "(x,y,z)" for arbitrary direction
        - logical operators: "and", "or", "not", "except"
        - modifiers: "|" (parallel to), "#" (perpendicular to), ">" (maximum), "<" (minimum), "+" (positive direction), "-" (negative direction), "%" (curve/surface type selector) 
        - examples: ">Z", "<Y", "|X", "%Plane", ">>Y[-2]", "not(<X or >X)", "(not >X[0] and #XY)"

    ── Computed values (_attr / _call / _run_on) ───────────────────────────────
    Inside any "args" or "params" value, three additional dict keys let you
    compute CadQuery objects at resolve-time without a separate tool call:

      {"_attr": {"obj": <resolvable>, "name": "plane"}}
        - Access an attribute on a resolved object.

      {"_call": {"obj": <resolvable>, "method": "toWorldCoords", "args": [[0, 0]]}}
        - Call a method on a resolved object. Use "args" (list) or "params" (dict).
        - NOTE: args is a list of positional arguments, e.g. [[0,0]] passes one arg [0,0].

      {"_run_on": {"obj": {"_ref": "PART"}, "ops": [
          {"method": "faces",     "args": [">>X"]},
          {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
          {"method": "center",    "args": [10, -5]}
      ]}}
        - Run an ops chain on a resolved object without storing the result.

      {"_type": "Vertex", "point": <resolvable>}
        - Construct a Vertex at a Vector (e.g. output of Plane.toWorldCoords).
        - Alternative to {"_type": "Vertex", "x":..., "y":..., "z":...}.

      {"_expr": "cos(30)*cos(45)"}
        - Evaluate a math expression to a float. Trig functions use DEGREES.
        - Available: cos, sin, tan, acos, asin, atan, atan2, sqrt, abs, pow,
                     ceil, floor, pi, e
        - Use inside Vector x/y/z to build direction vectors from angles:
            {"_type": "Vector",
             "x": {"_expr": "cos(30)*cos(45)"},
             "y": {"_expr": "sin(30)*cos(45)"},
             "z": {"_expr": "sin(45)"}}

    Canonical use-case — tag a reference point on a part for Assembly constraints:

      workplane_api(
        start_from="PART",
        store_as="PART_tags",
        operations=[
          {
            "method": "newObject",
            "args": [[{
              "_type": "Vertex",
              "point": {
                "_call": {
                  "obj": {
                    "_attr": {
                      "obj": {
                        "_run_on": {
                          "obj": {"_ref": "PART"},
                          "ops": [
                            {"method": "faces",     "args": [">>X"]},
                            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
                            {"method": "center",    "args": [10, -5]}
                          ]
                        }
                      },
                      "name": "plane"
                    }
                  },
                  "method": "toWorldCoords",
                  "args": [[0, 0]]
                }
              }
            }]]
          },
          {"method": "tag", "args": ["PART-point1"]}
        ]
      )

    Side-effect: tag() writes into the shared ctx, so "PART?PART-point1" becomes
    accessible on the stored PART object for use in Assembly constraints.

    IMPORTANT: store_as naming rule for tagging chains:
        When running a newObject().tag() chain via start_from, ALWAYS use a DIFFERENT store_as name from the original part (e.g. store_as="PART_tags"). Do NOT reuse the original part's name as store_as.

        Why: store_as saves the Workplane at its current stack position. After newObject([Vertex]).tag(), the stack top is a single vertex — not the solid. If you overwrite the original name with this vertex-state object, any later @faces@ or @edges@ selector in assembly_api will fail with "Can not return the Nth element of an empty list".

        Correct pattern:
            workplane_api(start_from="PART", store_as="PART_tags", operations=[
              {"method": "newObject", "args": [[{"_type": "Vertex", ...}]]},
              {"method": "tag", "args": ["PART-point1"]}
            ])
            The tag is written into the shared context regardless of store_as, so "PART?PART-point1" resolves correctly in assembly even though the tag chain was stored under "PART_tags".

        Wrong pattern (corrupts the stored part):
            workplane_api(start_from="PART", store_as="PART", operations=[
              {"method": "newObject", "args": [[{"_type": "Vertex", ...}]]},
              {"method": "tag", "args": ["PART-point1"]}
            ])
            This overwrites "PART" with a vertex-state Workplane, breaking all face/edge selectors for that part in assembly_api.

    ── RETURN ──────────────────────────────────────────────────────────────────
    {"status":"success", "name":str, "obj_type":str,
     "properties": {"volume":float, "area":float, "center":[x,y,z], "bounding_box":{...}}}
    """
    try:
        if start_from:
            obj = _get(start_from)
        else:
            resolved = resolve_value(init_params or {})
            plane = resolved.get("plane", "XY")
            if isinstance(plane, str):
                obj = Workplane(plane)
            elif isinstance(plane, Plane):
                obj = Workplane()
                obj.plane = plane
            else:
                obj = Workplane(plane)

        obj = _run(obj, operations)

        # tag() chains via newObject([Vertex]) leave only a Vertex on the stack.
        # Storing that would break assembly selectors (partName@faces@...) on the part.
        # Tags are written into the shared ctx, so they remain accessible even if we
        # restore the original solid-bearing Workplane as the stored reference.
        if start_from and isinstance(obj, Workplane):
            vals = obj.vals()
            if vals and all(isinstance(v, (Vertex, Edge, Wire)) for v in vals):
                obj = _get(start_from)

        name = store_as or _auto_name("workplane")
        _store(name, obj)
        _show(obj)

        return json.dumps({"status": "success", "name": name,
                           "obj_type": _obj_type(obj), "properties": _properties(obj)})
    except Exception as e:
        return _error(str(e), traceback.format_exc())

# =============================================================================
# TOOL 2 — sketch_api
# =============================================================================

@mcp.tool(name="sketch_api")
async def sketch_api(
    operations: List[dict],
    init_params: Optional[dict] = None,
    start_from: Optional[str] = None,
    store_as: Optional[str] = None,
) -> str:
    """
    Build 2D profiles using CadQuery's Sketch API via method chaining.
    The stored Sketch can be passed to workplane_api via:
      {"method": "placeSketch", "args": [{"_ref": "sketch_1"}]}
    followed by extrude or cutBlind.

    ── TWO WORKFLOWS ───────────────────────────────────────────────────────────

    WORKFLOW 1 — Face-based (recommended)
    ──────────────────────────────────────
    Build a face pool. mode controls how each new face combines:
      'a' (default) → fuse    's' → subtract    'i' → intersect
      'r' → replace           'c' → construction (must tag, invisible)

    Example — rectangle with a centered hole:
      [
        {"method": "rect",   "params": {"w": 20, "h": 10}},
        {"method": "circle", "params": {"r": 3, "mode": "s"}},
        {"method": "clean",  "params": {}}
      ]

    Example — 3×2 grid of holes inside a border:
      [
        {"method": "rarray",  "params": {"xs": 5, "ys": 5, "nx": 3, "ny": 2}},
        {"method": "circle",  "params": {"r": 1.5}},
        {"method": "reset",   "params": {}},
        {"method": "rect",    "params": {"w": 20, "h": 14, "mode": "a"}},
        {"method": "clean",   "params": {}}
      ]

    WORKFLOW 2 — Edge-based (complex profiles / constraint solving)
    ───────────────────────────────────────────────────────────────
    Build wire from individual edges → assemble into face(s).

    Example — triangle:
      [
        {"method": "segment", "params": {"p1": [0,0], "p2": [0,3]}},
        {"method": "segment", "params": {"p1": [0,3], "p2": [2,0]}},
        {"method": "segment", "params": {"p1": [2,0], "p2": [0,0]}},
        {"method": "assemble","params": {}}
      ]

    Example — constraint-based (experimental):
      [
        {"method": "segment",  "params": {"p1": [0,0], "p2": [0,3], "tag": "s1"}},
        {"method": "arc",      "params": {"p1": [0,3], "p2": [1.5,1.5], "p3": [0,0], "tag": "a1"}},
        {"method": "constrain","params": {"tag1": "s1", "kind": "Fixed", "param": null}},
        {"method": "constrain","params": {"tag1": "s1", "tag2": "a1", "kind": "Coincident", "param": null}},
        {"method": "solve",    "params": {}},
        {"method": "assemble", "params": {}}
      ]

    ── INIT ────────────────────────────────────────────────────────────────────
    init_params: omit — Sketch() takes no arguments.
    start_from:  name of an existing stored Sketch to continue from.
    store_as: name to store the result under (auto-generated if omitted).

    ── FACE API ────────────────────────────────────────────────────────────────
    face(b: Union[Wire, Iterable[Edge], Shape, ~T], angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None, ignore_selection: bool=False)
		- Construct a face from a wire or edges.
	rect(w: float, h: float, angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct a rectangular face.
	circle(r: float, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct a circular face.
	ellipse(a1: float, a2: float, angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct an elliptical face.
	trapezoid(w: float, h: float, a1: float, a2: float | None=None, angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct a trapezoidal face.
	slot(w: float, h: float, angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct a slot-shaped face.
	regularPolygon(r: float, n: int, angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct a regular polygonal face.
	polygon(pts: Iterable[Union[Vector, Tuple[float, float]]], angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Construct a polygonal face.
	rarray(xs: float, ys: float, nx: int, ny: int)
		- Generate a rectangular array of locations.
	parray(r: float, a1: float, da: float, n: int, rotate: bool=True)
		- Generate a polar array of locations.
	distribute(n: int, start: float=0, stop: float=1, rotate: bool=True)
		- Distribute locations along selected edges or wires.
	each(callback: Callable[[Location], Union[Face, ForwardRef('Sketch'), Compound]], mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None, ignore_selection: bool=False)
		- Apply a callback on all applicable entities.
	push(locs: Iterable[Union[Location, Vector, Tuple[float, float]]], tag: str | None=None)
		- Set current selection to given locations or points.
	hull(mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Generate a convex hull from current selection or all objects.
	offset(d: float, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Offset selected wires or edges.
	fillet(d: float)
		- Add a fillet based on current selection.
	chamfer(d: float)
		- Add a chamfer based on current selection.
	clean()
		- Remove internal wires.

    ── EDGE API ────────────────────────────────────────────────────────────────
    edge(val: Edge, tag: str | None=None, forConstruction: bool=False)
		- Add an edge to the sketch.
	segment(p1: Union[Vector, Tuple[float, float]], p2: Union[Vector, Tuple[float, float]], tag: str | None=None, forConstruction: bool=False).
		- Construct a segment.
	segment(p2: Vector | Tuple[int | float, int | float], tag: str | None = None, forConstruction: bool = False)
		- Construct a segment.
	segment(l: int | float, a: int | float, tag: str | None = None, forConstruction: bool = False)
		- Construct a segment.
	arc(p1: Union[Vector, Tuple[float, float]], p2: Union[Vector, Tuple[float, float]], p3: Union[Vector, Tuple[float, float]], tag: str | None=None, forConstruction: bool=False)
		- Construct an arc.
	arc(p2: Vector | Tuple[int | float, int | float], p3: Vector | Tuple[int | float, int | float], tag: str | None = None, forConstruction: bool = False)
		- Construct an arc.
	arc(c: Vector | Tuple[int | float, int | float], r: int | float, a: int | float, da: int | float, tag: str | None = None, forConstruction: bool = False)
		- Construct an arc.
	bezier(pts: Iterable[Union[Vector, Tuple[float, float]]], tag: str | None=None, forConstruction: bool=False)
		- Construct an bezier curve.
	spline(pts: Iterable[Union[Vector, Tuple[float, float]]], tangents: Optional[Iterable[Union[Vector, Tuple[float, float]]]], periodic: bool, tag: str | None=None, forConstruction: bool=False)
		- Construct a spline edge.
	spline(pts: Iterable[Vector | Tuple[int | float, int | float]], tag: str | None = None, forConstruction: bool = False)
		- Construct a spline edge.
	close(tag: str | None=None)
		- Connect last edge to the first one.
	assemble(mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Assemble edges into faces.

    ── CONSTRAINTS (experimental) ──────────────────────────────────────────────
    constrain(tag1: str, tag2: str, constraint: Literal['Fixed', 'FixedPoint', 'Coincident', 'Angle', 'Length', 'Distance', 'Radius', 'Orientation', 'ArcAngle'], arg: Any)
		- Add a constraint. Double entity.
	constrain(tag: str, constraint: Literal['Fixed', 'FixedPoint', 'Coincident', 'Angle', 'Length', 'Distance', 'Radius', 'Orientation', 'ArcAngle'], arg: Any)
		- Add a constraint. Single entity.
	solve()
		- Solve current constraints and update edge positions
   
    Kinds: Fixed, Coincident, Angle, Length, Distance, ArcAngle

    ── SELECTION ───────────────────────────────────────────────────────────────
    tag(tag: str)
		- Tag current selection.
	select(tags: str)
		- Select based on tags.
	reset()
		- Reset current selection.
	delete()
		- Delete selected object.
	faces(s: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
		- Select faces.
	wires(s: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
		- Select wires.
	edges(s: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
		- Select edges.
	vertices(s: Union[str, selectors.Selector, NoneType]=None, tag: str | None=None)
		- Select vertices.
  
    ── OTHER ──────────────────────────────────────────────────────────────────
    add()
		- Add selection to the underlying faces.
	apply(f: Callable[[Iterable[Union[Shape, Location]]], Iterable[Union[Shape, Location]]])
		- Apply a callable to all items at once.
	filter(f: Callable[[Union[Shape, Location]], bool])
		- Filter items using a boolean predicate.
	invoke(f: Union[Callable[[~T], ~T], Callable[[~T], NoneType], Callable[[], NoneType]])
		- Invoke a callable mapping Sketch to Sketch or None.
	map(f: Callable[[Union[Shape, Location]], Union[Shape, Location]])
		- Apply a callable to every item separately.
	replace()
		- Replace the underlying faces with the selection.
	sort(key: Callable[[Union[Shape, Location]], Any])
		- Sort items using a callable.
	subtract()
		- Subtract selection from the underlying faces.
	val()
		- Return the first selected item, underlying compound or first edge.
	vals()
		- Return all selected items, underlying compound or all edges.
	moved(args, kwargs)
		- Create a partial copy of the sketch with moved _faces.
	located(loc: Location)
		- Create a partial copy of the sketch with a new location.
	copy()
		- Create a partial copy of the sketch.
	finalize()
		- Finish sketch construction and return the parent.
	importDXF(filename: str, tol: float=1e-06, exclude: List[str]=[], include: List[str]=[], angle: float=0, mode: Literal['a', 's', 'i', 'c', 'r']='a', tag: str | None=None)
		- Import a DXF file and construct face(s)
	export(fname: str, tolerance: float=0.1, angularTolerance: float=0.1, opt: Optional[Dict[str, Any]]=None)
		- Export Sketch to file.

    ── RETURN ──────────────────────────────────────────────────────────────────
    {"status":"success", "name":str, "obj_type":"Sketch",
     "properties": {"face_count":int, "total_area":float, "edge_count":int}}
    """
    try:
        if start_from:
            obj = _get(start_from)
        else:
            obj = Sketch()

        obj = _run(obj, operations)
        name = store_as or _auto_name("sketch")
        _store(name, obj)
        _show(obj)

        return json.dumps({"status": "success", "name": name,
                           "obj_type": _obj_type(obj), "properties": _properties(obj)})
    except Exception as e:
        return _error(str(e), traceback.format_exc())

# =============================================================================
# TOOL 3 — assembly_api
# =============================================================================

@mcp.tool(name="assembly_api")
async def assembly_api(
    operations: List[dict],
    init_params: Optional[dict] = None,
    start_from: Optional[str] = None,
    store_as: Optional[str] = None,
) -> str:
    """
    Assembly API fundamentals: 
        - Use Workplane API to build each part (one part = one piece on a CNC machine or printer), then use Assembly API to combine those distinct parts together with constraint-based relative positioning/orientation, part-specific colors, hierarachy, sub-assemblies, etc.
        - Always use constraints. Constraints offer a better representation of the real world relationship the user wants to model than directly supplying locations. They allow you to dynamically position parts relative to each other. With constraints, if one part's location changes, then it automatically updates the location of all other connected parts.
        - There are a total of 9 constraints (5 relative, 4 fixed). Each constraint is basically a cost function. After all constraints have been defined, a solver updates the position and orientation of the parts to minimize the sum of all cost functions. The solver is basically an optimizer.
        - The tree structure (hierarchical organization of parts) of an assembly consist of three elements: 
            - root/parent node: a single object passed to the current assembly via "init_params".
                - {"shape": {"_ref": "stored_shape"}, "loc": {"_type": "Location", ...}, "name": "my_assembly", "color": {"_type": "Color", ...}}
                - this node sits at the top of the tree structure (above all children nodes)
                - there can be only one root/parent node in an assembly. you can use it to define a base/ anchor part.
                - you can apply constraints on it just like how you can do on children nodes
                - this node is OPTIONAL, meaning it is completely fine to create an assembly containing only children nodes and no root node.
            - children nodes: objects passed to the current assembly via the "add" method.
                - all children nodes are on the same hierarchical level (like siblings), but the order in which you add them in the assembly determine their order in the tree structure.
            - sub-assemblies: another assembly passed as a child node to the current assembly via the "add" method. 
                - assemblies may be nested to form sub-assemblies, reflecting real-world product hierarchies.
                - sub-assemblies are also regarded as children nodes of the current assembly, but they have their own root/parent and children nodes.
                - How constraints work with sub-assemblies:
                    - The path separator / is used to address parts within a sub-assembly from a parent context, yielding selectors of the form "subassembly_name/part_name@type@StringSyntaxSelector". This is a critical point with a non-obvious consequence: the constraint solver operates on a fully flattened namespace. When .solve() is invoked on the top-level assembly, the entire hierarchy — across all nesting depths — is reduced to a flat set of individual entities, each solved independently. Sub-assemblies carry no intrinsic rigidity within the solver. A constraint targeting a sub-assembly by its root name ("subassembly_name@type@StringSyntaxSelector") displaces the sub-assembly's root location, and all children follow by virtue of their relative positioning. A constraint targeting a named path ("subassembly_name/part_name@type@StringSyntaxSelector") into the sub-assembly moves only that specific part, leaving all sibling parts unaffected. Articulation of a multi-part sub-assembly therefore requires explicit constraints between each of its constituent parts, all defined at the top-level assembly and resolved in a single unified solve pass.
        - Available methods (Always use them in the same order in which they are listed below):
            1. "add" - add an object created via workplane api to this assembly
                - add(arg: AssemblyObjects, loc: Location | None = None, name: str | None = None, color: Color | None = None, material: Material | str | None = None, metadata: Dict[str, Any] | None = None)
            2. "constrain" - apply a constraint between parts
                - ALWAYS use the positional "args" form. "constrain" is a *args method and does not accept keyword arguments.
                - Relative constraints (5): {"method": "constrain", "args": ["query1", "query2", "Kind"]}
                    - To pass a non-default param: {"method": "constrain", "args": ["query1", "query2", "Kind"], "kwargs": {"param": value}}
                - Fixed constraints (4) without param: {"method": "constrain", "args": ["query1", "Kind"]}
                - Fixed constraints (4) with param — pass it as the 3rd positional arg: {"method": "constrain", "args": ["query1", "Kind", param_value]}
            3. "solve" - calculates position and orientation based on the provided constraints
                - solve()
            4. "export" - exports the assembly to a file
                - export(path: str, exportType: Literal['STEP','XML','XBF','GLTF','VTKJS','VRML','STL'] | None = None, mode: Literal['default','fused'] = 'default', tolerance: float = 0.1, angularTolerance: float = 0.1, unit: Literal['MM','CM','M','KM','INCH','FT','MI','UM','NM'] = 'MM', outputUnit: Literal['MM','CM','M','KM','INCH','FT','MI','UM','NM'] | None = None, **kwargs)
                - DEFAULT export type: STEP — only change if the user explicitly requests another format
            5. "toCompound" - converts the multi-part assembly to a single compound solid
                - toCompound()

    ── Adding objects section ─────────────────────────────────────────────────────────────────────

    "add" method format and info:
        - add(arg: {"_ref": "stored_shape"}, loc: {"_type": "Location", ...} | None = None, name: str | None = None, color: {"_type": "Color", ...}| None = None, material: Material | str | None = None, metadata: Dict[str, Any] | None = None)
            - "arg": {"_ref": "stored_shape"} - the object you created using workplane api that you want to add in this assembly
            - "loc": {"_type": "Location", ...} - set the initial position and orientation of the added object in the assembly (it gets overridden by constraints and solver, but an approximated "loc" can still be helpful for the solver)
            - "name": the name used to reference this assembly object in the "constrain" method (via its "query1" and "query2" positional args) later on to apply constraints on it
            - "color": {"_type": "Color", ...} - set the material color of the added object in the assembly
            - "material": Not supported right now, so set to "None"
            - "metadata": Dict[str, Any] - any specific metadata/ context about the assembly part (do this if the user asks you to export the assembly to a STEP file)

    {"_type": "Location", ...} ("loc" param of the "add" method):
        method #1: {"_type": "Location", "x":0, "y":0, "z":0, "rx":0, "ry":0, "rz":0}
            - "x", "y", "z" are 3D global coordinates for translation
            - "rx", "ry", "rz" are Euler angle values for rotation (order in which rotation is applied: rx -> ry -> rz)
            - all arguments support both positive and negative values
        method #2: {"_type": "Location", "x":0, "y":0, "z":0, "ax":0, "ay":0, "az":0, "deg":0}
            - This method is not supported yet!
            - "x", "y", "z" are 3D global coordinates for translation
            - "ax", "ay", "az" define an axis around which the rotation should happen
            - "deg" defines defines the angle of the rotation 
            - all arguments support both positive and negative values
        note:
            - This gets overriden by constraints and solver, but providing an approximated initial position and orientation that is as close as possible to the desired ultimate post-constraint position and orientation can be helpful for the constraint solver.
                - Quote from official docs: "If initial locations and the method solve() are used the solver will overwrite these initial locations with it’s solution, however initial locations can still affect the final solution. In an underconstrained system the solver may not move an object if it does not contribute to the cost function, or if multiple solutions exist (ie. multiple instances where the cost function is at a minimum) initial locations can cause the solver to converge on one particular solution. For very complicated assemblies setting approximately correct initial locations can also reduce the computational time required."
            - The selectors used by constraints ("query1" and "query2") ignore the intial position and orientation defined via "loc" when adding objects to the assembly; instead, they use the position and orientation of the objects that was defined when they were created in the Workplane API.
                - Remember this point when you define the initial orientation of an object via the "loc" argument and also plan to perform rotational constraint on it; otherwise, it can cause confusion with making accurate selection for the constraints.
            - Ideally, never use "loc" alone for positioning and orientation! Use it to aid the constraint solver. The constraints must be the main means for positioning and orientation of parts in an assembly!

    {"_type": "Color", ...} ("color" param of the "add" method):
        method #1: {"_type": "Color", name= "..."}
            - All available values for the "name" argument (some of these might be a bit misleading - if so, use method #2): 
                aliceblue, antiquewhite, antiquewhite1, antiquewhite2, antiquewhite3, antiquewhite4, aquamarine1, aquamarine2, aquamarine4, azure, azure2, azure3, azure4, beet, beige, bisque, bisque2, bisque3, bisque4, black, blanchedalmond, blue, blue1, blue2, blue3, blue4, blueviolet, brown, brown1, brown2, brown3, brown4, burlywood, burlywood1, burlywood2, burlywood3, burlywood4, cadetblue, cadetblue1, cadetblue2, cadetblue3, cadetblue4, chartreuse, chartreuse1, chartreuse2, chartreuse3, chartreuse4, chocolate, chocolate1, chocolate2, chocolate3, chocolate4, coral, coral1, coral2, coral3, coral4, cornflowerblue, cornsilk1, cornsilk2, cornsilk3, cornsilk4, cyan, cyan1, cyan2, cyan3, cyan4, darkgoldenrod, darkgoldenrod1, darkgoldenrod2, darkgoldenrod3, darkgoldenrod4, darkgreen, darkkhaki, darkolivegreen, darkolivegreen1, darkolivegreen2, darkolivegreen3, darkolivegreen4, darkorange, darkorange1, darkorange2, darkorange3, darkorange4, darkorchid, darkorchid1, darkorchid2, darkorchid3, darkorchid4, darksalmon, darkseagreen, darkseagreen1, darkseagreen2, darkseagreen3, darkseagreen4, darkslateblue, darkslategray, darkslategray1, darkslategray2, darkslategray3, darkslategray4, darkturquoise, darkviolet, deeppink, deeppink2, deeppink3, deeppink4, deepskyblue1, deepskyblue2, deepskyblue3, deepskyblue4, dodgerblue1, dodgerblue2, dodgerblue3, dodgerblue4, firebrick, firebrick1, firebrick2, firebrick3, firebrick4, floralwhite, forestgreen, gainsboro, ghostwhite, gold, gold1, gold2, gold3, gold4, goldenrod, goldenrod1, goldenrod2, goldenrod3, goldenrod4, gray, gray0, gray1, gray10, gray11, gray12, gray13, gray14, gray15, gray16, gray17, gray18, gray19, gray2, gray20, gray21, gray22, gray23, gray24, gray25, gray26, gray27, gray28, gray29, gray3, gray30, gray31, gray32, gray33, gray34, gray35, gray36, gray37, gray38, gray39, gray4, gray40, gray41, gray42, gray43, gray44, gray45, gray46, gray47, gray48, gray49, gray5, gray50, gray51, gray52, gray53, gray54, gray55, gray56, gray57, gray58, gray59, gray6, gray60, gray61, gray62, gray63, gray64, gray65, gray66, gray67, gray68, gray69, gray7, gray70, gray71, gray72, gray73, gray74, gray75, gray76, gray77, gray78, gray79, gray8, gray80, gray81, gray82, gray83, gray85, gray86, gray87, gray88, gray89, gray9, gray90, gray91, gray92, gray93, gray94, gray95, gray97, gray98, gray99, green, green1, green2, green3, green4, greenyellow, honeydew, honeydew2, honeydew3, honeydew4, hotpink, hotpink1, hotpink2, hotpink3, hotpink4, indianred, indianred1, indianred2, indianred3, indianred4, ivory, ivory2, ivory3, ivory4, khaki, khaki1, khaki2, khaki3, khaki4, lavender, lavenderblush1, lavenderblush2, lavenderblush3, lavenderblush4, lawngreen, lemonchiffon1, lemonchiffon2, lemonchiffon3, lemonchiffon4, lightblue, lightblue1, lightblue2, lightblue3, lightblue4, lightcoral, lightcyan, lightcyan1, lightcyan2, lightcyan3, lightcyan4, lightgoldenrod, lightgoldenrod1, lightgoldenrod2, lightgoldenrod3, lightgoldenrod4, lightgoldenrodyellow, lightgray, lightpink, lightpink1, lightpink2, lightpink3, lightpink4, lightsalmon1, lightsalmon2, lightsalmon3, lightsalmon4, lightseagreen, lightskyblue, lightskyblue1, lightskyblue2, lightskyblue3, lightskyblue4, lightslateblue, lightslategray, lightsteelblue, lightsteelblue1, lightsteelblue2, lightsteelblue3, lightsteelblue4, lightyellow, lightyellow2, lightyellow3, lightyellow4, limegreen, linen, magenta, magenta1, magenta2, magenta3, magenta4, maroon, maroon1, maroon2, maroon3, maroon4, matrablue, matragray, mediumaquamarine, mediumorchid, mediumorchid1, mediumorchid2, mediumorchid3, mediumorchid4, mediumpurple, mediumpurple1, mediumpurple2, mediumpurple3, mediumpurple4, mediumseagreen, mediumslateblue, mediumspringgreen, mediumturquoise, mediumvioletred, midnightblue, mintcream, mistyrose, mistyrose2, mistyrose3, mistyrose4, moccasin, navajowhite1, navajowhite2, navajowhite3, navajowhite4, navyblue, oldlace, olivedrab, olivedrab1, olivedrab2, olivedrab3, olivedrab4, orange, orange1, orange2, orange3, orange4, orangered, orangered1, orangered2, orangered3, orangered4, orchid, orchid1, orchid2, orchid3, orchid4, palegoldenrod, palegreen, palegreen1, palegreen2, palegreen3, palegreen4, paleturquoise, paleturquoise1, paleturquoise2, paleturquoise3, paleturquoise4, palevioletred, palevioletred1, palevioletred2, palevioletred3, palevioletred4, papayawhip, peachpuff, peachpuff2, peachpuff3, peachpuff4, peru, pink, pink1, pink2, pink3, pink4, plum, plum1, plum2, plum3, plum4, powderblue, purple, purple1, purple2, purple3, purple4, red, red1, red2, red3, red4, rosybrown, rosybrown1, rosybrown2, rosybrown3, rosybrown4, royalblue, royalblue1, royalblue2, royalblue3, royalblue4, saddlebrown, salmon, salmon1, salmon2, salmon3, salmon4, sandybrown, seagreen, seagreen1, seagreen2, seagreen3, seagreen4, seashell, seashell2, seashell3, seashell4, sienna, sienna1, sienna2, sienna3, sienna4, skyblue, skyblue1, skyblue2, skyblue3, skyblue4, slateblue, slateblue1, slateblue2, slateblue3, slateblue4, slategray, slategray1, slategray2, slategray3, slategray4, snow, snow2, snow3, snow4, springgreen, springgreen2, springgreen3, springgreen4, steelblue, steelblue1, steelblue2, steelblue3, steelblue4, tan, tan1, tan2, tan3, tan4, teal, thistle, thistle1, thistle2, thistle3, thistle4, tomato, tomato1, tomato2, tomato3, tomato4, turquoise, turquoise1, turquoise2, turquoise3, turquoise4, violet, violetred, violetred1, violetred2, violetred3, violetred4, wheat, wheat1, wheat2, wheat3, wheat4, white, whitesmoke, yellow, yellow1, yellow2, yellow3, yellow4, yellowgreen
            - By default, use "gray90" for everything. It creates an off-white material color. If the user increases the material "metalness" variable in the OCP viewer, then "gray90" makes the material look like silver metal.
        method #2: {"_type": "Color", "r":0, "g":0, "b":0, "a":1}
            - "r", "g", "b" accept EITHER 0–255 integers OR 0.0–1.0 floats. The server auto-normalises: if any channel exceeds 1 the whole triple is divided by 255.
            - "a" is alpha (0.0 = fully transparent, 1.0 = fully opaque). Always pass a value in the 0.0–1.0 range.
        note:
            - By default (if you don't set a color for an object) the objects are displayed to the users with a yellowish color in the viewer. 

    ── Constraint section ─────────────────────────────────────────────────────────────────────────

    "constrain" method formats and info:
        CRITICAL: "constrain" is an *args method in CadQuery. It does NOT accept keyword arguments. 
        ALWAYS use the positional "args" form in MCP tool calls. NEVER use the "params" (keyword) form.

        Positional dispatch rules (CadQuery detects the form from the number and types of args):
            Relative constraints (5 total — 2 query strings + kind string):
                {"method": "constrain", "args": ["query1", "query2", "Kind"]}
                With a non-default param (e.g. Axis at 90°, PointInPlane with offset):
                {"method": "constrain", "args": ["query1", "query2", "Kind"], "kwargs": {"param": value}}
            Fixed constraints, no param (Fixed):
                {"method": "constrain", "args": ["query1", "Kind"]}
            Fixed constraints with param (FixedPoint, FixedAxis, FixedRotation — param is the 3rd positional arg):
                {"method": "constrain", "args": ["query1", "Kind", param_value]}

        Note:
            "query1" and "query2" allow you to select an object or a particular geometry of it.

        Selection/ query string formats (aka the formats of "query1", "query2"):
            - "partName@type@StringSyntaxSelector" 
                - Fully supported by Point, Axis, Plane, PointInPlane, PointOnLine, FixedPoint, FixedAxis
                - "partName" = defined by the "name" param of the "add()" and init methods
                - "type" = faces/ vertices/ edges
                - "StringSyntaxSelector" = verbatim Workplane API's StringSyntaxSelector syntax
            - "partName"
                - Fully supported by Fixed, FixedRotation, FixedPoint (Obj's center of mass), Point (Obj's center of mass)
                - Partially supported by: PointInPlane (Obj 1's center of mass), PointOnLine (Obj 1's center of mass) 
            - "partName?tagName"
                - Use this for complex/ advanced selection that can't be done via "partName@type@StringSyntaxSelector"
                - "tagName" = defined by the "tag()" method of the Workplane API
                - "partName" and "tagName" must be associated with the same object (you cannot use part2's tag with part1)
                - IMPORTANT: Tag must be created without overwriting the part's stored name. When tagging via workplane_api(start_from="PART", ...), always use a different store_as value (e.g. "PART_tags"), NOT "PART". Overwriting the part name with the tagging chain's result leaves a vertex on the stack, breaking all @faces@/@edges@ selectors. Also, tag names containing hyphens are NOT supported (use underscores instead). See workplane_api tagging notes for the correct pattern.
            - "subassembly_name@type@StringSyntaxSelector"
                - Use this to select an entire sub-assembly (specifically its root)
            - "subassembly_name/part_name@type@StringSyntaxSelector"
                - Use this to select a specific part of a sub-assembly 

    Constraint Kinds (Relative - One object's position/orientation is dependent on that of other object):
        "Point":
            - Definition: Minimizes the distance between the centers of two shapes. 
                - This is a translation constraint; it doesn't lock any rotational degrees of freedom.
            - Param: Desired offset between the two centers (Default = 0). 
                - Negative and Positive values have the same effect.
                - The offset happens in ANY random direction (think of the offset as an imaginary sphere's radius, the sphere is on the center of either object, the other object's center must touch ANYWHERE on the surface of this sphere)
                - This param doesn't allow you to define the direction of the offset, thus it is ideal to keep it untouched at 0. 
                - Instead of param, use a dummy vertex to create an offset in a specific direction.
            - Supported shape types: Any (the point is set at the shape's center of Mass). 
                - Example: When a face is selected, then the Point is at that face's center.
            - Example #1: How to select a particular point anywhere on any face on an object for Point constraint?
                - workplane_api("init_params": { "plane": "XY" }, "operations": [ { "method": "box", "args": [ 30, 30, 30 ]} ], "store_as": "PART")
                    # First create the object SEPARATELY
                - workplane_api("start_from": "PART", "operations": [{"method": "newObject", "args": [[{"_type": "Vertex", "point": {"_call": {"obj": {"_attr": {"obj": {"_run_on": {"obj": {"_ref": "PART"}, "ops": [{"method": "faces", "args": [">>X"]}, {"method": "workplane", "params": {"centerOption": "CenterOfMass"}}, {"method": "center", "args": [10, -5]}]}}, "name": "plane"}}, "method": "toWorldCoords", "args": [[0, 0]]}}}]]}, {"method": "tag", "args": ["PART-point1"]}]) 
                    # This creates a vertex 10mm in the +X direction and 5mm in the -Y direction from the center of the >>X face of PART and tags it without affecting PART's actual chain position/ stack selection
                - Note:
                    # newObject() allows you to tag without losing your PART's chain position/ stack selection
                    # Workplane API has a plane() attribute that tracks the current 2D local coordinate system's position/orientation in the 3D world space (like a mapping system). It has 3 parts: origin, xDir/yDir and normal.
                    # plane.toWorldCoords(x,y) converts your 2D local workplane point to 3D world coordinates (x,y,z). use the x,y param to select a point in the 2D local workplane before the conversion (it's affected by the center(x,y) method of Workplane API)
                    # Vertex.makeVertex(x,y,z) converts the raw 3D world coordinates into a OCCT Vertex Shape.
            - Example #2: Another way to do almost the same thing as Example #1
                - workplane_api("init_params": {"plane": "XY"}, "operations": [{"method": "box", "args": [30, 30, 30]}, {"method": "faces", "args": [">>X"]}, {"method": "workplane", "params": {"centerOption": "CenterOfMass"}}, {"method": "center", "args": [10, -5]}, {"method": "hole", "args": [1]}], "store_as": "PART")
                    # This creates a hole of 1mm diameter 10mm in the +X direction and 5mm in the -Y direction from the center of the >>X face. 
                    # Btw, center(x,y) method can't be the last element on the original stack, as it only shifts & selects 2D local coordinate system; otherwise it'll raise an error in the assembly.
                - workplane_api("start_from": "PART", "operations": [{"method": "newObject", "args": [[{"_type": "Vertex", "point": {"_call": {"obj": {"_attr": {"obj": {"_ref": "PART"}, "name": "plane"}}, "method": "toWorldCoords", "args": [[0, 0]]}}}]]}, {"method": "tag", "args": ["PART-point2"]}]) 
                    # This creates a vertex where the hole was created. 
                    # Basically, "PART.plane" directly picks up the last plane present on top of the original stack, which in this case was "faces(">>X").workplane(centerOption="CenterOfMass").center(10,-5)"
                - Note:
                    # the same thing (creating a custom hole and tagging its center for assembly constraints) can be done via Example #1's method too!
                    # don't use this method if the last plane on top of the original stack is not the desired one for tagging OR if you need to create multiple tags on multiple different planes of the same object (there can only be 1 plane on top of each original stack) -> use Example #1's method instead
            - Example #3: How to create a dummy vertex by peforming offset (with a specific direction) on the point selected in Example #1?
                - workplane_api("start_from": "PART", "operations": [{"method": "newObject", "args": [[{"_type": "Vertex", "point": {"_call": {"obj": {"_attr": {"obj": {"_run_on": {"obj": {"_ref": "PART"}, "ops": [{"method": "faces", "args": [">>X"]}, {"method": "workplane", "params": {"offset": -5, "centerOption": "CenterOfMass"}}, {"method": "center", "args": [10, -5]}]}}, "name": "plane"}}, "method": "toWorldCoords", "args": [[0, 0]]}}}]]}, {"method": "tag", "args": ["PART-dummy_vertex"]}])
                    # This moves Example #1's point 5mm inside the PART (because that's where PART.faces(">>X")'s inverse normal is pointing)
                - Note:
                    # the "offset" param of workplane() allows you to create an offset in any direction (positive values mean normal direction and negative mean inverse normal direction). 
                    # the "invert" param of workplane() can also define offset direction.
            - Note:
                - Example #1 and #2's method can be used to define "joints" in an assembly (e.g. connecting a motor mount's shaft hole with a robotic arm's mounting hole). Rotational constraints like "Axis", "FixedAxis", etc can then be used to rotate objects around these joints (e.g. rotate a robotic arm around its connection point with the motor mount).
                - Example #3's dummy vertex method can be used to create a "tolerance gap" between two assembly objects.
                - "Point" is only a translational constraint, meaning you must use rotational constraints with it; otherwise, the orientation of the objects will be random and abnormal.
                - Some common example uses are centering faces or aligning verticies.

        "Axis":
            - Definition: Rotates two objects so that the angle between their specified directional vectors matches the specified angle.
                - This is a rotational constraint; it doesn't lock any translational degrees of freedom.
                - One Axis constraint locks 2 rotational degrees of freedom (RDOF) of an object. the remaining 1 RDOF is that object's specified directional vector itself, allowing the object to spin around it.
                - Two Axis constraints on non-parallel (perpendicular) axis locks all three RDOF of an object. 
            - Param: Desired angle between the two specified directional vectors in degrees (Default = 180). 
                - The default angle of 180 degrees sets the two directions opposite to each other. This represents a "mate" relationship, where the external faces of two objects touch.
                - If the angle is set to zero, the two objects will point in the same direction.
                - Acceptable angle values range from 0 to 180 degrees. Values above 180 will cause weird abnormal results.
                - Negative and positive values have the same effect, meaning they can't be used to control the direction of the rotation.
                - The constraint solver will converge to whichever valid solution is geometrically closest to the object's starting/ initial orientation (defined by the "loc" param of the "add" method when adding the object to the assembly). 
                    - with the "loc" param, you can set the initial rotation of an object on any axis, at any angle and at any direction. 
                    - so just set an approximate initial orientation that somewhat aligns with the ultimate orientation you want to perform via the Axis constraint -> this will help you control the direction of the rotation that happens via the Axis constraint 
            - Supported shape types: A direction vector is extracted from the specified shape in three ways, depending on the shape's type.
                - Face: Normal of the face is selected as the direction vector
                - Edge (Circle or Arc): Normal of the plane where the circle or arc lie flat is selected as the direction vector
                - Edge (Not Circle or Arc): Tangent of the edge is selected as the direction vector 
            - Note: 
                - The most common use case is to define an Axis constraint from a Face.
                - It is frequently used to align faces and control the rotation of an object.

        "Plane":
            - Definition: Combination of both "Point" and "Axis" constraints.
                - Hence, it is both a translational and a rotational constraint.
                - It minimizes the distance between the center of mass of two objects while also rotating them so that the angle between their specified directional vectors match the angle specified in the "param" argument.
            - Param: Same as "Axis" constraint -> Desired angle between the two specified directional vectors in degrees (Default = 180)
                - You cannot define an offset in "Plane" constraint like how you can do in "Point" constraint using its "param" argument. The offset between the two object's center of mass is always 0 in "Plane".
            - Supported shape types: For "Point", the point is set at the shape's center of Mass. For Axis, a direction vector is extracted from the specified shape in three ways, depending on the shape's type.
                - Face: Normal of the face is selected as the direction vector
                - Edge (Circle or Arc): Normal of the plane where the circle or arc lie flat is selected as the direction vector
                - Edge (Not Circle or Arc): Tangent of the edge is selected as the direction vector 
            - Note:
                - "Plane" is most commonly used as a shortcut for "Point" and "Axis" constraints.

        "PointInPlane":
            - Definition: Positions the center of mass of the 1st object on the plane defined by the 2nd object.
                - This is a translational constraint; not a rotational one.
            - Param: offset displacement (not distance) from the second object's plane (Default = 0).
                - Positive param: offset 1st object's center in the direction towards the normal of 2nd object's plane.
                - Negative param: offset 1st object's center in the direction against the normal of 2nd object's plane. 
            - Supported shape types: 
                - 1st Object: ANY (Its center of mass is selected and used)
                - 2nd Object: Face OR an Edge/Wire that is a Circle or Arc
            - Note:
                - A single "PointInPlane" constraint will position the 1st object's center ANYWHERE on the 2nd object's PLANE (NOT THE FACE ITSELF).
                - Thus, if you want to position the 1st object at a specific point on a specific face of the 2nd object using "PointInPlane", a common approach is to create three "PointInPlane" constraints:
                    - The first constraint's 2nd object's plane should be the face where you want to position the 1st object. Its offset should be 0 (or it can be a positive value for tolerance). The 1st object in this case should be the face of the 1st object that you want to connect with the face of the 2nd object (the 1st object's face's center of mass is automatically selected).
                    - The second and third constraint's 2nd object's planes should be the faces perpendicular to that of the first constraint's. The offset of these constraints should be negative, which represents how far each constraint's 1st object's center should be from a particular side/ border of the 2nd object's face that was specified in the first constraint. You can provide distinct 1st objects to both of these constraints; ideally, for each of these constraints, you should select a particular geometry of the 1st object, the center of which you plan to use to measure/ set its distance from the respective 2nd object's plane/ face of each constraint.
                    - Lastly, use the "Axis" constraint to fix the orientation of the 1st object with respect to that of the 2nd object.
                - "PointInPlane" is a translational constraint (it gives no control over rotation). This is because it selects the CENTER POINT of the 1st object, which does not have a directional vector. 
                    - Thus, we should pass 1-3 "Axis" constraints with "PointInPlane" to control the orientation of the 1st object with respect to that of the 2nd object.

        "PointOnLine":
            - Definition: Positions the center of mass of the 1st object on the line defined by the 2nd object.
                - This is a translational constraint; not a rotational one.
            - Param: "PointOnLine" has a param but it is extremely unpredictable and unreliable, so don't use it! (Default = 0)
            - Supported shape types:
                - 1st Object: ANY (Its center of mass is selected and used)
                - 2nd Object: Linear Edge/Wire (Not a Circle or Arc) 
            - Note: 
                - A single "PointOnLine" constraint will position the 1st object's center ANYWHERE on the 2nd object's TANGENT (NOT THE EDGE ITSELF!).
                - Thus, a common use case of this constraint is to position the 1st object's center on the connection point of 2-3 edges/ lines of the 2nd object. This is done so by using 2-3 "PointOnLine" constraints on 2-3 interconnected edges/lines of the 2nd object. Basically, using multiple "PointOnLine" constraints positions the 1st object's center on the intersection point of all of the tangents defined by the 2nd object's edges, thus preventing the 1st object from being positioned at a random place anywhere on one particular tangent of the 2nd object.
                - Also, never use the param of "PointOnLine"
                - "PointOnLine" is a translational constraint (it gives no control over rotation). This is because it selects the CENTER POINT of the 1st object, which does not have a directional vector. 
                    - Thus, we should pass 1-3 "Axis" constraints with "PointOnLine" to control the orientation of the 1st object with respect to that of the 2nd object.

    Constraints Kinds (Fixed/ Absolute - Each object is independent in terms of their position/orientation)
        "FixedPoint"
            - Definition: Fixes the position of the provided object's center of mass to be equal to the given 3D point specified via the "param".
                - This locks ALL translational degrees of freedom of the object. But it doesn't lock any RDOF.
            - Param: Translational vector tuple (x,y,z)
                - It supports both positive and negative values, allowing you to move the object's center anywhere in the 3D space.
                - Note that this is a translational vector (not a directional vector).
            - Supported shape types: Any (the shape's center of mass is selected and used).
            - Note:
                - "FixedPoint" locks ALL translational degrees of freedom of the object. Hence, if you also perform a relative translational constraint (e.g. Point, PointInPlane, PointOnLine, etc) on this object and another object, then the translation will only apply to the other object (meaning the other object will move relative to the FixedPoint object); not the one that was already fixed via FixedPoint.
                - All rotational degrees of freedom of a "FixedPoint" object are free.

        "FixedAxis":
            - Definition: Fixes the orientation of the provided object's normal or tangent to be equal to the orientation of the directional vector specified via the "param"
                - It locks 2 RDOF, leaving only 1 free RDOF, which is the specified directional vector itself, meaning the object can spin around it.
            - Param: Directional vector tuple (x,y,z)
                - It supports both positive and negative values, allowing you to create any sort of directional vector.
                - Note that this is a directional vector (not a translational vector).
                    - Example: The directional vectors (1,1,0), (0.5,0.5,0) and (100,100,0) are all pointing in the same direction
            - Supported shape types:
                - Face: Normal of the face is selected for comparison with the directional vector.
                - Edge: Tangent of the edge is selected for comparison with the directional vector.
            - Note: 
                - Some common methods to create a directional vector:
                    - Manual: Just pick any (x,y,z) vector pointing in the desired direction.
                    - Automatic: Convert angle values to directional vectors using trigometry.
                        - Example 1: Create a planar directional vector on XY plane's 1st quadrant with an Angle of 30 degrees -> (cos(30), sin(30), 0)
                        - Example 2: Tilt Example 1's directional vector 45 degrees upward -> (cos(30)*cos(45), sin(30)*cos(45), sin(45))
                        - Example 3: Create a planar directional vector on YZ Plane's 1st quadrant with an angle of 40 degrees -> (0, cos(40), sin(40))
                        - This method supports all angle values (0-360 degrees) unlike the Axis constraint, which only supports 0-180 degrees.
                        - Clockwise (CW) and Counter-clockwise (CCW) direction of the rotation is controlled by the sign of the components of that vector.
                - Using "FixedAxis" on multiple places can cause issues though. So ideally, only use FixedAxis on one object in assembly that requires full 360 degree rotation with complete control over the rotation direction. Use "Axis" on other objects that require 180 degree rotation.
        
        "FixedRotation": 
            - Definition: Fixes the rotation of the provided object to be equal to the Euler angle values specified in the "param"
                - It locks ALL 3 RDOF.
            - Param: A tuple of Euler angle values in degrees (rx, ry, rz)
                - It supports both positive and negative values, meaning you have control over the clockwise and counter-clockwise direction for each of the 3 axis-based rotations.
                - Order: First, the rotation around the x-axis (rx) is applied. Then, the rotation around the y-axis (ry) is applied. Then, the rotation around the z-axis (rz) is applied. Each axis-based rotation affects the subsequent axis-based rotation.
            - Supported shape types: The object/ part itself (No shape geometry) - Bare Name
                - You only pass the entire part name (defined by the "name" param of Assembly API's "add" method) to "FixedRotation" without specific geometry selection. It doesn't focus on a specific geometry of the object; instead, it focuses on rotating the entire object as a whole. 

        "Fixed":
            - Definition: Locks all rotational and translational degrees of freedom of an object.
                - It has no "param" argument whatsoever.
            - Supported shape types: The object/ part itself (No shape geometry) - Bare Name
                - You only pass the entire part name (defined by the "name" param of Assembly API's "add" method) to "Fixed" without specific geometry selection.
            - Note:
                - Ideally, an assembly should have only one "Fixed" part, which acts as an anchor/ base for the other parts.
                    - Example: In a robotic arm assembly, the base part on which the entire robotic arm sits, should ideally be set as "Fixed"

    Note: 
        - If you don't call the "solve()" method after defining all the constraints, then none of the constraints will be applied!

    ── Best practices section ─────────────────────────────────────────────────────────────────────

    Best Practices:
        - Provide as many constraints as possible for every assembly part. Otherwise, the Assembly API will likely mess up their position/orientation.
        - Sometimes calling solver on a single object or constraint can raise an error, but not when there are two or more.
        - If you don't mark any child as "Fixed", the solver locks the first entity it encounters in the constraint list to prevent the whole system from floating freely in space. So it's good practice to explicitly "Fixed"-constrain at least one child node, or rely on the root node being the implicit anchor.
    
    Example: Basic simple robotic arm assembly
        1. Build Parts

        part7 — Motor mount for claw
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part7",
        "operations": [
            {"method": "box", "params": {"length": 20, "width": 15, "height": 10}},
            {"method": "edges", "args": [">Y and |Z"]},
            {"method": "chamfer", "args": [5]},
            {"method": "faces", "args": ["<Y"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "center", "args": [7.5, 0]},
            {"method": "hole", "args": [2]},
            {"method": "center", "args": [-15, 0]},
            {"method": "hole", "args": [2]},
            {"method": "center", "args": [7.5, 0]},
            {"method": "rect", "args": [10, 10]},
            {"method": "cutBlind", "args": [-10]},
            {"method": "faces", "args": ["-Y and (not <Y)"]},
            {"method": "edges", "args": ["|Z"]},
            {"method": "chamfer", "args": [2]}
        ]
        }

        part6 — Arm 2
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part6",
        "operations": [
            {"method": "box", "params": {"length": 30, "width": 80, "height": 30}},
            {"method": "faces", "args": [">X"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "center", "args": [25, 0]},
            {"method": "center", "args": [7.5, 0]},
            {"method": "hole", "args": [2]},
            {"method": "center", "args": [-15, 0]},
            {"method": "hole", "args": [2]},
            {"method": "faces", "args": ["<X"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "center", "args": [30, 0]},
            {"method": "hole", "args": [2]}
        ]
        }

        part5 — Motor mount 3
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part5",
        "operations": [
            {"method": "box", "params": {"length": 30, "width": 30, "height": 30}},
            {"method": "faces", "args": ["<X"]},
            {"method": "shell", "args": [2, "intersection"]},
            {"method": "faces", "args": [">X"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "hole", "args": [2]},
            {"method": "center", "args": [0, 0]},
            {"method": "rect", "args": [20, 20]},
            {"method": "vertices", "args": []},
            {"method": "hole", "args": [2, 2]}
        ]
        }

        part4 — Arm 1
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part4",
        "operations": [
            {"method": "box", "params": {"length": 100, "width": 30, "height": 30}},
            {"method": "faces", "args": ["<Y"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "center", "args": [-40, 0]},
            {"method": "hole", "args": [2, 10]},
            {"method": "center", "args": [75, 0]},
            {"method": "hole", "args": [2]},
            {"method": "center", "args": [0, 0]},
            {"method": "rect", "args": [20, 20]},
            {"method": "vertices", "args": []},
            {"method": "hole", "args": [2]}
        ]
        }

        part3 — Motor mount 2
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part3",
        "operations": [
            {"method": "box", "params": {"length": 30, "width": 30, "height": 30}},
            {"method": "faces", "args": [">X"]},
            {"method": "shell", "args": [2, "intersection"]},
            {"method": "faces", "args": ["<X"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "rect", "args": [20, 20]},
            {"method": "vertices", "args": []},
            {"method": "hole", "args": [2]},
            {"method": "faces", "args": ["<Z"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "rect", "args": [20, 20]},
            {"method": "vertices", "args": []},
            {"method": "hole", "args": [2, 2]},
            {"method": "faces", "args": ["<X"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "hole", "args": [2]}
        ]
        }

        part2 — Motor mount 1
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part2",
        "operations": [
            {"method": "box", "params": {"length": 25, "width": 25, "height": 40}},
            {"method": "faces", "args": [">Z"]},
            {"method": "shell", "args": [2, "intersection"]},
            {"method": "faces", "args": [">Z"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "rect", "args": [20, 20]},
            {"method": "vertices", "args": []},
            {"method": "hole", "args": [2]},
            {"method": "faces", "args": [">Z"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "hole", "args": [2]}
        ]
        }

        part1 — Base
        {
        "tool": "autonoma:workplane_api",
        "store_as": "part1",
        "operations": [
            {"method": "box", "params": {"length": 50, "width": 50, "height": 30}},
            {"method": "edges", "args": ["|Z"]},
            {"method": "chamfer", "args": [2.5]},
            {"method": "faces", "args": [">Z"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "rect", "args": [40, 40]},
            {"method": "vertices", "args": []},
            {"method": "cboreHole", "args": [3, 4, 2]},
            {"method": "faces", "args": [">Z"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "hole", "args": [3, 10]},
            {"method": "faces", "args": [">Z"]},
            {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
            {"method": "rect", "args": [20, 20]},
            {"method": "vertices", "args": []},
            {"method": "hole", "args": [2]}
        ]
        }


        2. Tag Connection Points

        p7h1 — part7's screw hole face center
        {
        "tool": "autonoma:workplane_api",
        "start_from": "part7",
        "store_as": "part7_tags",
        "operations": [
            {"method": "newObject", "args": [[{
            "_type": "Vertex",
            "point": {"_call": {
                "obj": {"_attr": {
                "obj": {"_run_on": {
                    "obj": {"_ref": "part7"},
                    "ops": [
                    {"method": "faces", "args": ["<Y"]},
                    {"method": "item", "args": [0]},
                    {"method": "workplane", "params": {"centerOption": "CenterOfMass"}}
                    ]
                }},
                "name": "plane"
                }},
                "method": "toWorldCoords",
                "args": [[0, 0]]
            }}
            }]]},
            {"method": "tag", "args": ["p7h1"]}
        ]
        }

        p6h1 — part6's arm1 connection hole (30mm from <X face center)
        {
        "tool": "autonoma:workplane_api",
        "start_from": "part6",
        "store_as": "part6_tags",
        "operations": [
            {"method": "newObject", "args": [[{
            "_type": "Vertex",
            "point": {"_call": {
                "obj": {"_attr": {
                "obj": {"_run_on": {
                    "obj": {"_ref": "part6"},
                    "ops": [
                    {"method": "faces", "args": ["<X"]},
                    {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
                    {"method": "center", "args": [30, 0]}
                    ]
                }},
                "name": "plane"
                }},
                "method": "toWorldCoords",
                "args": [[0, 0]]
            }}
            }]]},
            {"method": "tag", "args": ["p6h1"]}
        ]
        }

        p4h1 — part4's motor mount 2 connection hole (-40mm from <Y face center)
        {
        "tool": "autonoma:workplane_api",
        "start_from": "part4",
        "store_as": "part4_tags",
        "operations": [
            {"method": "newObject", "args": [[{
            "_type": "Vertex",
            "point": {"_call": {
                "obj": {"_attr": {
                "obj": {"_run_on": {
                    "obj": {"_ref": "part4"},
                    "ops": [
                    {"method": "faces", "args": ["<Y"]},
                    {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
                    {"method": "center", "args": [-40, 0]}
                    ]
                }},
                "name": "plane"
                }},
                "method": "toWorldCoords",
                "args": [[0, 0]]
            }}
            }]]},
            {"method": "tag", "args": ["p4h1"]}
        ]
        }

        p4h2 — part4's motor mount 3 connection hole (-35mm from >Y face center)
        {
        "tool": "autonoma:workplane_api",
        "start_from": "part4",
        "store_as": "part4_tags2",
        "operations": [
            {"method": "newObject", "args": [[{
            "_type": "Vertex",
            "point": {"_call": {
                "obj": {"_attr": {
                "obj": {"_run_on": {
                    "obj": {"_ref": "part4"},
                    "ops": [
                    {"method": "faces", "args": [">Y"]},
                    {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
                    {"method": "center", "args": [-35, 0]}
                    ]
                }},
                "name": "plane"
                }},
                "method": "toWorldCoords",
                "args": [[0, 0]]
            }}
            }]]},
            {"method": "tag", "args": ["p4h2"]}
        ]
        }

        p4h3 — part4's arm2 connection hole (+35mm net from <Y face center)
        {
        "tool": "autonoma:workplane_api",
        "start_from": "part4",
        "store_as": "part4_p4h3_fixed",
        "operations": [
            {"method": "newObject", "args": [[{
            "_type": "Vertex",
            "point": {"_call": {
                "obj": {"_attr": {
                "obj": {"_run_on": {
                    "obj": {"_ref": "part4"},
                    "ops": [
                    {"method": "faces", "args": ["<Y"]},
                    {"method": "workplane", "params": {"centerOption": "CenterOfMass"}},
                    {"method": "center", "args": [-40, 0]},
                    {"method": "hole", "args": [2, 10]},
                    {"method": "center", "args": [75, 0]}
                    ]
                }},
                "name": "plane"
                }},
                "method": "toWorldCoords",
                "args": [[0, 0]]
            }}
            }]]},
            {"method": "tag", "args": ["p4h3"]}
        ]
        }


        3. Build the Assembly

        {
        "tool": "autonoma:assembly_api",
        "store_as": "robotic_arm",
        "operations": [
            {"method": "add", "params": {
            "arg": {"_ref": "part1"}, "name": "part1",
            "color": {"_type": "Color", "name": "darkorange"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 0}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part2"}, "name": "part2",
            "color": {"_type": "Color", "name": "deepskyblue1"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 40}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part3"}, "name": "part3",
            "color": {"_type": "Color", "name": "mediumorchid"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 70}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part4"}, "name": "part4",
            "color": {"_type": "Color", "name": "gold"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 100, "rx": 30}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part5"}, "name": "part5",
            "color": {"_type": "Color", "name": "tomato"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 100}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part6"}, "name": "part6",
            "color": {"_type": "Color", "name": "limegreen"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 100}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part7"}, "name": "part7",
            "color": {"_type": "Color", "name": "lightcoral"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 150}
            }},

            {"method": "constrain", "args": ["part1", "Fixed"]},

            {"method": "constrain", "args": ["part1@faces@>Z", "part2@faces@<Z", "Point"]},
            {"method": "constrain", "args": ["part1@faces@>Z", "part2@faces@<Z", "Axis"], "kwargs": {"param": 180}},
            {"method": "constrain", "args": ["part2@faces@>X", "FixedAxis", {"_type": "Vector", "x": 1, "y": 0, "z": 0}]},

            {"method": "constrain", "args": ["part2@faces@>Z", "part3@faces@<Z", "Plane"]},
            {"method": "constrain", "args": ["part2@faces@>X", "part3@faces@<X", "Axis"]},
            {"method": "constrain", "args": ["part2@faces@>Y", "part3@faces@<Y", "Axis"]},

            {"method": "constrain", "args": ["part3@faces@<X", "part4?p4h1", "Point"]},
            {"method": "constrain", "args": ["part3@faces@<X", "part4@faces@<Y", "Axis"]},
            {"method": "constrain", "args": ["part3@faces@>Y", "part4@faces@>X", "Axis"], "kwargs": {"param": 30}},

            {"method": "constrain", "args": ["part4?p4h2", "part5@faces@>X", "Point"]},
            {"method": "constrain", "args": ["part4@faces@>Y", "part5@faces@>X", "Axis"]},
            {"method": "constrain", "args": ["part4@faces@>X", "part5@faces@>Y", "Axis"], "kwargs": {"param": 0}},

            {"method": "constrain", "args": ["part4?p4h3", "part6?p6h1", "Point"]},
            {"method": "constrain", "args": ["part4@faces@<Y", "part6@faces@>X", "Axis"], "kwargs": {"param": 0}},
            {"method": "constrain", "args": ["part4@faces@>X", "part6@faces@>Y", "Axis"], "kwargs": {"param": 30}},

            {"method": "constrain", "args": ["part6@faces@>X", "part7@faces@<Y", "Axis"]},
            {"method": "constrain", "args": ["part6@faces@>Y", "part7@faces@<X", "Axis"], "kwargs": {"param": 0}},
            {"method": "constrain", "args": ["part7@faces@<Y", "part6@faces@>X", "PointInPlane"]},
            {"method": "constrain", "args": ["part7@faces@<X", "part6@faces@>Y", "PointInPlane"], "kwargs": {"param": -5}},
            {"method": "constrain", "args": ["part7@faces@<Y", "part6@faces@<Z", "PointInPlane"], "kwargs": {"param": -15}},

            {"method": "solve", "params": {}}
        ]
        }
    
    ── Assembly API MCP communication guide ────────────────────────────────────────────────────────
    
    INIT:
        init_params (all optional — omit for an empty assembly):
        {
            "shape": {"_ref": "stored_shape"},
            "loc":   {"_type": "Location", "x":0, "y":0, "z":0},
            "name":  "my_assembly",
            "color": {"_type": "Color", "name": "steelblue"}
        }

        start_from: name of an existing stored Assembly to add parts into.
        store_as:   name to store the result under (auto-generated if omitted).

        Each operation: {"method": str, "params": dict} or {"method": str, "args": list}

    add:
        First param key is "arg" (the CadQuery object to add).

        {"method": "add", "params": {
            "arg":   {"_ref": "box_1"},
            "name":  "body",
            "color": {"_type": "Color", "name": "steelblue"}
        }}

        {"method": "add", "params": {
            "arg":  {"_ref": "pin_1"},
            "name": "pin",
            "loc":  {"_type": "Location", "x": 3, "y": 3, "z": 10}
        }}

        # sub-assembly:
        {"method": "add", "params": {"arg": {"_ref": "sub_assy_1"}, "name": "module_A"}}

    constrain:
        ALWAYS use the "args" (positional) form. "constrain" is a *args method; the "params" (keyword) form raises TypeError at runtime.

        Relative constraints — 2 query strings + kind string:
        {"method": "constrain", "args": ["body@faces@>Z", "pin@faces@<Z", "Plane"]}
        {"method": "constrain", "args": ["body@faces@>Z", "lid@faces@<Z", "Axis"]}
        {"method": "constrain", "args": ["body@faces@>Z", "pin@faces@<Z", "Point"]}
        {"method": "constrain", "args": ["pin@faces@<Z", "body@faces@>Z", "PointInPlane"]}

        Relative constraints with a non-default param — use "kwargs" for the keyword-only param:
        {"method": "constrain", "args": ["body@faces@>Z", "lid@faces@<Z", "Axis"],
         "kwargs": {"param": 90}}
        {"method": "constrain", "args": ["pin@faces@<Z", "slot@faces@>Z", "PointInPlane"],
         "kwargs": {"param": 2.5}}

        Fixed constraints — 1 query string + kind string (no param):
        {"method": "constrain", "args": ["body", "Fixed"]}

        Fixed constraints with param — param is the 3rd positional arg (NOT a keyword):
        {"method": "constrain", "args": ["part", "FixedPoint", [x, y, z]]}
        {"method": "constrain", "args": ["part@faces@>Z", "FixedAxis", [ax, ay, az]]}
        {"method": "constrain", "args": ["part", "FixedRotation", [rx, ry, rz]]}

    solve:
        {"method": "solve", "params": {}}
        {"method": "solve", "params": {"verbosity": 1}}

    export:
        Signature: export(path, exportType=None, mode='default', tolerance=0.1, angularTolerance=0.1, unit='MM', outputUnit=None, **kwargs)

        DEFAULT behaviour (use unless user explicitly requests otherwise):
          - path: C:\\Users\\...\\<filename>.step
          - exportType: "STEP"

        Standard STEP export (default — use this unless told otherwise):
        {"method": "export", "params": {"path": "C:\\Users\\...\\assy.step", "exportType": "STEP"}}

        glTF / GLB (only if user asks for web/viewer format):
        {"method": "export", "params": {"path": "C:\\Users\\...\\assy.gltf"}}
        {"method": "export", "params": {"path": "C:\\Users\\...\\assy.glb"}}

        Fused STEP (only if user asks for a single merged solid):
        {"method": "export", "params": {"path": "C:\\Users\\...\\assy.step", "exportType": "STEP", "mode": "fused"}}
        
        Note: Whenever the user asks to export something, ask him/ her to provide you an absolute file path where you should export the files. Remember to include double backslahes (\\) in that path rather than single ones (\\)!

        Supported exportType values: 'STEP', 'XML', 'XBF', 'GLTF', 'VTKJS', 'VRML', 'STL'

    toCompound:
         {"method": "toCompound", "params": {}}
        Converts the assembly to a single Compound; the result is re-stored.

     _expr IN DIRECTION VECTORS:
        Use {"_expr": "..."} inside Vector x/y/z to compute directions from angles.
        All trig functions in _expr use DEGREES (math.radians applied internally):
        cos(θ) = math.cos(math.radians(θ))
        sin(θ) = math.sin(math.radians(θ))

        Example — FixedAxis pointing 30° azimuth, 45° elevation:
        (FixedAxis passes the direction vector as the 3rd positional arg — no "kwargs" needed)
        {"method": "constrain", "args": [
            "PART",
            "FixedAxis",
            {"_type": "Vector",
             "x": {"_expr": "cos(30)*cos(45)"},
             "y": {"_expr": "sin(30)*cos(45)"},
             "z": {"_expr": "sin(45)"}}
        ]}

    _ref / _type in params:
        {"_ref": "name"}
        {"_type": "Vector", "x":1, "y":0, "z":0}
        {"_type": "Location", "x":5, "y":0, "z":0}
            → translation only; x/y/z default to 0
        {"_type": "Location", "x":0, "y":0, "z":0, "rx":0, "ry":0, "rz":45}
            → translation + rotation; rx/ry/rz are XYZ extrinsic Euler angles in degrees
        {"_type": "Color",    "name": "red"}
        {"_type": "Color",    "r":1.0, "g":0.0, "b":0.0, "a":1.0}
            → r/g/b/a in 0.0–1.0 range. 0–255 integers also accepted (auto-normalised).
        {"_type": "Vertex",   "x":0, "y":0, "z":0}

    RETURN:
        {"status":"success", "name":str, "obj_type":"Assembly",
         "properties": {"children":[str,...], "object_count":int, "volume":float, "center":[x,y,z]}}
    """
    try:
        if start_from:
            obj = _get(start_from)
        else:
            resolved = resolve_value(init_params or {})
            obj = Assembly(
                resolved.get("shape"),
                loc=resolved.get("loc"),
                name=resolved.get("name"),
                color=resolved.get("color"),
            )

        obj = _run(obj, operations)
        name = store_as or _auto_name("assembly")
        _store(name, obj)
        _show(obj)

        return json.dumps({"status": "success", "name": name,
                           "obj_type": _obj_type(obj), "properties": _properties(obj)})
    except Exception as e:
        return _error(str(e), traceback.format_exc())

# =============================================================================
# TOOL 4 — query_docs
# =============================================================================

import cadquery.selectors as _cq_selectors

_DOC_DEFAULTS = [Workplane, Sketch, Assembly]


def _resolve_cls(name: str):
    if name == "selectors":
        return _cq_selectors
    for module in (cq, _cq_selectors):
        obj = getattr(module, name, None)
        if isinstance(obj, type):
            return obj
    raise ValueError(f"Unknown CadQuery class: '{name}'")


def _doc_type(ann) -> str:
    if ann is inspect.Parameter.empty:
        return ""
    s = ann.__name__ if isinstance(ann, type) else str(ann)
    for prefix in ("typing.", "cadquery.occ_impl.geom.", "cadquery.occ_impl.shapes.",
                   "cadquery.assembly.", "cadquery.sketch.", "cadquery.cq.", "cadquery."):
        s = s.replace(prefix, "")
    return s


def _doc_sig(name, sig) -> str:
    parts = []
    for pn, p in sig.parameters.items():
        if pn == "self":
            continue
        chunk = f"{pn}: {_doc_type(p.annotation)}" if p.annotation is not inspect.Parameter.empty else pn
        if p.default is not inspect.Parameter.empty:
            d = repr(p.default)
            chunk += f" = {d[:27] + '...' if len(d) > 30 else d}"
        parts.append(chunk)
    return f"{name}({', '.join(parts)})"


def _doc_render(cls_name, name, method, sig) -> str:
    raw = inspect.getdoc(method)

    # Parse sphinx-style :param name: description
    pdocs, sum_lines, in_p, cur = {}, [], False, None
    for line in (raw or "").splitlines():
        s = line.strip()
        if s.startswith(":param "):
            in_p, rest = True, s[7:]
            if ": " in rest:
                cur = rest.split(": ")[0].strip().split()[-1]
                pdocs[cur] = rest.split(": ", 1)[1].strip()
        elif in_p and cur and s and not s.startswith(":"):
            pdocs[cur] += " " + s
        elif s.startswith(":"):
            in_p = False; cur = None
        elif not in_p:
            sum_lines.append(line)

    summary = "\n".join(sum_lines).strip().split("\n\n")[0] if sum_lines else ""
    out = [f"{cls_name}.{_doc_sig(name, sig)}",
           f"  {summary}" if summary else "  No description."]

    for pn, p in sig.parameters.items():
        if pn == "self":
            continue
        pt = _doc_type(p.annotation)
        if p.default is not inspect.Parameter.empty:
            dv = repr(p.default); dv = dv[:27] + "..." if len(dv) > 30 else dv
            dv_str = f" = {dv}"
        elif p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            dv_str = ""
        else:
            dv_str = " [required]"
        dc = pdocs.get(pn, pdocs.get(pn.lstrip("*"), ""))
        type_str = f": {pt}" if pt else ""
        desc_str = f" -- {dc}" if dc else ""
        out.append(f"  {pn}{type_str}{dv_str}{desc_str}")

    if raw:
        out.append(f"\n  Full docstring:\n{raw}")
    out.append("\n---")
    return "\n".join(out)


@mcp.tool(name="query_docs")
async def query_docs(methods: List[str], cls: Optional[str] = None) -> str:
    """
    Return detailed docs for specific methods.
    The docs includes param description, usecases, examples, etc.
    
    IMPORTANT: You MUST use this before executing methods that you don't 100% understand. It is ESSENTIAL to have 100% understanding of each method, its params and its usecases before execution! 

    methods: list of method names or callable names to look up.
             e.g. ["box", "fillet"] for Workplane methods,
             or ["BoxSelector", "TypeSelector"] when cls="selectors".

    cls:     which class/namespace to search. Examples:
               "Workplane", "Sketch", "Assembly"
               "Vector", "Plane", Location", "Color", "Matrix", "Vertex"
               "selectors" — use this to look up selector constructors:
                 BoxSelector, NearestToPointSelector, DirectionSelector,
                 ParallelDirSelector, PerpendicularDirSelector, TypeSelector,
                 RadiusNthSelector, LengthNthSelector, AreaNthSelector,
                 CenterNthSelector, DirectionNthSelector, DirectionMinMaxSelector,
                 AndSelector, SumSelector, SubtractSelector, InverseSelector, 
                 StringSyntaxSelector (default)
             Omit to search Workplane, Sketch, and Assembly.

    Returns plain-text docs per entry: signature, summary, params, full docstring.
    """
    # "Edge", "Wire", "Face", "Shell", "Solid", "Compound", "Shape" - add this once there is a direct_api tool
    
    if cls:
        try:
            classes = [(cls, _resolve_cls(cls))]
        except ValueError as e:
            return str(e)
    else:
        classes = [(k.__name__, k) for k in _DOC_DEFAULTS]
    results = []
    for display_name, klass in classes:
        for mname in methods:
            try:
                m = getattr(klass, mname, None)
                if not callable(m):
                    continue
                sig = inspect.signature(m)
            except Exception:
                continue
            results.append(_doc_render(display_name, mname, m, sig))

    return "\n".join(results) if results else f"No docs found for: {', '.join(methods)}"


# =============================================================================
# HTTP ROUTES & AUTH  (only used when MCP_TRANSPORT=http)
# =============================================================================
# These run on the same FastMCP app as the streamable-HTTP /mcp endpoint.
# Auth model: a single shared secret (MCP_TOKEN). The web backend sends it as
# a Bearer token; the browser never sees it. If MCP_TOKEN is unset (local/dev),
# requests are allowed so stdio / local testing is unaffected.

def _authorized(request) -> bool:
    expected = os.environ.get("MCP_TOKEN")
    if not expected:
        return True
    return request.headers.get("authorization") == f"Bearer {expected}"


@mcp.custom_route("/health", methods=["GET"])
async def _health(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/export", methods=["GET"])
async def _export(request):
    """Export the current stored object so the web backend can serve it as a
    download. Token-gated. ?fmt=step|stl|brep (default step)."""
    from starlette.responses import JSONResponse, FileResponse
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    fmt = request.query_params.get("fmt", "step").lower()
    ext = {"step": "step", "stp": "step", "stl": "stl", "brep": "brep"}.get(fmt, "step")
    try:
        obj = _get(None)  # current object
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"model.{ext}")
    try:
        cq.exporters.export(obj.val() if hasattr(obj, "val") else obj, path)
    except Exception as e:
        return JSONResponse({"error": f"export failed: {e}"}, status_code=500)
    return FileResponse(path, filename=f"model.{ext}")


# =============================================================================
# ENTRY POINT
# =============================================================================
# Default transport is stdio (local use + CI unchanged). Set MCP_TRANSPORT=http
# to serve the streamable-HTTP /mcp endpoint plus /health and /export.

class _AuthASGI:
    """Pure-ASGI auth wrapper. Gates only the /mcp endpoint; passes everything
    else (incl. lifespan + streaming) straight through so it never buffers the
    streamable-HTTP response."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http" and scope.get("path", "").startswith("/mcp"):
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"").decode()
            expected = os.environ.get("MCP_TOKEN")
            if expected and auth != f"Bearer {expected}":
                from starlette.responses import JSONResponse
                await JSONResponse({"error": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _run_http():
    import uvicorn
    mcp.settings.host = os.environ.get("MCP_HOST", "127.0.0.1")
    mcp.settings.port = int(os.environ.get("MCP_PORT", "9000"))
    app = _AuthASGI(mcp.streamable_http_app())
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)


if __name__ == "__main__":
    if os.environ.get("MCP_TRANSPORT", "stdio") == "http":
        _run_http()
    else:
        mcp.run(transport="stdio")
