"""
Text2CAD MCP Server.
Tools: workplane_api, sketch_api, assembly_api, query_docs, select_model
"""
# MCP server entry point

from typing import Any, Dict, List, Optional
from contextvars import ContextVar
from dataclasses import dataclass, field
# import atexit  # was only used by the disabled PostHog MCP analytics (see below)
import inspect
import json
import logging
import math
import os
import pickle
import re
import time
import traceback
import sys
import zlib

import anyio
from simpleeval import EvalWithCompoundTypes

# Port of the standalone ocp_vscode viewer (`python -m ocp_vscode`) used by the
# stdio viewer-push pipeline. NOTE: instantiating ViewerBackend(0) below calls
# set_port(0) and clobbers this, so _show_push re-asserts it before every show().
VIEWER_PORT = 3939

try:
    from ocp_vscode import show, set_port
    set_port(VIEWER_PORT)
    OCP_VIEWER_AVAILABLE = True
except ImportError:
    OCP_VIEWER_AVAILABLE = False
    show = None
    set_port = None

# Headless tessellation: _convert returns the {data, config} payload that the
# frontend's three-cad-viewer renders.
try:
    from ocp_vscode.show import _convert as _ocp_convert
    OCP_CONVERT_AVAILABLE = True
except Exception:
    OCP_CONVERT_AVAILABLE = False
    _ocp_convert = None


# Measurement backend (distance / properties tools). Forcing is_jupyter_cadquery
# makes its handlers RETURN responses instead of websocket-sending them, so we
# can expose them over HTTP via /backend. One backend is created PER SESSION
# (lazily) so each user's measurements act on their own model — see Session.
try:
    import ocp_vscode.backend as _ocp_backend_mod
    _ocp_backend_mod.is_jupyter_cadquery = True
    from ocp_vscode.comms import MessageType as _MessageType, default as _ocp_default
    from ocp_vscode.measure import get_properties as _get_properties
    from ocp_tessellate.ocp_utils import tq_to_loc as _tq_to_loc
    MEASURE_AVAILABLE = True
except Exception:
    _ocp_backend_mod = None
    _MessageType = None
    _ocp_default = None
    _get_properties = None
    _tq_to_loc = None
    MEASURE_AVAILABLE = False


def _new_measure_backend():
    """A fresh measurement backend for one session (None if unavailable)."""
    return _ocp_backend_mod.ViewerBackend(0) if MEASURE_AVAILABLE else None

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings

import cadquery as cq
from cadquery import (
    Workplane, Sketch, Assembly,
    Vector, Plane, Location, Matrix,
    Vertex, Edge, Wire, Face, Shell, Solid, Compound, Shape,
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

# STEP file reader → structured description, and direct-edit ops (work as a script and as the
# src package).
try:
    from src.step_import import (
        read_step, describe_shape, to_ap242, ensure_ap242_schema,
        SUPPORTED_IMPORT_EXTS, looks_like_step,
    )
    from src.direct_edit import apply_edits
except ImportError:
    from step_import import (
        read_step, describe_shape, to_ap242, ensure_ap242_schema,
        SUPPORTED_IMPORT_EXTS, looks_like_step,
    )
    from direct_edit import apply_edits

# =============================================================================
# EXTENSION PLUGINS  (gear generators + mechanical-part library)
# =============================================================================
# Optional parametric-part plugins layered ON TOP of the core API and surfaced to
# the model through the `extension_api` tool. One plugin is purely additive (it adds
# a Workplane.gear() builder). The other ships a large mechanical-part library and,
# as a side effect of import, monkeypatches ~40 core Shape methods. Only ONE of those
# overrides misbehaves on this build's geometry kernel — Shape.copy:
#   • the native copy rebuilds via self.__class__(...): fine for plain/gear geometry,
#     but can't reconstruct the library's parts (they have custom constructors).
#   • the library's copy (a serialize/deserialize deep-copy) handles its own parts,
#     but corrupts complex gear geometry.
# So we install a type-aware dispatcher that routes each shape to the copy that works
# for it. With that in place both plugins coexist and core behaviour stays identical
# to stock (verified: an ops battery is byte-for-byte the same with/without the load).

_EXT_BASE_SHAPES = {Shape, Solid, Face, Wire, Edge, Vertex, Compound, Shell}
_EXT_CATALOG: Dict[str, Dict[str, Any]] = {}   # part name -> {"cls": type, "family": str}
EXT_AVAILABLE = False


def _register_ext(cls: type, family: str) -> None:
    _EXT_CATALOG[cls.__name__] = {"cls": cls, "family": family}


def _scan_ext(module: Any, base: type, family: str, origin: str) -> None:
    """Register every concrete public part class in `module` that subclasses `base`.
    Introspection-driven so new library classes are picked up automatically."""
    for name in dir(module):
        if name.startswith("_"):
            continue
        obj = getattr(module, name)
        if (isinstance(obj, type) and issubclass(obj, base) and obj is not base
                and not inspect.isabstract(obj)
                and (getattr(obj, "__module__", "") or "").startswith(origin)):
            _register_ext(obj, family)


def _load_extension_plugins() -> None:
    """Import the plugins, install the copy dispatcher, and build the part catalog.
    Guarded: if the plugins aren't installed the core server still runs normally."""
    global EXT_AVAILABLE
    _native_copy = Shape.copy
    try:
        import cq_warehouse.extensions          # noqa: F401  (import applies the monkeypatches)
        import cq_warehouse.fastener as _fa
        import cq_warehouse.bearing as _be
        import cq_warehouse.thread as _th
        import cq_warehouse.sprocket as _sp
        import cq_warehouse.chain as _ch
        import cq_warehouse.drafting as _dr
        import cq_gears as _gears
        import heatserts                       # noqa: F401  (import adds Workplane.heatsert)
    except Exception as e:  # plugins are optional
        print(f"[t2c] extension plugins unavailable: {e}", file=sys.stderr, flush=True)
        return

    _cqw_copy = Shape.copy                       # the library's deep-copy (post-import)

    def _dispatch_copy(self, mesh: bool = False):
        # base + gear geometry -> native copy; library custom-ctor parts -> library copy
        return (_native_copy if type(self) in _EXT_BASE_SHAPES else _cqw_copy)(self, mesh)

    for _c in _EXT_BASE_SHAPES:
        _c.copy = _dispatch_copy

    _GearBase = next(b for b in _gears.SpurGear.__mro__ if b.__name__ == "GearBase")
    _scan_ext(_gears, _GearBase, "gear", "cq_gears")
    for _base in (_fa.Screw, _fa.Nut, _fa.Washer):
        _scan_ext(_fa, _base, "fastener", "cq_warehouse")
    _scan_ext(_be, _be.Bearing, "bearing", "cq_warehouse")
    # Thread classes (IsoThread, AcmeThread, …) subclass Solid directly, not a shared
    # Thread base, so key off the module origin instead.
    _scan_ext(_th, Shape, "thread", "cq_warehouse.thread")
    _register_ext(_sp.Sprocket, "sprocket")
    _register_ext(_ch.Chain, "chain")
    # Drafting is config + method, not a standalone part: register the annotation
    # OPERATIONS (each builds a Draft then returns a real dimension/callout assembly)
    # rather than the bare Draft config, which would render nothing.
    for _method in ("dimension_line", "extension_line", "callout"):
        _EXT_CATALOG[_method] = {"cls": _dr.Draft, "family": "drafting", "method": _method}
    EXT_AVAILABLE = True


_load_extension_plugins()

# =============================================================================
# SERVER
# =============================================================================

mcp = FastMCP(
    name="parametric_text2cad_mcp",
    instructions=(
        "Dedicated parametric CAD tools:\n"
        "  • workplane_api  — 3D modeling via Workplane API method chaining\n"
        "  • sketch_api     — 2D profiles via Sketch API (face or edge workflows)\n"
        "  • assembly_api   — multi-part assemblies via Assembly API add/constrain/solve\n"
        "  • extension_api  — build ready-made parametric parts: gears, fasteners, bearings, threads, sprockets, chains\n"
        "  • select_model   — re-activate an earlier model by name (shows it in the viewer and makes it exportable)\n"
        "  • query_docs     — fetch official detailed docs of specific methods and their parameters\n"
        "  • report_learning — privately tell the developers something you learned that could improve this assistant\n\n"
        "All tools share a persistent object store. Reference stored objects with "
        "{\"_ref\": \"name\"} and construct types inline with "
        "{\"_type\": \"Vector\"|\"Plane\"|\"Location\"|\"Color\", ...}.\n\n"
        "Typical workflow:\n"
        "  1. workplane_api → create and store parts\n"
        "  2. sketch_api    → create 2D profiles, use in workplane_api via placeSketch\n"
        "  3. assembly_api  → combine stored parts with constraints"
        "  4. query_docs    → fetch official detailed docs for specific methods causing error or confusion"
    ),
    # Disable DNS-rebinding protection: the server is reached via a public host
    # (tunnel / Render) and is protected by the MCP_TOKEN bearer check instead.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)

# =============================================================================
# STATE  —  one isolated Session per user/chat (multi-user)
# =============================================================================
# Every request carries a session id (the `X-Session-Id` header on /mcp, or the
# `?session=` query param on the viewer routes). All CAD objects, the tessellated
# viewer payload, and the measurement backend live on that session — never in
# module globals — so concurrent users never see each other's models. Requests
# without an id (stdio / local dev) fall back to a single shared "local" session.

_LOCAL_SID = "local"
_SESSION_TTL = float(os.environ.get("SESSION_TTL_SECONDS", 3600))  # evict idle sessions


@dataclass
class Session:
    state: Dict[str, Any] = field(default_factory=dict)       # name → CadQuery object
    counters: Dict[str, int] = field(default_factory=dict)    # auto-naming counters
    current: Optional[str] = None                             # active object name
    auto_names: set = field(default_factory=set)              # names we auto-generated (vs explicit store_as)
    rev: int = 0                                              # bumps on every CAD-state mutation
    exported_rev: int = -1                                    # rev at last /session/export (skip re-export when equal)
    # Latest tessellated model the browser polls (/model serves payload; /version
    # lets it detect changes and reports obj_type for 2D-only export formats).
    viewer: Dict[str, Any] = field(
        default_factory=lambda: {"payload": None, "version": 0, "obj_type": None})
    _measure: Any = None                                      # lazy measurement backend
    last_used: float = field(default_factory=time.monotonic)

    @property
    def measure_backend(self):
        if self._measure is None:
            self._measure = _new_measure_backend()
        return self._measure


_sessions: Dict[str, Session] = {}
# The session bound to the current request; set at each tool/route entry. anyio
# copies it into the worker thread, so offloaded CAD work sees the right session.
_cur_session: ContextVar[Optional[Session]] = ContextVar("cur_session", default=None)


def _evict_idle() -> None:
    now = time.monotonic()
    stale = [sid for sid, s in _sessions.items() if s.last_used < now - _SESSION_TTL]
    for sid in stale:
        _sessions.pop(sid, None)


def _get_session(sid: Optional[str]) -> Session:
    sid = sid or _LOCAL_SID
    _evict_idle()
    sess = _sessions.get(sid)
    if sess is None:
        sess = _sessions[sid] = Session()
        # Seed the placeholder so the viewer (grid + tools + empty scene) is always
        # visible for every session, even before the LLM builds a model.
        _init_viewer(sess)
    sess.last_used = time.monotonic()
    return sess


def _sess() -> Session:
    """The session for the current request (creating the local one if unbound)."""
    sess = _cur_session.get()
    if sess is None:
        sess = _get_session(_LOCAL_SID)
        _cur_session.set(sess)
    return sess


def _bind(sid: Optional[str]) -> Session:
    """Bind the request's session so _store/_get/_show operate on it."""
    sess = _get_session(sid)
    _cur_session.set(sess)
    return sess


def _sid_from_ctx(ctx) -> Optional[str]:
    """The X-Session-Id header from the live HTTP request (None over stdio)."""
    try:
        return ctx.request_context.request.headers.get("x-session-id")
    except Exception:
        return None


def _store(name: str, obj: Any) -> None:
    sess = _sess()
    sess.state[name] = obj
    sess.current = name
    sess.rev += 1  # CAD state changed → snapshot is now stale (see /session/export)


def _get(name: Optional[str]) -> Any:
    sess = _sess()
    target = name or sess.current
    if not target:
        raise ValueError("No object name given and no current object set")
    if target not in sess.state:
        raise ValueError(f"Object '{target}' not found")
    return sess.state[target]


def _auto_name(prefix: str) -> str:
    sess = _sess()
    c = sess.counters
    c[prefix] = c.get(prefix, 0) + 1
    name = f"{prefix}_{c[prefix]}"
    sess.auto_names.add(name)  # mark as auto so snapshots can prune stale intermediates
    return name


# --- Durable snapshots ---------------------------------------------------------
# A session's whole object store pickles with full fidelity: CadQuery shapes
# serialize via OCCT BinTools, and Workplane/Sketch/Assembly (with colors,
# locations, hierarchy) all round-trip. So a chat's CAD state can be saved to the
# DB and restored after a server restart. The blob is produced AND consumed only
# by this backend, so unpickling is trusted (never fed arbitrary user input).
def _iter_assemblies(obj: Any):
    """Yield an Assembly and all its nested Assembly children."""
    if isinstance(obj, Assembly):
        yield obj
        for c in getattr(obj, "children", []) or []:
            yield from _iter_assemblies(c)


# How many recent auto-named objects to keep in the durable snapshot beyond the
# active + explicitly-named models. A recency window protects fresh intermediates
# the agent may still reference next turn while shedding old superseded ones.
SNAPSHOT_KEEP_RECENT = int(os.environ.get("SNAPSHOT_KEEP_RECENT", 10))


def _prune_for_snapshot(sess: "Session") -> Dict[str, Any]:
    """The subset of the object store worth persisting. Keeps: the active model
    (viewer + resume anchor — never dropped), every explicitly-named model (the
    agent references these by name), and the most recently created objects (recency
    window). Superseded older auto-named intermediates are dropped from the DURABLE
    copy only — the live session keeps everything, so nothing is lost mid-chat."""
    names = list(sess.state.keys())  # dict preserves creation order
    keep = {n for n in names if n not in sess.auto_names}   # explicit store_as names
    if sess.current in sess.state:
        keep.add(sess.current)                              # active model — always
    keep.update(names[-SNAPSHOT_KEEP_RECENT:])              # recency window
    return {n: sess.state[n] for n in names if n in keep}   # keep creation order


def _snapshot(sess: "Session") -> bytes:
    objects = _prune_for_snapshot(sess)
    # A solved Assembly caches an OCCT solver result (`_solve_result`) holding a
    # non-picklable SwigPyObject. It's just solver metadata — solve() regenerates
    # it and the solved child locations are already baked in — so strip it for the
    # dump and restore it on the live objects afterward. Constraints (picklable)
    # are kept, so a restored assembly can still be re-solved.
    stripped = []
    for obj in objects.values():
        for a in _iter_assemblies(obj):
            sr = getattr(a, "_solve_result", None)
            if sr is not None:
                a._solve_result = None
                stripped.append((a, sr))
    try:
        raw = pickle.dumps(
            {"counters": dict(sess.counters), "current": sess.current, "objects": objects,
             "auto_names": [n for n in sess.auto_names if n in objects]},
            protocol=pickle.HIGHEST_PROTOCOL,
        )
        # Deflate: BREP/pickle geometry is highly redundant, so this shrinks the
        # stored blob several-fold (keeps DB rows small on the free tier).
        return zlib.compress(raw, 9)
    finally:
        for a, sr in stripped:
            a._solve_result = sr


def _recast_shape(s: Any) -> Any:
    """Downcast a restored shape: OCCT BinTools.Read returns a generic TopoDS_Shape
    (not TopoDS_Solid/Compound/…), which the tessellator's type check rejects.
    cq.Shape.cast reads the real ShapeType and re-wraps it correctly."""
    if isinstance(s, cq.Shape) and s.wrapped is not None:
        try:
            return cq.Shape.cast(s.wrapped)
        except Exception:
            return s
    return s


def _normalize(obj: Any) -> Any:
    """Make a restored object display-ready by recasting its shapes (see above).
    Geometry/modeling already work on the raw restored object; this fixes the
    viewer tessellation for Workplanes and Assemblies (Sketches are unaffected)."""
    if isinstance(obj, Workplane):
        shapes = [_recast_shape(o) for o in obj.objects if isinstance(o, cq.Shape)]
        if not shapes:
            return obj
        plane = getattr(obj, "plane", None)
        return (Workplane(plane) if plane is not None else Workplane()).newObject(shapes)
    if isinstance(obj, Sketch):
        f = getattr(obj, "_faces", None)
        if isinstance(f, cq.Shape) and f.wrapped is not None:
            obj._faces = _recast_shape(f)
        return obj
    if isinstance(obj, Assembly):
        def _rec(a):
            if getattr(a, "obj", None) is not None:
                a.obj = _normalize(a.obj)
            for c in getattr(a, "children", []) or []:
                _rec(c)
        _rec(obj)
        return obj
    if isinstance(obj, cq.Shape):
        return _recast_shape(obj)
    return obj


def _restore_into(sess: "Session", data: bytes) -> int:
    # New snapshots are zlib-compressed; fall back to raw for any legacy blob.
    try:
        data = zlib.decompress(data)
    except zlib.error:
        pass
    d = pickle.loads(data)
    objs = d.get("objects", {}) or {}
    sess.state = {name: _normalize(o) for name, o in objs.items()}
    sess.counters = d.get("counters", {}) or {}
    sess.current = d.get("current")
    # Restore auto-name tracking (legacy blobs lack it → treat survivors as explicit).
    sess.auto_names = set(d.get("auto_names", [])) & set(sess.state)
    # Restored state == what's already stored, so mark clean: no re-export until a
    # real mutation. (The route re-shows current for the viewer; that's not a change.)
    sess.rev = 0
    sess.exported_rev = 0
    return len(sess.state)

# =============================================================================
# REFERENCE & TYPE RESOLUTION
# =============================================================================

# -----------------------------------------------------------------------------
# Sandboxed expression evaluation (simpleeval, AST-whitelisted).
#
#   {"_expr": "..."}  → one number. Trig is in DEGREES (legacy convention).
#   {"_func": {...}}  → a Python callable f(*params) for CadQuery methods that
#                       take a lambda (parametricCurve / parametricSurface).
#                       Trig is in RADIANS (standard maths convention).
#
# simpleeval whitelists AST nodes, so it blocks attribute access, imports, and
# oversized powers — AI-authored strings cannot escape the sandbox the way a
# bare eval() can.
# -----------------------------------------------------------------------------

# Degree-based trig for {"_expr": ...}: angle args in degrees, inverse trig
# returns degrees.
_EXPR_FUNCS: Dict[str, Any] = {
    "cos":   lambda x: math.cos(math.radians(x)),
    "sin":   lambda x: math.sin(math.radians(x)),
    "tan":   lambda x: math.tan(math.radians(x)),
    "acos":  lambda x: math.degrees(math.acos(x)),
    "asin":  lambda x: math.degrees(math.asin(x)),
    "atan":  lambda x: math.degrees(math.atan(x)),
    "atan2": lambda y, x: math.degrees(math.atan2(y, x)),
    "sqrt":  math.sqrt,
    "abs":   abs,
    "pow":   pow,
    "ceil":  math.ceil,
    "floor": math.floor,
}
_EXPR_CONSTS: Dict[str, Any] = {"pi": math.pi, "e": math.e}

# Standard radians maths for {"_func": ...} parametric formulas.
_FUNC_FUNCS: Dict[str, Any] = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "exp": math.exp, "log": math.log, "log10": math.log10,
    "sqrt": math.sqrt, "pow": pow, "hypot": math.hypot,
    "floor": math.floor, "ceil": math.ceil, "abs": abs,
    "min": min, "max": max, "round": round,
    "degrees": math.degrees, "radians": math.radians,
}
_FUNC_CONSTS: Dict[str, Any] = {"pi": math.pi, "tau": math.tau, "e": math.e}


def _safe_eval_expr(expr: str) -> float:
    """Evaluate an {"_expr"} string to a number (degree-based namespace)."""
    ev = EvalWithCompoundTypes(functions=_EXPR_FUNCS, names=dict(_EXPR_CONSTS))
    return ev.eval(expr)


def _make_func(spec: dict):
    """Build a Python callable from {"_func": {"params": [...], "expr": "..."}}.

    The expression is evaluated in the radians maths namespace with each call's
    positional arguments bound to `params` (default ["t"]). A list/tuple result
    such as "[x, y, z]" is returned as a tuple so CadQuery can wrap it in a
    Vector. Powers parametricCurve (f(t)) and parametricSurface (f(u, v))."""
    params = spec.get("params") or ["t"]
    expr   = spec["expr"]
    ev     = EvalWithCompoundTypes(functions=_FUNC_FUNCS, names=dict(_FUNC_CONSTS))
    node   = ev.parse(expr)  # validate once; raises on bad syntax

    def _fn(*args):
        ev.names = {**_FUNC_CONSTS, **dict(zip(params, args))}
        out = ev.eval(expr, previously_parsed=node)
        if not isinstance(out, (list, tuple)):
            raise ValueError(
                f"_func expr {expr!r} must return [x, y] or [x, y, z], got {out!r}")
        coords = tuple(out)
        if len(coords) not in (2, 3) or not all(
                isinstance(c, (int, float)) for c in coords):
            raise ValueError(
                f"_func expr {expr!r} must return 2 or 3 numbers, got {coords!r}")
        return coords

    return _fn


def resolve_value(value: Any) -> Any:
    """Resolve {"_ref": name} → stored object, {"_type": ...} → CadQuery type,
    {"_attr": ...} → attribute access, {"_call": ...} → method call,
    {"_run_on": ...} → run ops on object without storing,
    {"_func": ...} → sandboxed callable f(*params), {"_expr": ...} → number."""
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
        elif "_func" in value:
            return _make_func(value["_func"])
        elif "_expr" in value:
            return float(_safe_eval_expr(value["_expr"]))
        else:
            return {k: resolve_value(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_value(v) for v in value]
    return value


# =============================================================================
# STUDIO MATERIALS
# =============================================================================
# A part's material is the name of one of the viewer's built-in Studio materials.
# Passed as a plain-string tag on the part, it routes through the viewer's reliable
# `createStudioMaterial` path. (Do NOT feed a threejs_materials.PbrProperties: that
# takes the viewer's MaterialX path, which renders opaque non-metals with a black
# albedo.) These 29 names are the viewer's full built-in catalogue.
_VIEWER_BUILTINS = {
    # metals (keep their own tuned tint)
    "chrome", "polished-steel", "polished-aluminum", "gold", "copper", "brass",
    "stainless-steel", "brushed-aluminum", "cast-iron", "titanium", "galvanized",
    # opaque non-metals (recoloured per part — see _LEAF_COLOR_BUILTINS)
    "plastic-glossy", "plastic-matte", "abs-black", "nylon", "rubber-black",
    "rubber-gray", "rubber-red", "paint-matte", "paint-glossy", "paint-metallic",
    "car-paint", "ceramic-white", "concrete", "carbon-fiber",
    # transparent (keep their own tuned look)
    "acrylic-clear", "glass-clear", "glass-tinted", "glass-frosted",
}

# Opaque non-metals: the part's OWN colour (its `add` colour) becomes the hue, while
# the builtin supplies the finish. Emitted as a "builtin:<name>" tag that
# _inject_builtin_materials turns into a colour-stripped appearance entry so the
# viewer falls back to the part's CAD colour. Metals/glass keep their builtin look.
_LEAF_COLOR_BUILTINS = {
    "plastic-glossy", "plastic-matte", "abs-black", "nylon", "rubber-black",
    "rubber-gray", "rubber-red", "paint-matte", "paint-glossy", "paint-metallic",
    "car-paint", "ceramic-white", "concrete",
}

# Friendly aliases so natural words also resolve to a builtin.
_MATERIAL_ALIASES = {
    "steel": "polished-steel", "polished_steel": "polished-steel",
    "stainless": "stainless-steel", "stainless_steel": "stainless-steel",
    "aluminum": "polished-aluminum", "aluminium": "polished-aluminum",
    "polished_aluminum": "polished-aluminum", "silver": "polished-aluminum",
    "brushed_aluminum": "brushed-aluminum", "cast_iron": "cast-iron",
    "plastic": "plastic-matte", "matte_plastic": "plastic-matte",
    "glossy_plastic": "plastic-glossy", "abs": "abs-black", "abs_plastic": "abs-black",
    "black_plastic": "abs-black", "rubber": "rubber-black", "matte_black": "paint-matte",
    "paint": "paint-glossy", "matte_paint": "paint-matte", "glossy_paint": "paint-glossy",
    "metallic_paint": "paint-metallic", "car_paint": "car-paint",
    "ceramic": "ceramic-white", "carbon_fiber": "carbon-fiber",
    "glass": "glass-clear", "frosted_glass": "glass-frosted", "tinted_glass": "glass-tinted",
    "acrylic": "acrylic-clear", "clear_plastic": "acrylic-clear",
}

try:
    import webcolors as _webcolors
except Exception:
    _webcolors = None


def _named_color(name: str) -> "Color":
    """Resolve a colour name to a CadQuery Color. CadQuery/OCCT only knows X11 names
    (red, steelblue, gray90, …); fall back to the full CSS palette (crimson, silver,
    navy, darkblue, …) so common colour names the model reaches for just work."""
    try:
        return Color(name)
    except Exception:
        pass
    if _webcolors is not None:
        try:
            r, g, b = _webcolors.name_to_rgb(name.strip().replace(" ", "").lower())
            return Color(r / 255.0, g / 255.0, b / 255.0)
        except ValueError:
            pass
    raise ValueError(f"Unknown colour name '{name}'. Use a CSS/X11 colour name or {{\"r\",\"g\",\"b\"}}.")


# =============================================================================
# TEXTURED MATERIALS
# =============================================================================
# Real surface detail (wood grain, brushed metal, fabric, stone …) via Poly Haven
# CC0 texture sets, referenced BY URL (loaded straight from Poly Haven's CDN, not
# embedded in the payload) and projected onto the model with the viewer's triplanar
# mapping (no UVs needed on CAD geometry). Maps: diff (colour), nor_gl (normal),
# rough (roughness), ao. Missing maps degrade gracefully (the viewer skips them).
_TEXTURE_BASE = "https://dl.polyhaven.org/file/ph-assets/Textures/jpg/1k"

# category → (Poly Haven slug, metalness). Slugs verified to exist.
_TEXTURE_CATALOG = {
    "wood":             ("wood_table_001", 0.0),
    "oak":              ("oak_veneer_01", 0.0),
    "wood_laminate":    ("laminate_floor_02", 0.0),
    "marble":           ("marble_01", 0.0),
    "concrete":         ("concrete_wall_008", 0.0),
    "concrete_floor":   ("concrete_floor_worn_001", 0.0),
    "brick":            ("brick_wall_006", 0.0),
    "cobblestone":      ("cobblestone_floor_04", 0.0),
    "stone":            ("gray_rocks", 0.0),
    "rubber":           ("rubber_tiles", 0.0),
    "fabric":           ("denim_fabric", 0.0),
    "leather":          ("leather_white", 0.0),
    "metal_plate":      ("metal_plate_02", 1.0),
    "blue_metal":       ("blue_metal_plate", 1.0),
    "corrugated_metal": ("corrugated_iron_02", 1.0),
    "rusty_metal":      ("rusty_metal_02", 0.6),
}
_TEXTURE_ALIASES = {
    "wood_grain": "wood", "timber": "wood", "planks": "oak", "laminate": "wood_laminate",
    "denim": "fabric", "cloth": "fabric", "textile": "fabric", "tile": "cobblestone",
    "stone_tiles": "cobblestone", "rock": "stone", "brushed_metal": "metal_plate",
    "galvanized_metal": "corrugated_metal", "rust": "rusty_metal", "granite": "stone",
}


def _texture_appearance(tag: str) -> dict:
    """Build a viewer appearance object (reliable createStudioMaterial path) with
    Poly Haven texture-map URLs from a "tex:<metalness>:<slug>" tag."""
    _, metalness, slug = tag.split(":", 2)
    url = lambda suffix: f"{_TEXTURE_BASE}/{slug}/{slug}_{suffix}_1k.jpg"
    return {
        "builtin": "plastic-matte",   # carrier; overridden below
        "color": [1.0, 1.0, 1.0],     # show the texture's own colour, untinted
        "metalness": float(metalness),
        "roughness": 1.0,             # let the roughness map drive it fully
        "map": url("diff"),
        "normalMap": url("nor_gl"),
        "roughnessMap": url("rough"),
        "aoMap": url("ao"),
    }


def _build_material(spec: dict) -> str:
    """Resolve a {"_type": "Material"} spec to a viewer material tag (a str).

    The tag is applied to the assembly child directly (see _run) rather than via
    Assembly.add(material=...), which would wrap a str into a CadQuery Material.
    """
    # Textured material: {"_type": "Material", "texture": "<category|slug>"}
    tex = spec.get("texture")
    if tex is not None:
        cat = _TEXTURE_ALIASES.get(tex, tex)
        if cat in _TEXTURE_CATALOG:
            slug, metalness = _TEXTURE_CATALOG[cat]
        else:
            slug, metalness = cat, (1.0 if spec.get("metal") else 0.0)  # raw Poly Haven slug
        return f"tex:{metalness}:{slug}"

    name = spec.get("preset") or spec.get("builtin") or spec.get("name")
    if not name:
        raise ValueError('Material spec needs a "preset" or "texture" — a material name.')
    tag = _MATERIAL_ALIASES.get(name, name)
    if tag not in _VIEWER_BUILTINS:
        raise ValueError(
            f"Unknown material '{name}'. Available: {sorted(_VIEWER_BUILTINS)}; "
            f"aliases: {sorted(_MATERIAL_ALIASES)}; textures: {sorted(_TEXTURE_CATALOG)}."
        )
    # Opaque non-metals are recoloured from the part's own colour.
    return f"builtin:{tag}" if tag in _LEAF_COLOR_BUILTINS else tag


def _inject_studio_materials(payload: dict) -> None:
    """Turn our marker material tags into the viewer's materials-map appearance
    entries: "builtin:<name>" → part-coloured builtin; "tex:<m>:<slug>" → textured
    appearance with Poly Haven map URLs. Both take the reliable createStudioMaterial
    path (no black-albedo MaterialX bug)."""
    try:
        shapes = payload["data"]["shapes"]
    except (KeyError, TypeError):
        return
    tags: set = set()

    def walk(node):
        if not isinstance(node, dict):
            return
        mat = node.get("material")
        if isinstance(mat, str) and (mat.startswith("builtin:") or mat.startswith("tex:")):
            tags.add(mat)
        for child in node.get("parts", []) or []:
            walk(child)

    walk(shapes)
    if not tags:
        return
    mats = shapes.setdefault("materials", {})
    for tag in tags:
        if tag.startswith("builtin:"):
            mats.setdefault(tag, {"builtin": tag[len("builtin:"):]})
        else:
            mats.setdefault(tag, _texture_appearance(tag))


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
            return _named_color(spec["name"])
        r, g, b, a = spec.get("r", 0), spec.get("g", 0), spec.get("b", 0), spec.get("a", 1)
        # Accept either 0–255 integers or 0.0–1.0 floats; normalise the former.
        if max(r, g, b) > 1:
            r, g, b = r / 255.0, g / 255.0, b / 255.0
        return Color(r, g, b, a)

    # ── Material (PBR) ────────────────────────────────────────────────────────
    elif t == "Material":
        return _build_material(spec)

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

    # ── Extension-plugin parts (gears, fasteners, bearings, threads, …) ────────
    # Example: {"_type": "SpurGear", "params": {"module": 1, "teeth_number": 20,
    #           "width": 5, "bore_d": 5}, "plane": "XY"}
    elif t in _EXT_CATALOG:
        return _build_ext_part(spec)

    raise ValueError(f"Unknown _type: '{t}'")


def _build_ext_part(spec: dict) -> Any:
    """Construct a catalog part and normalise it to a native object the rest of the
    pipeline stores/renders. spec = {"_type": <part>, "params": {...}, "plane"?: str}.
    Gears build through the plugin's Workplane.gear() builder; drafting ops build a
    dimension/callout assembly; every other part is already (or exposes via
    .cq_object) a native shape/assembly."""
    entry = _EXT_CATALOG[spec["_type"]]
    params = resolve_value(spec.get("params", {})) or {}
    if entry.get("method"):                       # drafting annotation op
        return _build_drafting(entry["cls"], entry["method"], params)
    obj = entry["cls"](**params)
    if entry["family"] == "gear":
        return cq.Workplane(spec.get("plane", "XY")).gear(obj)
    if isinstance(obj, (Workplane, Sketch, Assembly, Shape)):
        return obj
    if hasattr(obj, "cq_object"):
        return obj.cq_object
    return obj


def _draft_config_keys(draft_cls: type) -> set:
    return set(inspect.signature(draft_cls.__init__).parameters) - {"self"}


def _build_drafting(draft_cls: type, method: str, params: dict) -> Any:
    """Build a Draft from the config-subset of params (font_size, units, …), then call
    the requested annotation method (dimension_line/extension_line/callout) with the
    rest. Returns a real assembly, so it renders and never pushes an empty scene."""
    cfg_keys = _draft_config_keys(draft_cls)
    cfg = {k: v for k, v in params.items() if k in cfg_keys}
    args = {k: v for k, v in params.items() if k not in cfg_keys}
    # dimension_line/extension_line accept coordinate lists directly, but callout()
    # wants Vector origin/tail — coerce plain points so the model can pass coordinates
    # uniformly across all three ops.
    if method == "callout":
        # origin is a single point (wants a Vector); coerce a flat coordinate list.
        o = args.get("origin")
        if isinstance(o, (list, tuple)) and 2 <= len(o) <= 3 and all(isinstance(x, (int, float)) for x in o):
            args["origin"] = Vector(*o)
        # callout takes EITHER origin OR tail; passing both hits a library bug (it
        # follows origin but still draws the tail arrow). origin wins — drop the tail.
        if args.get("origin") is not None and args.get("tail") is not None:
            args.pop("tail")
    return getattr(draft_cls(**cfg), method)(**args)

# =============================================================================
# HELPERS
# =============================================================================

# Strip tech-stack proper nouns from anything the LLM/client sees (BRep kept — it's
# a geometry method the model needs, not a stack name).
_BRAND_RE = re.compile(
    r"open\s*cascade(\s*technology)?|\bocct\b|\bocp[_\s-]?vscode\b|\bocp\b"
    r"|\bcq[_\s-]?gears\b|\bcq[_\s-]?warehouse\b|\bmeadiode\b|\bgumyr\b"
    r"|\bcadquery\b|\bcq\b|\bfast\s*mcp\b|\bfastmcp\b|\bpython\b",
    re.IGNORECASE)


def _scrub(t: str) -> str:
    return _BRAND_RE.sub("", t or "")


def _log_err(msg: str, tb: str = None) -> None:
    # stderr: safe under stdio (stdout is the JSON-RPC pipe) and captured by the host over HTTP.
    print(f"[t2c] {msg}\n{tb or ''}", file=sys.stderr, flush=True)


def _error(msg: str, tb: str = None) -> str:
    if tb:
        _log_err(msg, tb)
    return json.dumps({"status": "error", "error": _scrub(msg)})


# The tessellated model for the web viewer now lives on each Session (Session.viewer);
# /model + /version serve it per session so users never see each other's models.

# Which viewer pipeline _show() uses, chosen by transport in __main__:
#   "stdio" → push to the standalone ocp_vscode viewer on :3939 via show()
#   "http"  → tessellate in-process and serve the payload over /model
# Defaults to "http" so importing the module (tests, HTTP entrypoints) keeps the
# tessellation pipeline; stdio runs flip it before any model is built.
_VIEWER_MODE: str = "http"


def _show(obj: Any) -> None:
    """Render obj into the current session's viewer pipeline (see _VIEWER_MODE)."""
    _sess().viewer["obj_type"] = _obj_type(obj)  # so /version can report it
    if _VIEWER_MODE == "stdio":
        _show_push(obj)
    else:
        _show_tessellate(obj)


def _show_push(obj: Any) -> None:
    """stdio: push obj to the standalone ocp_vscode viewer on :3939 via show().
    Mirrors the main-branch behavior; show() chatters on stdout, so mute it to
    keep the stdio JSON-RPC stream clean."""
    if not OCP_VIEWER_AVAILABLE:
        return
    import contextlib, io
    try:
        if isinstance(obj, Assembly):
            target = (obj,)
        elif isinstance(obj, Sketch):
            target = Workplane().placeSketch(obj)
        else:
            target = obj
        # ViewerBackend(0) clobbered the comms port to 0 at import; re-assert the
        # viewer port so show() reaches the standalone viewer instead of port 0.
        set_port(VIEWER_PORT)
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            show(target, port=VIEWER_PORT)
    except Exception:
        pass


# =============================================================================
# PICKED-FEATURE REFERENCES  (viewer "select" tool → prompt injection)
# =============================================================================
# When the user clicks features in the 3D viewer, the frontend posts the picked
# shape-id paths to /selection. Each path already resolves to a real OCCT
# sub-shape via the session's measurement backend (mb.model). We turn each into a
# neutral, human-readable geometric description the LLM can map to a selection.

def _part_name(shape_id: str) -> str:
    """The part a picked feature belongs to: the path segment before the topology
    suffix (/faces/faces_N, /edges/edges_N, /vertices/vertices_N), or the last
    segment for a whole-solid pick."""
    base = re.split(r"/(?:faces|edges|vertices)/", shape_id)[0]
    segs = [s for s in base.split("/") if s]
    return segs[-1] if segs else shape_id


def _fmt(x: float) -> str:
    v = round(float(x), 3) + 0.0
    if v == 0:
        v = 0.0  # avoid "-0"
    return f"{v:g}"


def _pt(p) -> str:
    return "(" + ", ".join(_fmt(v) for v in p) + ")"


def _face_normal(shape):
    """Outward-ish surface normal at the face's UV midpoint, as a unit tuple."""
    from OCP.BRepTools import BRepTools
    from OCP.BRepGProp import BRepGProp_Face
    from OCP.gp import gp_Pnt, gp_Vec, gp_Dir
    u0, u1, v0, v1 = BRepTools.UVBounds_s(shape)
    pnt, normal = gp_Pnt(), gp_Vec()
    BRepGProp_Face(shape).Normal((u0 + u1) / 2, (v0 + v1) / 2, pnt, normal)
    if normal.Magnitude() < 1e-10:
        return None
    d = gp_Dir(normal)
    return (d.X(), d.Y(), d.Z())


def _line_direction(shape):
    """Unit direction of a straight edge, or None."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.gp import gp_Pnt, gp_Vec, gp_Dir
    curve = BRepAdaptor_Curve(shape)
    pnt, vec = gp_Pnt(), gp_Vec()
    curve.D1(curve.FirstParameter(), pnt, vec)
    if vec.Magnitude() < 1e-10:
        return None
    d = gp_Dir(vec)
    return (d.X(), d.Y(), d.Z())


def _feat_center(shape):
    """Centroid CadQuery's nearest-to-point selector compares against — the datum
    that lets the LLM re-resolve this exact entity. None on failure."""
    try:
        c = cq.Shape.cast(shape).Center()
        return (c.x, c.y, c.z)
    except Exception:
        return None


def _describe_feature(shape_id, shape, world_center=None, placement=None) -> dict:
    """One picked feature → {id,label,text}. Neutral one-liner (safe to show the
    user), unnumbered (the frontend numbers by position).

    `shape` is the frame the model is EDITED in: for a placed assembly part that's
    the PART-LOCAL shape (coords labelled "local"), and world_center + placement
    then locate it in the assembly. For a single part (identity placement) it's the
    world shape and world_center/placement are None — coords are unlabelled."""
    props = _get_properties(shape)
    st = props.get("shape_type", "Shape")     # Vertex/Edge/Face/Solid/Compound
    gt = props.get("geom_type", "")            # Plane/Cylinder/Line/Circle/...
    part = _part_name(shape_id)
    flat = {}
    for sec in props.get("result", []) or []:
        if isinstance(sec, dict):
            flat.update(sec)

    center = _feat_center(shape)
    placed = placement is not None
    f = "local " if placed else ""   # label coords as part-local for placed parts

    attrs = [f'on part "{part}"' + (" (assembly part)" if placed else "")]
    if st == "Vertex":
        pt = center or flat.get("xyz") or props.get("refpoint")
        if pt is not None:
            attrs.append(f"{f}position {_pt(pt)}")
        label = "Vertex"
    elif st == "Edge":
        if center is not None:
            attrs.append(f"{f}center {_pt(center)}")
        for k in ("radius", "major radius", "minor radius"):
            if k in flat:
                attrs.append(f"{k} {_fmt(flat[k])}")
        if "start" in flat and "end" in flat:
            attrs.append(f"{f}from {_pt(flat['start'])} to {_pt(flat['end'])}")
        if "length" in flat:
            attrs.append(f"length {_fmt(flat['length'])}")
        if gt == "Line":
            try:
                d = _line_direction(shape)
                if d:
                    attrs.append(f"{f}direction {_pt(d)}")
            except Exception:
                pass
        label = f"{gt} edge" if gt else "Edge"
    elif st == "Face":
        if center is not None:
            attrs.append(f"{f}center {_pt(center)}")
        for k in ("radius", "base radius", "minor radius", "major radius"):
            if k in flat:
                attrs.append(f"{k} {_fmt(flat[k])}")
        try:
            n = _face_normal(shape)
            if n:
                attrs.append(f"{f}normal {_pt(n)}")
        except Exception:
            pass
        if "area" in flat:
            attrs.append(f"area {_fmt(flat['area'])}")
        label = f"{gt} face" if gt else "Face"
    else:  # Solid / CompSolid / Compound
        if center is not None:
            attrs.append(f"{f}center {_pt(center)}")
        if "volume" in flat:
            attrs.append(f"volume {_fmt(flat['volume'])}")
        label = st

    bb = flat.get("bb")
    if isinstance(bb, dict) and "min" in bb and "max" in bb:
        attrs.append(f"{f}bbox {_pt(bb['min'])}–{_pt(bb['max'])}")

    # Assembly context: where the feature sits in the assembly, and how the part is
    # placed — so the LLM can reason about position/orientation without the math.
    if placed:
        if world_center is not None:
            attrs.append(f"world center {_pt(world_center)}")
        (tx, ty, tz), (rx, ry, rz) = placement
        attrs.append(
            f"part placed at ({_fmt(tx)}, {_fmt(ty)}, {_fmt(tz)}) "
            f"rotated ({_fmt(rx)}, {_fmt(ry)}, {_fmt(rz)})° (XYZ)"
        )

    kind = (f"{gt} " if gt and gt not in ("Point", "Other") else "") + st.lower()
    text = f"{kind}: " + ", ".join(attrs)
    return {"id": shape_id, "label": label, "text": text}


def _walk_part_locs(model: dict) -> dict:
    """Map each tessellated part id → its absolute world placement (t, q) or None,
    from the ocp_vscode model tree — the same placement that moved mb.model's
    shapes to world. Lets /selection recover part-local coordinates."""
    out = {}

    def walk(node):
        for v in node.get("parts", []) or []:
            if v.get("parts") is not None:
                walk(v)
            else:
                out[v["id"]] = v.get("loc")
    try:
        walk(model)
    except Exception:
        pass
    return out


def _placement_of(loc_tq):
    """(cq.Location, ((tx,ty,tz),(rx,ry,rz)) degrees) for a part's (t,q), or
    (None, None) if it's missing / an identity placement (single-part case)."""
    if not loc_tq or _tq_to_loc is None:
        return None, None
    try:
        loc = cq.Location(_tq_to_loc(*loc_tq))
        pl = loc.toTuple()
        (t, r) = pl
        if all(abs(v) < 1e-6 for v in (*t, *r)):
            return None, None   # identity → world == local, no assembly context
        return loc, pl
    except Exception:
        return None, None


# =============================================================================
# ASSEMBLY SELF-DIAGNOSTIC FEEDBACK  (returned automatically by assembly_api)
# =============================================================================
# Ground truth about the solved assembly so the LLM can spot and fix its own
# positioning/orientation/constraint mistakes before handing the result to the
# user — the automatic analog of what the "select geometry" feature gives manually.

def _num(x) -> float:
    v = round(float(x), 3) + 0.0
    return 0.0 if v == 0 else v


def _assembly_world_parts(asm) -> list:
    """[(name, world_location, world_shape)] for each leaf part, at its solved
    world placement (relative child locations composed down the tree)."""
    out = []

    def rec(node, parent_loc):
        wl = parent_loc * node.loc
        if node.obj is not None:
            try:
                s = node.obj.val() if isinstance(node.obj, Workplane) else node.obj
                if isinstance(s, cq.Shape):
                    out.append((node.name, wl, s.located(wl)))
            except Exception:
                pass
        for ch in node.children:
            rec(ch, wl)

    rec(asm, Location())
    return out


def _bbox_overlap(a, b, tol=1e-6) -> bool:
    return all(a["min"][i] <= b["max"][i] + tol and b["min"][i] <= a["max"][i] + tol
               for i in range(3))


def _overlap_volume(s1, s2):
    try:
        c = s1.intersect(s2)
        return c.Volume() if c is not None else 0.0
    except Exception:
        return None


def _min_distance(s1, s2):
    try:
        from OCP.BRepExtrema import BRepExtrema_DistShapeShape
        d = BRepExtrema_DistShapeShape(s1.wrapped, s2.wrapped)
        return d.Value() if d.IsDone() else None
    except Exception:
        return None


def _assembly_report(asm) -> dict:
    """Per-part placement + bbox, part collisions, floating (disconnected) parts,
    unconstrained parts, and solve status. Best-effort — never raises."""
    try:
        parts = _assembly_world_parts(asm)
    except Exception:
        return {}
    if not parts:
        return {}

    constrained = set()
    try:
        for c in asm.constraints:
            for o in c.objects:
                constrained.add(o.split("@")[0].split("/")[-1])  # leaf part name
    except Exception:
        pass

    report = {"parts": [], "collisions": [], "floating_parts": [], "unconstrained_parts": []}
    bbs = []  # (name, shape, bbox_dict)
    for name, wl, shape in parts:
        t, r = wl.toTuple()
        try:
            bb = shape.BoundingBox()
            bbox = {"min": [_num(bb.xmin), _num(bb.ymin), _num(bb.zmin)],
                    "max": [_num(bb.xmax), _num(bb.ymax), _num(bb.zmax)]}
        except Exception:
            bbox = None
        report["parts"].append({
            "name": name,
            "position": [_num(v) for v in t],
            "rotation_deg": [_num(v) for v in r],
            "bbox": bbox,
            "constrained": name in constrained,
        })
        if name not in constrained:
            report["unconstrained_parts"].append(name)
        bbs.append((name, shape, bbox))

    # Pairwise interference/contact — bbox-prefiltered, capped so it stays cheap.
    n = len(bbs)
    if 1 < n <= 12:
        import itertools
        touch = {name: False for name, _, _ in bbs}
        for (n1, s1, b1), (n2, s2, b2) in itertools.combinations(bbs, 2):
            if not (b1 and b2 and _bbox_overlap(b1, b2)):
                continue  # bboxes apart → cannot touch
            vol = _overlap_volume(s1, s2)
            if vol is not None and vol > 1e-6:
                report["collisions"].append({"parts": [n1, n2], "overlap_volume": _num(vol)})
                touch[n1] = touch[n2] = True
            else:
                d = _min_distance(s1, s2)
                if d is not None and d < 1e-6:  # coincident faces (mated, no volume)
                    touch[n1] = touch[n2] = True
        report["floating_parts"] = [nm for nm, ok in touch.items() if not ok]

    sr = getattr(asm, "_solve_result", None)
    if isinstance(sr, dict):
        obj_hist = (sr.get("iterations") or {}).get("obj") or []
        report["solve"] = {"success": bool(sr.get("success")),
                           "residual": _num(obj_hist[-1]) if obj_hist else None}
    else:
        report["solve"] = {"attempted": False}
    return report


def _payload_is_empty(payload: Any) -> bool:
    """True when a tessellation payload has nothing to render (no instances and no
    parts). Pushing such a payload tears the viewer down mid-swap and blanks the web
    app, so callers skip the viewer update and keep the last good model instead."""
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if data.get("instances"):
        return False
    return not (data.get("shapes", {}) or {}).get("parts")


# Above this many rendered parts, drop edge geometry from the tessellation payload. The
# viewer builds one line-object (its own draw call + geometry) per part for edges, so on a big
# assembly they ~double the scene's object/draw-call count and memory for little value at that
# zoom. This is a level-of-detail cut for large models only; smaller models keep their edges.
VIEWER_EDGE_LOD_PARTS = 300


def _count_parts(shapes: dict) -> int:
    """Number of leaf parts in a three-cad-viewer shapes tree."""
    n = 0
    for p in (shapes.get("parts") or []):
        n += _count_parts(p) if "parts" in p else 1
    return n


def _lod_strip_edges(payload: dict) -> None:
    """For a big assembly, blank the edge buffers so the viewer skips ~one line-object per part
    (a large cut to draw calls, build time and memory). The viewer only builds edges when
    `edges.length > 0`, so emptying the buffer (0 bytes → 0-length array) makes it skip them.
    Format-agnostic: each buffer keeps its own encoding, we only blank its data + shape."""
    data = payload.get("data") or {}
    if _count_parts(data.get("shapes") or {}) <= VIEWER_EDGE_LOD_PARTS:
        return
    def blank(buf):
        return {**buf, "buffer": "", "shape": [0]} if isinstance(buf, dict) and "buffer" in buf else buf
    for inst in (data.get("instances") or []):
        for k in ("edges", "edge_types", "segments_per_edge"):
            if k in inst:
                inst[k] = blank(inst[k])


def _show_tessellate(obj: Any) -> None:
    """http: tessellate obj into the three-cad-viewer payload and store it for
    /model. Replaces the ocp_vscode websocket push (which can't work over HTTPS)."""
    if not OCP_CONVERT_AVAILABLE:
        return
    try:
        if isinstance(obj, Sketch):
            obj = Workplane().placeSketch(obj)
        import contextlib, io
        # _convert harmlessly tries to read config from a live viewer and prints
        # progress to stdout; mute both so the stdio JSON-RPC stream stays clean.
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            payload, mapping = _ocp_convert(obj)
        # An object with no renderable geometry (e.g. an annotation config) yields an
        # empty payload; don't bump the viewer version, so the frontend keeps showing
        # the previous model rather than crashing on an empty scene.
        if _payload_is_empty(payload):
            return
        _inject_studio_materials(payload)  # resolve builtin/texture material tags → appearance entries
        _lod_strip_edges(payload)          # big assembly: drop edges (draw-call/memory LOD)
        payload["config"]["reset_camera"] = "iso"  # frame the part on each render
        sess = _sess()
        sess.viewer["payload"] = payload
        sess.viewer["version"] += 1
        # Serialize the OCCT mapping the same way send_backend would, then (a) record
        # each part's world placement for /selection's local-frame recovery and
        # (b) load it into this session's measurement backend.
        try:
            model = json.loads(json.dumps(mapping, default=_ocp_default))
        except Exception:
            model = None
        sess.viewer["part_locs"] = _walk_part_locs(model) if model is not None else {}
        mb = sess.measure_backend
        if mb is not None and model is not None:
            try:
                with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                    mb.load_model(model)
            except Exception:
                pass
    except Exception:
        pass


def _build_placeholder() -> Any:
    """A single Workplane vertex shown at startup and after /clear so the 3D
    viewer is never blank — it still renders the grid, axes, and toolbar."""
    return Workplane().newObject([Vertex.makeVertex(0, 0, 0)])


def _init_viewer(sess: "Session") -> None:
    """Seed a new session's viewer with the placeholder so /model is never blank.
    Only meaningful for the http pipeline; stdio pushes to the standalone viewer."""
    if _VIEWER_MODE != "http":
        return
    token = _cur_session.set(sess)
    try:
        _show(_build_placeholder())
    except Exception:
        pass
    finally:
        _cur_session.reset(token)


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
        # Report the metric that matches the shape's dimensionality: length for
        # 1D wires/edges (e.g. parametricCurve), area for 2D faces/shells (e.g.
        # parametricSurface), volume (+area) for solids/compounds.
        if isinstance(shape, (Wire, Edge)):
            try: props["length"] = shape.Length()
            except Exception: pass
        elif isinstance(shape, (Face, Shell)):
            try: props["area"] = shape.Area()
            except Exception: pass
        else:
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
            resolved_params = resolve_value(raw_params)
            # A string `material` is a viewer builtin tag. CadQuery's add() would wrap
            # a str into a Material object (breaking tessellation), so apply it to the
            # freshly-added child directly instead of passing it through add().
            builtin_mat = (
                resolved_params.pop("material")
                if method_name == "add" and isinstance(resolved_params.get("material"), str)
                else None
            )
            obj = method(**resolved_params)
            if builtin_mat is not None and getattr(obj, "children", None):
                obj.children[-1].material = builtin_mat
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
    ctx: Context = None,
) -> str:
    """
    Build 3D models using Workplane API via method chaining.
    
    Modelling method: Boundary Representation (BRep)

    ── COORDINATE SYSTEM & VIEWER ──────────────────────────────────────────────
    The CAD engine uses a right-handed XYZ coordinate system:
      X → points right                     (red axis in 3D viewer)
      Y → points into or out of the screen (green axis)
      Z → points up                        (blue axis)

    The 3D viewer renders accordingly:
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
            - Pass "func" as {"_func": {"params": ["t"], "expr": "[x, y, z]"}} (trig in RADIANS).
            - e.g. a helix: {"method": "parametricCurve", "params": {"func": {"_func": {"params": ["t"], "expr": "[10*cos(2*pi*t), 10*sin(2*pi*t), 20*t]"}}, "N": 200}}
            - A closed curve makes a closed wire you can then extrude/loft into a solid.
        parametricSurface(func: Callable[[float, float], Union[Tuple[float, float], Tuple[float, float, float], Vector]], N: int=20, start: float=0, stop: float=1, tol: float=0.01, minDeg: int=1, maxDeg: int=6, smoothing: Optional[Tuple[float, float, float]]=(1, 1, 1))
	        - Create a spline surface approximating the provided function of two independent variables.
            - Pass "func" as {"_func": {"params": ["u", "v"], "expr": "[x, y, z]"}} (trig in RADIANS).
            - "start"/"stop" apply to BOTH u and v; keep them 0..1 and scale inside the expr.
            - e.g. a sphere: {"method": "parametricSurface", "params": {"func": {"_func": {"params": ["u", "v"], "expr": "[15*sin(pi*v)*cos(2*pi*u), 15*sin(pi*v)*sin(2*pi*u), 15*cos(pi*v)]"}}, "N": 30, "start": 0, "stop": 1}}
            - To make a solid from an OPEN patch (a sheet over a rectangular u,v range), follow with {"method": "val"} then {"method": "thicken", "args": [<thickness>]}.
            - Do NOT thicken a wrapped/closed surface (sphere, cylinder, dome): its u-seam and poles leave sliver faces + stray edges. Build solids of revolution with revolve on a profile instead.
            - May need "tol" tuning (raise it) if the approximation fails.
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
	        - Returns a model object with all pending edges connected into a wire.
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
        heatsert(size: str='M6', bolt_clear: float=0, chamfer=None, clean: bool=True)
	        - Cuts a heatsert (threaded-insert) hole for each point on the stack; size is 'M3'/'M4'/'M5'/'M6'. For 3D-printed parts. query_docs(["heatsert"]) for params.
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
	        - Mirror a single model object.
            - Moves a single model object about a specific plane, but on the current workplane. Kinda like moving the object to a different quadrant in the current workplane
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
	        - Tags the current model object for later reference.
            - Never use hyphens in tag names! It is not supported. Use underscores instead.
            - Example: Tag a solid, then select it's features later using selectors like solids, faces, edges, etc via the "tag" param.
      end(n: int=1)
	        - Return the nth parent of this model element
            - Can be used to iterate over items on the current stack and move a specific item to the current selection.
            - If you use "end" 2x on a stack, then the N of your 2nd end cannot go before the position of the 1st end.
      pushPoints(pntList: Iterable[Union[Tuple[float, float], Tuple[float, float, float], Vector, Location]])
	        - Pushes a list of 2D points onto the stack as vertices.
            - Creates an array of custom 2D points and pushed them onto the stack (similar to "rarray" or "polarArray")
      each(callback: Callable[[Union[Vector, Location, Shape, Sketch]], Shape], useLocalCoordinates: bool=False, combine: Union[bool, Literal['cut', 'a', 's']]=True, clean: bool=True)
	        - Runs the provided function on each value in the stack, and collects the return values into a new model object.
            - Needs a geometry-building callback [not supported]. To place a stored part at each point, use eachpoint instead.
      eachpoint(arg: Union[Shape, ForwardRef('Workplane'), Callable[[Location], Shape]], useLocalCoordinates: bool=False, combine: Union[bool, Literal['cut', 'a', 's']]=False, clean: bool=True)
	        - Places a copy of a stored part at every point on the stack (after rarray/polarArray/pushPoints).
            - Pass the part as "arg": {"_ref": "part_name"} and set "combine": true so the result is one object.
            - e.g. an 8-hole bolt circle: [{"method": "polarArray", "args": [25, 0, 360, 8]}, {"method": "eachpoint", "params": {"arg": {"_ref": "peg"}, "combine": true}}]
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
        - Parameters: obj (a model object) – an object to copy the workplane from
        - Returns: a model object with obj’s workplane
        - Example: Workplane("front").circle(1).extrude(10).copyWorkplane(Workplane("right", origin=(-5, 0, 0)) ).circle(1).extrude(10) # This creates two perpendicular cylinders
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
    compute CAD objects at resolve-time without a separate tool call:

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

      {"_func": {"params": ["t"], "expr": "[10*cos(2*pi*t), 10*sin(2*pi*t), 20*t]"}}
        - Build a math function (a callable) from a formula string. This is how
          you supply the lambda that parametricCurve / parametricSurface need.
        - "params": the variable names bound per call — ["t"] for parametricCurve
          (a curve), ["u", "v"] for parametricSurface (a surface). Default ["t"].
        - "expr": must return the point as a list [x, y, z] (or 2D [x, y]).
        - Trig here uses RADIANS (standard maths). Available: sin, cos, tan,
          asin, acos, atan, atan2, sinh, cosh, tanh, exp, log, log10, sqrt, pow,
          hypot, floor, ceil, abs, min, max, round, degrees, radians, pi, tau, e.
        - Conditionals work: "0 if t<0.5 else 1" (piecewise formulas).

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
    _bind(_sid_from_ctx(ctx))
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

        obj = await anyio.to_thread.run_sync(_run, obj, operations)

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
        await anyio.to_thread.run_sync(_show, obj)

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
    ctx: Context = None,
) -> str:
    """
    Build 2D profiles using Sketch API via method chaining.
    The stored Sketch can be passed to workplane_api via:
      {"method": "placeSketch", "args": [{"_ref": "sketch_1"}]}
    followed by extrude or cutBlind.

    ── FLAT PARTS FOR LASER / PLASMA / WATERJET / CNC CUTTING ───────────────────
    These processes cut a flat sheet, so the deliverable is a 2D vector profile,
    NOT a 3D solid. When the user wants a part for laser cutting, a plasma/waterjet
    table, CNC routing, vinyl cutting, etc.:
      • Build the cut outline (and any internal holes) as a Sketch here, and leave
        it as a Sketch — do NOT extrude it into a solid.
      • select_model the sketch so it is the active model.
      • The user can then download it as DXF or SVG (flat 2D vector formats).
    DXF/SVG downloads are offered ONLY when the active model is a 2D Sketch; 3D
    solids and assemblies cannot be exported to these formats.

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

    ── RETURN ──────────────────────────────────────────────────────────────────
    {"status":"success", "name":str, "obj_type":"Sketch",
     "properties": {"face_count":int, "total_area":float, "edge_count":int}}
    """
    _bind(_sid_from_ctx(ctx))
    try:
        if start_from:
            obj = _get(start_from)
        else:
            obj = Sketch()

        obj = await anyio.to_thread.run_sync(_run, obj, operations)
        name = store_as or _auto_name("sketch")
        _store(name, obj)
        await anyio.to_thread.run_sync(_show, obj)

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
    ctx: Context = None,
) -> str:
    """
    Assembly API fundamentals:
        - The response includes an "assembly_report" with ground truth about the result: each part's world "position"/"rotation_deg"/"bbox"/"constrained", "collisions" (parts whose solids overlap, with overlap_volume — usually a positioning error), "floating_parts" (parts touching nothing — often misplaced/disconnected), "unconstrained_parts" (left at their add-location), and "solve" (success + residual). ALWAYS read it: if it shows collisions, unconstrained/floating parts, or an unsuccessful solve, fix the constraints/locations and re-run BEFORE presenting the assembly to the user.
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
            4. "toCompound" - converts the multi-part assembly to a single compound solid
                - toCompound()
                - Note: this method is optional - not necessary!

    ── Adding objects section ─────────────────────────────────────────────────────────────────────

    "add" method format and info:
        - add(arg: {"_ref": "stored_shape"}, loc: {"_type": "Location", ...} | None = None, name: str | None = None, color: {"_type": "Color", ...}| None = None, material: Material | str | None = None, metadata: Dict[str, Any] | None = None)
            - "arg": {"_ref": "stored_shape"} - the object you created using workplane api that you want to add in this assembly
            - "loc": {"_type": "Location", ...} - set the initial position and orientation of the added object in the assembly (it gets overridden by constraints and solver, but an approximated "loc" can still be helpful for the solver)
            - "name": the name used to reference this assembly object in the "constrain" method (via its "query1" and "query2" positional args) later on to apply constraints on it
            - "color": {"_type": "Color", ...} - set the base color of the added object in the assembly
            - "material": {"_type": "Material", ...} - set the PHYSICAL SURFACE MATERIAL (metal/plastic/glass/…) of the part. Optional. See the {"_type": "Material", ...} section below. Set sensible materials proactively — a metal bracket → "steel"/"aluminum", a lens/window → "glass", a knob → "glossy_plastic". This makes the model look photorealistic in the viewer's Studio mode.
            - "metadata": Dict[str, Any] - any specific metadata/ context about the assembly part

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
            - Standard CSS/web colour names also work (crimson, silver, navy, darkblue, teal, indigo,
              maroon, olive, etc.) in addition to the X11 names listed below — so just use the common
              colour name you want. Only drop to method #2 (RGB) for a specific shade not covered by a name.
            - X11 colour names (a superset with numbered shades like steelblue1..4):
                aliceblue, antiquewhite, antiquewhite1, antiquewhite2, antiquewhite3, antiquewhite4, aquamarine1, aquamarine2, aquamarine4, azure, azure2, azure3, azure4, beet, beige, bisque, bisque2, bisque3, bisque4, black, blanchedalmond, blue, blue1, blue2, blue3, blue4, blueviolet, brown, brown1, brown2, brown3, brown4, burlywood, burlywood1, burlywood2, burlywood3, burlywood4, cadetblue, cadetblue1, cadetblue2, cadetblue3, cadetblue4, chartreuse, chartreuse1, chartreuse2, chartreuse3, chartreuse4, chocolate, chocolate1, chocolate2, chocolate3, chocolate4, coral, coral1, coral2, coral3, coral4, cornflowerblue, cornsilk1, cornsilk2, cornsilk3, cornsilk4, cyan, cyan1, cyan2, cyan3, cyan4, darkgoldenrod, darkgoldenrod1, darkgoldenrod2, darkgoldenrod3, darkgoldenrod4, darkgreen, darkkhaki, darkolivegreen, darkolivegreen1, darkolivegreen2, darkolivegreen3, darkolivegreen4, darkorange, darkorange1, darkorange2, darkorange3, darkorange4, darkorchid, darkorchid1, darkorchid2, darkorchid3, darkorchid4, darksalmon, darkseagreen, darkseagreen1, darkseagreen2, darkseagreen3, darkseagreen4, darkslateblue, darkslategray, darkslategray1, darkslategray2, darkslategray3, darkslategray4, darkturquoise, darkviolet, deeppink, deeppink2, deeppink3, deeppink4, deepskyblue1, deepskyblue2, deepskyblue3, deepskyblue4, dodgerblue1, dodgerblue2, dodgerblue3, dodgerblue4, firebrick, firebrick1, firebrick2, firebrick3, firebrick4, floralwhite, forestgreen, gainsboro, ghostwhite, gold, gold1, gold2, gold3, gold4, goldenrod, goldenrod1, goldenrod2, goldenrod3, goldenrod4, gray, gray0, gray1, gray10, gray11, gray12, gray13, gray14, gray15, gray16, gray17, gray18, gray19, gray2, gray20, gray21, gray22, gray23, gray24, gray25, gray26, gray27, gray28, gray29, gray3, gray30, gray31, gray32, gray33, gray34, gray35, gray36, gray37, gray38, gray39, gray4, gray40, gray41, gray42, gray43, gray44, gray45, gray46, gray47, gray48, gray49, gray5, gray50, gray51, gray52, gray53, gray54, gray55, gray56, gray57, gray58, gray59, gray6, gray60, gray61, gray62, gray63, gray64, gray65, gray66, gray67, gray68, gray69, gray7, gray70, gray71, gray72, gray73, gray74, gray75, gray76, gray77, gray78, gray79, gray8, gray80, gray81, gray82, gray83, gray85, gray86, gray87, gray88, gray89, gray9, gray90, gray91, gray92, gray93, gray94, gray95, gray97, gray98, gray99, green, green1, green2, green3, green4, greenyellow, honeydew, honeydew2, honeydew3, honeydew4, hotpink, hotpink1, hotpink2, hotpink3, hotpink4, indianred, indianred1, indianred2, indianred3, indianred4, ivory, ivory2, ivory3, ivory4, khaki, khaki1, khaki2, khaki3, khaki4, lavender, lavenderblush1, lavenderblush2, lavenderblush3, lavenderblush4, lawngreen, lemonchiffon1, lemonchiffon2, lemonchiffon3, lemonchiffon4, lightblue, lightblue1, lightblue2, lightblue3, lightblue4, lightcoral, lightcyan, lightcyan1, lightcyan2, lightcyan3, lightcyan4, lightgoldenrod, lightgoldenrod1, lightgoldenrod2, lightgoldenrod3, lightgoldenrod4, lightgoldenrodyellow, lightgray, lightpink, lightpink1, lightpink2, lightpink3, lightpink4, lightsalmon1, lightsalmon2, lightsalmon3, lightsalmon4, lightseagreen, lightskyblue, lightskyblue1, lightskyblue2, lightskyblue3, lightskyblue4, lightslateblue, lightslategray, lightsteelblue, lightsteelblue1, lightsteelblue2, lightsteelblue3, lightsteelblue4, lightyellow, lightyellow2, lightyellow3, lightyellow4, limegreen, linen, magenta, magenta1, magenta2, magenta3, magenta4, maroon, maroon1, maroon2, maroon3, maroon4, matrablue, matragray, mediumaquamarine, mediumorchid, mediumorchid1, mediumorchid2, mediumorchid3, mediumorchid4, mediumpurple, mediumpurple1, mediumpurple2, mediumpurple3, mediumpurple4, mediumseagreen, mediumslateblue, mediumspringgreen, mediumturquoise, mediumvioletred, midnightblue, mintcream, mistyrose, mistyrose2, mistyrose3, mistyrose4, moccasin, navajowhite1, navajowhite2, navajowhite3, navajowhite4, navyblue, oldlace, olivedrab, olivedrab1, olivedrab2, olivedrab3, olivedrab4, orange, orange1, orange2, orange3, orange4, orangered, orangered1, orangered2, orangered3, orangered4, orchid, orchid1, orchid2, orchid3, orchid4, palegoldenrod, palegreen, palegreen1, palegreen2, palegreen3, palegreen4, paleturquoise, paleturquoise1, paleturquoise2, paleturquoise3, paleturquoise4, palevioletred, palevioletred1, palevioletred2, palevioletred3, palevioletred4, papayawhip, peachpuff, peachpuff2, peachpuff3, peachpuff4, peru, pink, pink1, pink2, pink3, pink4, plum, plum1, plum2, plum3, plum4, powderblue, purple, purple1, purple2, purple3, purple4, red, red1, red2, red3, red4, rosybrown, rosybrown1, rosybrown2, rosybrown3, rosybrown4, royalblue, royalblue1, royalblue2, royalblue3, royalblue4, saddlebrown, salmon, salmon1, salmon2, salmon3, salmon4, sandybrown, seagreen, seagreen1, seagreen2, seagreen3, seagreen4, seashell, seashell2, seashell3, seashell4, sienna, sienna1, sienna2, sienna3, sienna4, skyblue, skyblue1, skyblue2, skyblue3, skyblue4, slateblue, slateblue1, slateblue2, slateblue3, slateblue4, slategray, slategray1, slategray2, slategray3, slategray4, snow, snow2, snow3, snow4, springgreen, springgreen2, springgreen3, springgreen4, steelblue, steelblue1, steelblue2, steelblue3, steelblue4, tan, tan1, tan2, tan3, tan4, teal, thistle, thistle1, thistle2, thistle3, thistle4, tomato, tomato1, tomato2, tomato3, tomato4, turquoise, turquoise1, turquoise2, turquoise3, turquoise4, violet, violetred, violetred1, violetred2, violetred3, violetred4, wheat, wheat1, wheat2, wheat3, wheat4, white, whitesmoke, yellow, yellow1, yellow2, yellow3, yellow4, yellowgreen
            - By default, use "gray90" for everything. It creates an off-white material color. If the user increases the material "metalness" variable in the 3D viewer, then "gray90" makes the material look like silver metal.
        method #2: {"_type": "Color", "r":0, "g":0, "b":0, "a":1}
            - "r", "g", "b" accept EITHER 0–255 integers OR 0.0–1.0 floats. The server auto-normalises: if any channel exceeds 1 the whole triple is divided by 255.
            - "a" is alpha (0.0 = fully transparent, 1.0 = fully opaque). Always pass a value in the 0.0–1.0 range.
        note:
            - By default (if you don't set a color for an object) the objects are displayed to the users with a yellowish color in the viewer.

    {"_type": "Material", ...} ("material" param of the "add" method):
        Sets the part's physical surface finish (metal / plastic / glass / …), rendered
        realistically in the viewer's "Studio" mode. Format: {"_type": "Material", "preset": "<name>"}
        where <name> is one of the viewer's built-in materials:

            metals       : gold, copper, brass, chrome, polished-steel, stainless-steel,
                           polished-aluminum, brushed-aluminum, cast-iron, titanium, galvanized
            plastic      : plastic-glossy, plastic-matte, abs-black, nylon
            rubber       : rubber-black, rubber-gray, rubber-red
            paint        : paint-glossy, paint-matte, paint-metallic, car-paint
            other        : ceramic-white, concrete, carbon-fiber
            transparent  : glass-clear, glass-tinted, glass-frosted, acrylic-clear
        Friendly aliases also work: steel, aluminum, silver, plastic, glass, rubber, ceramic,
        acrylic, carbon_fiber, matte_black, etc.

        HOW COLOUR WORKS — this matters:
            - For opaque NON-METALS (plastic, rubber, paint, ceramic, concrete, carbon-fiber) the
              part's OWN colour (the "color" you pass to "add") becomes the hue, and the material only
              supplies the finish. So to make a red glossy knob: set the part "color" to red AND
              "material" preset "plastic-glossy". Pick the finish; the part colour drives the hue.
            - METALS keep their own metallic tint (gold looks gold) — the part colour doesn't recolour
              them, so just choose the metal whose colour you want.
            - GLASS/acrylic are transparent.
        Do NOT put a "color" or raw PBR numbers inside a PRESET material spec — only pick a preset name;
        colour always comes from the part's "add" colour.

        TEXTURED surfaces (real grain/weave/detail): {"_type": "Material", "texture": "<name>"}
            Use these when the surface should show a photographic texture rather than a flat finish.
            Textures are projected onto the part automatically (no UVs needed) and carry their own
            colour, so you do NOT set the part "color" for a textured part.
            Texture names:
              wood, oak, wood_laminate, marble, concrete, concrete_floor, brick, cobblestone, stone,
              rubber, fabric (denim), leather, metal_plate, blue_metal, corrugated_metal, rusty_metal
            Aliases: wood_grain, timber, planks, cloth, textile, tile, rock, granite, brushed_metal,
              galvanized_metal, rust. Example — a wooden tabletop: "material": {"_type":"Material","texture":"wood"}.
            (Prefer a solid PRESET for plain plastics/metals/glass; use a TEXTURE only when real surface
            detail matters — a wooden panel, a brushed-metal plate, a fabric seat, a marble top.)

        Guidance: assign materials proactively and sensibly based on what the part physically is
        (metal bracket → steel/aluminum, lens/window → glass-clear, knob → plastic-glossy,
        tyre → rubber-black, mug → ceramic-white, wooden handle → texture "wood"). The user can ask you
        to change a part's material or colour at any time — just re-issue the model with the update.

    ── Constraint section ─────────────────────────────────────────────────────────────────────────

    "constrain" method formats and info:
        CRITICAL: "constrain" is an *args method. It does NOT accept keyword arguments. 
        ALWAYS use the positional "args" form in MCP tool calls. NEVER use the "params" (keyword) form.

        Positional dispatch rules (the CAD engine detects the form from the number and types of args):
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
                    # Vertex.makeVertex(x,y,z) converts the raw 3D world coordinates into a Vertex Shape.
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
            "material": {"_type": "Material", "preset": "glossy_plastic"},
            "loc": {"_type": "Location", "x": 0, "y": 0, "z": 0}
            }},
            {"method": "add", "params": {
            "arg": {"_ref": "part2"}, "name": "part2",
            "color": {"_type": "Color", "name": "deepskyblue1"},
            "material": {"_type": "Material", "preset": "brushed_aluminum"},
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
        First param key is "arg" (the CAD object to add).

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
    _bind(_sid_from_ctx(ctx))
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

        obj = await anyio.to_thread.run_sync(_run, obj, operations)
        name = store_as or _auto_name("assembly")
        _store(name, obj)
        await anyio.to_thread.run_sync(_show, obj)

        result = {"status": "success", "name": name,
                  "obj_type": _obj_type(obj), "properties": _properties(obj)}
        if isinstance(obj, Assembly):
            report = await anyio.to_thread.run_sync(_assembly_report, obj)
            if report:
                result["assembly_report"] = report
        return json.dumps(result)
    except Exception as e:
        return _error(str(e), traceback.format_exc())

# =============================================================================
# TOOL 4 — select_model
# =============================================================================

@mcp.tool(name="select_model")
async def select_model(name: str, ctx: Context = None) -> str:
    """
    Make a previously built model the ACTIVE model.

    Every model you build (via workplane_api / sketch_api / assembly_api) is stored
    under a name and automatically becomes active. The 3D viewer always shows the
    active model, and the active model is the one the user can download/ export.

    Only ONE model/assembly/sketch can be active at a time: the viewer displays
    exactly one active model, and only that one is downloadable. It cannot show
    multiple stored models at once.

    Use this tool to bring back an EARLIER model (by its stored name) WITHOUT
    rebuilding it — e.g. when the user wants to view a previous model again, or wants
    to download a previous model instead of the latest one.

    name: stored name of a model created earlier (returned as "name" by the build
          tools). Works with models from all three APIs.
    """
    sess = _bind(_sid_from_ctx(ctx))
    try:
        obj = _get(name)  # raises if name unknown
        sess.current = name
        sess.rev += 1  # active model changed → snapshot stale (reopened chat must show this one)
        await anyio.to_thread.run_sync(_show, obj)
        return json.dumps({"status": "success", "name": name,
                           "obj_type": _obj_type(obj), "properties": _properties(obj)})
    except Exception as e:
        return _error(str(e), traceback.format_exc())


_DETAIL_BUDGET = {"summary": 0, "standard": 200, "full": 100000}


@mcp.tool(name="inspect_model")
async def inspect_model(name: Optional[str] = None, detail: str = "standard",
                        ctx: Context = None) -> str:
    """
    Read the geometry of a stored model as structured data — a far richer, exact substitute
    for a 2D drawing.

    Returns the SHAPE (boundary representation), not the build recipe: the exact dimensions,
    surface/curve types, holes, fillets, bounding box, and — for assemblies — each part's
    name, color, and placement. A STEP file (like most CAD exchange files) never stores the
    sketch→extrude→fillet history, so infer any recipe you need from this geometry.

    Use this to understand a model you did NOT build — e.g. a STEP file the user uploaded —
    BEFORE editing it. To EDIT a specific face/edge, read its center coordinate here and pass
    it to a coordinate selector (e.g. faces(cq.selectors.NearestToPointSelector((x,y,z)))) in
    workplane_api(start_from="<name>") — that reliably targets one feature on a dumb solid.

    name:   stored model name; omit for the active model.
    detail: how much geometry to return.
            - "summary"  — bbox, primitives, hole/fillet counts, part list only (smallest).
            - "standard" — also the full face/edge list for solids up to ~200 faces (default).
            - "full"     — always emit every face and edge (use for large/complex parts when
                           you need to reference a specific feature; larger output).
    """
    _bind(_sid_from_ctx(ctx))
    budget = _DETAIL_BUDGET.get(detail, _DETAIL_BUDGET["standard"])
    try:
        obj = _get(name)
        description = await anyio.to_thread.run_sync(describe_shape, obj, None, budget)
        return json.dumps({"status": "success", "name": name or _sess().current,
                           "obj_type": _obj_type(obj), "detail": detail,
                           "description": description})
    except Exception as e:
        return _error(str(e), traceback.format_exc())


@mcp.tool(name="edit_model")
async def edit_model(operations: List[dict], name: Optional[str] = None,
                     store_as: Optional[str] = None, ctx: Context = None) -> str:
    """
    Directly edit a "dumb" solid — an imported STEP model, or any model with no build history —
    in a parametric way, WITHOUT constructive-solid-geometry hacks. Use this instead of
    building a separate shape and subtracting it.

    First call inspect_model to get the geometry. Each face and edge has a point on it
    ("point_on_face") and holes/fillets have an "axis_point"/center. You select a feature by
    passing a point that lies ON it (a "near" point); the tool resolves it to the exact face or
    edge. Prefer the point_on_face value for a face, and a point on the wall for a hole.

    operations: a list applied in order. Each item is {"op": <name>, ...}:
      • {"op":"resize_hole", "edge":{"near":[x,y,z]}, "diameter": D}
          Change the selected hole's diameter (removes the old hole, heals, re-cuts at the new
          size). Add "scope":"matching" to also resize every other hole of the same diameter
          (a bolt pattern) in the same call.
      • {"op":"remove_feature", "faces":[{"near":[x,y,z]}, ...]}
          Delete features (holes, bosses, fillets, chamfers) and heal the gap.
      • {"op":"push_pull_face", "face":{"near":[x,y,z]}, "distance": d}
          Move a planar face along its normal: +d adds material, -d removes it.
      • {"op":"offset_face", "face":{"near":[x,y,z]}, "distance": d}   (alias of push_pull_face)
      • {"op":"shell", "faces":[{"near":[x,y,z]}], "thickness": t}
          Hollow the solid, opening it at the given face(s).
      • {"op":"draft_face", "face":{"near":[x,y,z]}, "angle_deg": a}
          Taper a face by an angle (for moulded parts).

    Returns a report with "valid" (is the result a sound solid?) and the volume before/after.
    ALWAYS check "valid": if false, the edit did not apply cleanly — adjust and retry.

    name:     stored model to edit; omit for the active model.
    store_as: name to save the result under; omit to overwrite the edited model.
    """
    _bind(_sid_from_ctx(ctx))
    try:
        src_name = name or _sess().current
        obj = _get(name)
        result, report = await anyio.to_thread.run_sync(apply_edits, obj, operations)
        out_name = store_as or src_name or _auto_name("edit")
        _store(out_name, result)
        await anyio.to_thread.run_sync(_show, result)
        return json.dumps({"status": "success", "name": out_name,
                           "obj_type": _obj_type(result), "report": report})
    except Exception as e:
        return _error(str(e), traceback.format_exc())

# =============================================================================
# TOOL — extension_api
# =============================================================================

def _ext_options(part: str, fastener_type: Optional[str]) -> dict:
    """Introspect a catalog part: its constructor params and — for data-driven parts
    (fasteners/bearings/threads) — the exact valid standard types and sizes, read live
    from the part's own tables (never hardcoded)."""
    entry = _EXT_CATALOG[part]
    cls = entry["cls"]
    out: Dict[str, Any] = {"part": part, "family": entry["family"]}
    # For a drafting op the caller passes the method's args; for every other part they
    # pass the constructor's kwargs.
    target = getattr(cls, entry["method"]) if entry.get("method") else cls.__init__
    try:
        sig = inspect.signature(target)
        out["params"] = [
            (f"{p.name}={p.default!r}" if p.default is not inspect.Parameter.empty else f"{p.name} [required]")
            for p in sig.parameters.values() if p.name != "self"
        ]
    except (TypeError, ValueError):
        pass
    if entry.get("method"):   # drafting: config knobs can also be passed in params
        out["draft_config_params"] = sorted(_draft_config_keys(cls))
    if callable(getattr(cls, "types", None)):
        try:
            types = sorted(cls.types())
            out["standard_types"] = types
            ft = fastener_type or (types[0] if types else None)
            if ft and callable(getattr(cls, "sizes", None)):
                out["sizes_for_type"] = ft
                out["sizes"] = list(cls.sizes(ft))
        except Exception:
            pass
    return out


@mcp.tool(name="extension_api")
async def extension_api(
    op: str = "build",
    part: Optional[str] = None,
    params: Optional[dict] = None,
    plane: Optional[str] = None,
    fastener_type: Optional[str] = None,
    store_as: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """
    Build specialized, ready-made parametric PARTS that would be impractical or
    impossible to model from primitives — precision gears (true involute teeth),
    standards-based fasteners, bearings, threads, sprockets and roller chains.

    Use this whenever the user asks for such a part instead of approximating it with
    boxes/cylinders. Everything you build here is stored under a name and behaves like
    any other model: you can keep editing it in workplane_api (start_from="<name>") and
    combine it in assembly_api.

    ── OPERATIONS (op) ─────────────────────────────────────────────────────────
    • op="list"    → all available parts grouped by family. Call this first if unsure
                     what exists.
    • op="options" → for one `part`: its constructor parameters, and (for fasteners/
                     bearings/threads) the EXACT valid standard types and sizes read
                     live from the part's own tables. ALWAYS call this before building a
                     fastener/bearing/thread — their `size`/`type` strings must match
                     exactly (e.g. "M3-0.5", "iso4762") or construction fails. Pass
                     `fastener_type` to list the sizes for a specific standard.
    • op="build"   → construct `part` with `params` and store it (default op).

    ── build ARGS ──────────────────────────────────────────────────────────────
    part:    class name of the part (see op="list"), e.g. "SpurGear", "SocketHeadCapScrew".
    params:  keyword arguments for that part, e.g. {"module":1,"teeth_number":20,"width":5,"bore_d":5}.
    plane:   (gears only) the workplane the gear is built on. Default "XY".
    store_as: name to store the result under (auto-generated if omitted).

    Returns {status, name, obj_type, properties}, same as the other build tools.

    ── FAMILIES ────────────────────────────────────────────────────────────────
    • gear     — SpurGear (set helix_angle>0 for a helical gear), HerringboneGear,
                 BevelGear, RingGear, RackGear, Worm, PlanetaryGearset, … Build params
                 like module, teeth_number, width, bore_d, pressure_angle, helix_angle.
    • fastener — screws (SocketHeadCapScrew, HexHeadScrew, CounterSunkScrew, SetScrew, …),
                 nuts (HexNut, SquareNut, DomedCapNut, HeatSetNut, …), washers. Params:
                 size, fastener_type, length (screws). Pass simple=true for a fast plain
                 body, or omit for real thread geometry.
    • bearing  — SingleRowDeepGrooveBallBearing, …; params size, bearing_type.
    • thread   — IsoThread, AcmeThread, MetricTrapezoidalThread, …
    • sprocket / chain — Sprocket (num_teeth, chain_pitch, …); Chain across sprockets.
    • drafting — dimension_line, extension_line, callout: dimension & annotation
                 assemblies; params are the op's args (e.g. path) plus look settings.
                 Refrain from using "callout" unless the user explicitly requests it.

    ── FASTENER HOLES & PLACEMENT (in the OTHER tools) ──────────────────────────
    Matching holes for a fastener are cut in workplane_api, not here — call these
    methods there on a workplane, passing the built fastener via {"_ref":"<name>"}:
      clearanceHole, tapHole, threadedHole, insertHole (for HeatSetNut).
    To place fasteners into an assembly, use pushFastenerLocations / the hole methods'
    baseAssembly argument in assembly_api. query_docs any of these for exact params.

    A finger-jointed (laser-cut) box is likewise made in workplane_api, not here: build
    the box, select its vertical edges, then call the makeFingerJoints method
    (materialThickness, targetFingerWidth) on that workplane.


    To learn a part's exact parameters, call query_docs(methods=["<PartName>"]).
    """
    _bind(_sid_from_ctx(ctx))
    try:
        if not EXT_AVAILABLE:
            return _error("Specialized parts are currently unavailable.")

        if op == "list":
            fams: Dict[str, List[str]] = {}
            for name, entry in sorted(_EXT_CATALOG.items()):
                fams.setdefault(entry["family"], []).append(name)
            return json.dumps({"status": "success", "parts_by_family": fams})

        if op == "options":
            if not part or part not in _EXT_CATALOG:
                return _error(f"Unknown part '{part}'. Use op='list' to see available parts.")
            return json.dumps({"status": "success", **_ext_options(part, fastener_type)})

        if op == "build":
            if not part or part not in _EXT_CATALOG:
                return _error(f"Unknown part '{part}'. Use op='list' to see available parts.")
            spec = {"_type": part, "params": params or {}}
            if plane:
                spec["plane"] = plane
            obj = await anyio.to_thread.run_sync(_build_ext_part, spec)
            name = store_as or _auto_name(_EXT_CATALOG[part]["family"])
            _store(name, obj)
            await anyio.to_thread.run_sync(_show, obj)
            return json.dumps({"status": "success", "name": name,
                               "obj_type": _obj_type(obj), "properties": _properties(obj)})

        return _error(f"Unknown op '{op}'. Use 'build', 'list', or 'options'.")
    except Exception as e:
        return _error(str(e), traceback.format_exc())

# =============================================================================
# TOOL 5 — query_docs
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
    if name in _EXT_CATALOG:            # extension_api part classes (docs on __init__)
        return _EXT_CATALOG[name]["cls"]
    raise ValueError(f"Unknown class: '{name}'")


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
    return _scrub("\n".join(out))


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
             For an extension_api part or op, just pass its name as a method — e.g.
             methods=["SpurGear"] or ["dimension_line"] — no cls needed; you get its
             parameter docs directly.

    Returns plain-text docs per entry: signature, summary, params, full docstring.
    """
    # "Edge", "Wire", "Face", "Shell", "Solid", "Compound", "Shape" - add this once there is a direct_api tool

    results = []

    # extension_api parts/ops self-document: a part documents its constructor and a
    # drafting op documents its method, whether the name is given as `cls` or a method.
    for name in {n for n in ([cls] if cls else []) + methods if n in _EXT_CATALOG}:
        entry = _EXT_CATALOG[name]
        tname = entry.get("method", "__init__")
        target = getattr(entry["cls"], tname, None)
        if callable(target):
            try:
                results.append(_doc_render(name, tname, target, inspect.signature(target)))
            except (TypeError, ValueError):
                pass

    # Everything else is looked up on the requested class (or the defaults).
    remaining = [m for m in methods if m not in _EXT_CATALOG and m not in ("__init__", cls)]
    if remaining:
        if cls:
            try:
                klass = _EXT_CATALOG[cls]["cls"] if cls in _EXT_CATALOG else _resolve_cls(cls)
            except ValueError as e:
                return "\n".join(results) if results else str(e)
            classes = [(cls, klass)]
        else:
            classes = [(k.__name__, k) for k in _DOC_DEFAULTS]
        for display_name, klass in classes:
            for mname in remaining:
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
# TOOL 7 — report_learning  (agent → developer self-improvement channel)
# =============================================================================
# Stateless by design: this tool only validates + acks. The web backend reads the
# call's arguments straight off the turn's steps and logs the structured learning to
# Langfuse (a dedicated "agent-learnings" dataset item + a categorical trace score +
# trace tags), where the trace id and Langfuse client already live. Keeping the CAD
# server free of any Langfuse dependency is intentional — reporting must never touch
# core modeling behavior.

_LEARNING_TYPES = {
    "missing_capability",  # a capability the agent needed but no tool offers
    "tool_doc_error",      # a tool's doc/schema is wrong, misleading, or incomplete
    "tool_bug",            # a tool errors or misbehaves unexpectedly
    "context_gap",         # knowledge the agent wished it had up front (add to context)
    "stale_context",       # something in the agent's context is wrong/misleading (remove it)
    "technique",           # a reusable trick/recipe discovered by trial-and-error (save it)
    "painpoint",           # recurring friction, a repeated mistake, or user frustration
}
_LEARNING_SEVERITIES = {"low", "medium", "high"}


@mcp.tool(name="report_learning")
async def report_learning(
    type: str,
    title: str,
    detail: str,
    suggestion: Optional[str] = None,
    severity: str = "medium",
    tool: Optional[str] = None,
    evidence: Optional[str] = None,
    ctx: Context = None,
) -> str:
    """
    Tell the DEVELOPERS something you learned that would help them make you better.
    This is a private feedback channel to the people who build your tools — it is NOT
    shown to the user and is NOT a reply to the user. Use it to turn the things you
    discover mid-task (through real trial-and-error) into concrete, actionable signal
    the developers can act on: gaps to fill, docs to fix, bugs to squash, context to
    add or remove, and hard-won techniques worth keeping.

    ── WHEN TO CALL (self-trigger — you do not need to be asked) ─────────────────
    Certain things you OBSERVE while working ARE your cue to file a report, on your
    own, mid-task — the user will not ask you to. Each row below is a concrete event
    to watch for and the type to file when it happens:

    • tool_bug           — a tool errored, rejected valid input, or gave a wrong/
                           surprising result when you used it correctly. File it; put a
                           repro (the exact params, the error) in `evidence`.
    • tool_doc_error     — you had to retry or adjust a call because a tool's description
                           or params didn't match reality. Name the tool + what's off.
    • technique          — you only got a tricky result to work after trial-and-error and
                           found a reliable recipe (e.g. a specific op sequence). Save it.
    • context_gap        — a fact/convention would have saved you trial-and-error had you
                           known it up front. Suggest what to add to your context.
    • stale_context      — something in your instructions/tool docs was wrong or outdated
                           and misled you. Say what to remove or correct.
    • painpoint          — the user corrected you, repeated a request, or was visibly
                           frustrated, or you keep making the same mistake. Flag it.
    • missing_capability — the user asked for something no tool can do, and you confirmed
                           it by genuinely trying first. File ONCE, then plainly tell the
                           user you can't do it. Not before really trying with your tools.

    Reporting is background: file it and keep helping the user — it never delays or
    replaces your reply. File several in a turn if several distinct things happened.
    Skip routine successes and guesses — file only real, reusable signal a developer
    could act on without watching this session.

    ── fields ───────────────────────────────────────────────────────────────────
    title:      one short line — the headline a developer scans (required).
    detail:     what happened and WHY it matters (required). Be concrete and specific.
    suggestion: the fix you'd propose — the new tool, the corrected doc, the recipe, etc.
    severity:   "low" | "medium" | "high" — how much it hurts / how much it'd help.
    tool:       the tool this concerns, if any (e.g. "workplane_api").
    evidence:   a concrete example — the exact params that failed, an error message, a repro.

    Returns {"status": "logged", ...}. This is internal telemetry: never mention this
    tool, its existence, or its contents to the user.
    """
    if type not in _LEARNING_TYPES:
        return _error(f"Unknown type '{type}'. Use one of: {sorted(_LEARNING_TYPES)}")
    if severity not in _LEARNING_SEVERITIES:
        severity = "medium"
    # The web backend does the actual Langfuse write from the turn's steps; here we
    # only confirm the report was well-formed so the model can move on.
    return json.dumps({"status": "logged", "type": type, "title": title})


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


@mcp.custom_route("/clear", methods=["POST"])
async def _clear(request):
    """Reset ONE session's object store to a clean slate and re-show the
    placeholder vertex so its viewer updates. Token-gated. ?session=<id>."""
    from starlette.responses import JSONResponse
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sess = _get_session(request.query_params.get("session"))
    sess.state.clear()
    sess.counters.clear()
    sess.current = None
    _init_viewer(sess)  # re-show the placeholder (grid + tools) so the viewer stays visible
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/session/export", methods=["GET"])
async def _session_export(request):
    """Serialize a session's CAD objects for durable storage. Token-gated.
    404 when the session has nothing to save."""
    from starlette.responses import JSONResponse, Response
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sess = _get_session(request.query_params.get("session"))
    if not sess.state:
        return JSONResponse({"error": "empty"}, status_code=404)
    # Nothing changed since the last export (e.g. a Q&A / doc-only turn) → tell the
    # web layer to skip the re-upload. Avoids re-writing an identical blob every turn.
    if sess.rev == sess.exported_rev:
        return Response(status_code=304)
    try:
        data = _snapshot(sess)
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        return JSONResponse({"error": "snapshot failed"}, status_code=500)
    sess.exported_rev = sess.rev
    return Response(data, media_type="application/octet-stream")


@mcp.custom_route("/session/import", methods=["POST"])
async def _session_import(request):
    """Restore a previously-saved snapshot into a session so the LLM can keep
    working on models built in an earlier run. Token-gated. Skips if the session
    already has objects (never clobbers live work)."""
    from starlette.responses import JSONResponse
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    sess = _get_session(request.query_params.get("session"))
    if sess.state:
        return JSONResponse({"status": "already-loaded"})
    body = await request.body()
    if not body:
        return JSONResponse({"status": "empty"})
    try:
        n = _restore_into(sess, body)
        # Re-tessellate the active model so the viewer shows it immediately.
        if sess.current and sess.current in sess.state:
            token = _cur_session.set(sess)
            try:
                _show(sess.state[sess.current])
            finally:
                _cur_session.reset(token)
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        return JSONResponse({"error": "restore failed"}, status_code=500)
    return JSONResponse({"status": "ok", "objects": n})


@mcp.custom_route("/session/adopt", methods=["POST"])
async def _session_adopt(request):
    """Move a new chat's live CAD state from its temporary local session id to the persistent
    chat id. A brand-new chat has no chat id yet, so a model imported/built before the first
    message is stored under the local id; when the first message creates the chat row the
    backend session id switches to the chat id, orphaning that model. This re-homes it. No-op
    when there is nothing to move or the target already holds work (never clobbers). Token-
    gated. ?from=<localId>&to=<chatId>."""
    from starlette.responses import JSONResponse
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    frm = request.query_params.get("from")
    to = request.query_params.get("to")
    if not frm or not to or frm == to:
        return JSONResponse({"status": "noop"})
    src = _get_session(frm)
    if not src.state:
        return JSONResponse({"status": "noop"})          # nothing built/imported locally
    dst = _bind(to)                                       # binds contextvar for _show below
    if dst.state:
        return JSONResponse({"status": "target-occupied"})  # keep existing work intact
    dst.state.update(src.state)
    dst.counters.update(src.counters)
    dst.auto_names.update(src.auto_names)
    dst.current = src.current
    dst.rev += 1
    src.state.clear()                                     # release the throwaway local session
    src.current = None
    try:
        if dst.current and dst.current in dst.state:
            _show(dst.state[dst.current])                # re-tessellate so the viewer shows it
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
    return JSONResponse({"status": "ok", "objects": len(dst.state)})


@mcp.custom_route("/export", methods=["GET"])
async def _export(request):
    """Export the current stored object so the web backend can serve it as a
    download. Token-gated. ?fmt=stl|3mf|step|amf|brep (3D), or dxf|svg (2D, sketches
    only — for laser cutting / plasma / CNC). Default step."""
    from starlette.responses import JSONResponse, FileResponse
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    _bind(request.query_params.get("session"))
    fmt = request.query_params.get("fmt", "step").lower()
    fmt_map = {
        "step": ("step", "STEP"), "stp": ("step", "STEP"),
        "stl":  ("stl",  "STL"),
        "3mf":  ("3mf",  "3MF"),
        "amf":  ("amf",  "AMF"),
        "brep": ("brep", "BREP"),
        "dxf":  ("dxf",  "DXF"),
        "svg":  ("svg",  "SVG"),
    }
    ext, export_type = fmt_map.get(fmt, ("step", "STEP"))
    try:
        obj = _get(None)  # current object
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        return JSONResponse({"error": _scrub(str(e))}, status_code=404)
    # DXF/SVG are flat 2D vector formats — only meaningful for a 2D Sketch. A 3D
    # solid/assembly would just dump a messy projection of all edges, so refuse it.
    if export_type in ("DXF", "SVG") and not isinstance(obj, Sketch):
        return JSONResponse(
            {"error": "DXF/SVG export is only available for 2D sketches"},
            status_code=400,
        )
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"model.{ext}")
    if export_type == "STEP":
        ensure_ap242_schema()  # write STEP as AP242 (richest schema); falls back to default if unsupported
    try:
        if isinstance(obj, Assembly):
            if export_type == "STEP":
                # STEP preserves the assembly: part names, colors, hierarchy.
                obj.export(path, exportType="STEP")
            else:
                # Mesh/BREP formats can't hold hierarchy; flatten to a Compound
                # (parts kept as separate solids, locations applied).
                cq.exporters.export(obj.toCompound(), path, exportType=export_type)
        else:
            # Workplane/Shape/Sketch all expose .val(); Sketch.val() is a Compound.
            shape = obj.val() if hasattr(obj, "val") else obj
            cq.exporters.export(shape, path, exportType=export_type)
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        return JSONResponse({"error": _scrub(f"export failed: {e}")}, status_code=500)
    return FileResponse(path, filename=f"model.{ext}")


def _convert_and_read(body: bytes):
    """Persist the upload, normalise it to AP242 (keep the original if that fails), then read
    it. Returns (obj, meta, converted_bool). Runs in a worker thread (blocking OCCT work)."""
    import tempfile
    d = tempfile.gettempdir()
    orig = os.path.join(d, "import_orig.step")
    with open(orig, "wb") as fh:
        fh.write(body)
    ap242 = os.path.join(d, "import_ap242.step")
    converted = to_ap242(orig, ap242)          # first thing: convert AP203/214/… → AP242
    obj, meta = read_step(ap242 if converted else orig)
    return obj, meta, converted


@mcp.custom_route("/import", methods=["POST"])
async def _import(request):
    """Import an uploaded STEP file as the ACTIVE model and return a structured geometric
    description the LLM reads in place of a 2D drawing. Any AP203/AP214 file is first converted
    to AP242. The user can then edit it (workplane_api start_from=<name>). Token-gated. Raw
    STEP bytes in the request body; ?session=<id>&name=<model-name>&filename=<original-name>."""
    from starlette.responses import JSONResponse
    if not _authorized(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)
    _bind(request.query_params.get("session"))
    body = await request.body()
    if not body:
        return JSONResponse({"error": "empty file"}, status_code=400)
    # Reject unsupported formats up front (extension + content sniff) with a clear message.
    fname = (request.query_params.get("filename") or "").lower()
    ext_ok = fname.endswith(SUPPORTED_IMPORT_EXTS) if fname else True
    if not ext_ok or not looks_like_step(body):
        return JSONResponse(
            {"error": "unsupported file format. Only STEP files (.step / .stp) can be "
                      "imported.", "supported": list(SUPPORTED_IMPORT_EXTS)},
            status_code=415)
    try:
        obj, meta, converted = await anyio.to_thread.run_sync(_convert_and_read, body)
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        return JSONResponse({"error": _scrub(f"could not read STEP file: {e}")},
                            status_code=400)
    name = request.query_params.get("name") or _auto_name("import")
    _store(name, obj)
    try:
        description = await anyio.to_thread.run_sync(describe_shape, obj, meta)
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        description = {"error": "description unavailable"}
    await anyio.to_thread.run_sync(_show, obj)  # render it + load the measurement backend
    return JSONResponse({"status": "ok", "name": name, "obj_type": _obj_type(obj),
                         "converted_to_ap242": converted, "description": description})


@mcp.custom_route("/model", methods=["GET"])
async def _model(request):
    import gzip
    from starlette.responses import JSONResponse, Response
    sess = _get_session(request.query_params.get("session"))
    payload = sess.viewer["payload"]
    if payload is None:
        return JSONResponse({"error": "no model yet"}, status_code=404)
    # The tessellation payload is large (tens of MB for a big assembly) and the viewer
    # re-fetches it on every version bump. Serialise + gzip ONCE per version and cache the
    # bytes (a 1250-part model is ~21 MB JSON → ~5 MB gzip), cutting both transfer and the
    # browser's parse time. Repeated fetches of the same version reuse the cache.
    ver = sess.viewer["version"]
    cache = sess.viewer.get("_gz")
    if not cache or cache[0] != ver:
        cache = (ver, gzip.compress(json.dumps(payload).encode(), 5))
        sess.viewer["_gz"] = cache
    gz = cache[1]
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        return Response(gz, media_type="application/json",
                        headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
    return Response(gzip.decompress(gz), media_type="application/json")


@mcp.custom_route("/version", methods=["GET"])
async def _version(request):
    from starlette.responses import JSONResponse
    sess = _get_session(request.query_params.get("session"))
    return JSONResponse({"version": sess.viewer["version"],
                         "obj_type": sess.viewer.get("obj_type")})


@mcp.custom_route("/backend", methods=["POST"])
async def _backend(request):
    """Measurement tools (distance/properties). The frontend posts viewer
    state changes (activeTool + selectedShapeIDs); we return the computed
    backend_response for viewer.handleBackendResponse(). ?session=<id>."""
    from starlette.responses import JSONResponse
    mb = _get_session(request.query_params.get("session")).measure_backend
    if mb is None:
        return JSONResponse({}, status_code=503)
    try:
        changes = await request.json()
        resp = mb.handle_event(changes, _MessageType.UPDATES)
    except Exception as e:
        _log_err(str(e), traceback.format_exc())
        return JSONResponse({"error": _scrub(str(e))}, status_code=500)
    return JSONResponse(resp or {})


@mcp.custom_route("/selection", methods=["POST"])
async def _selection(request):
    """Viewer 'select' tool: the frontend posts the picked shape-id paths
    ({"shapeIds": [...]}); we resolve each to its OCCT sub-shape and return a
    neutral geometric description the prompt is prefixed with. ?session=<id>."""
    from starlette.responses import JSONResponse
    sess = _get_session(request.query_params.get("session"))
    mb = sess.measure_backend
    if mb is None or not getattr(mb, "model", None) or _get_properties is None:
        return JSONResponse({"features": []})
    try:
        ids = (await request.json()).get("shapeIds", []) or []
    except Exception:
        ids = []
    part_locs = sess.viewer.get("part_locs") or {}
    features = []
    for sid in ids:
        world_shape = mb.model.get(sid)
        if world_shape is None:
            continue
        try:
            # For a placed assembly part, edit-frame coords must be part-LOCAL:
            # recover them by un-applying the part's world placement. Single parts
            # (identity placement) stay world-framed with no assembly context.
            part_id = re.split(r"/(?:faces|edges|vertices)/", sid)[0]
            loc, placement = _placement_of(part_locs.get(part_id))
            if loc is not None:
                shape = cq.Shape.cast(world_shape).moved(loc.inverse).wrapped
                world_center = _feat_center(world_shape)
            else:
                shape, world_center = world_shape, None
            features.append(_describe_feature(sid, shape, world_center, placement))
        except Exception as e:
            _log_err(str(e), traceback.format_exc())
    return JSONResponse({"features": features})


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
            # No server→client push; 405 the optional inbound SSE so clients skip it.
            if scope.get("method") == "GET":
                from starlette.responses import PlainTextResponse
                await PlainTextResponse("Method Not Allowed", status_code=405)(scope, receive, send)
                return
        await self.app(scope, receive, send)


def _run_http():
    import uvicorn, logging
    # Drop only the benign 405'd inbound-SSE GET /mcp probes; keep 401s etc.
    class _DropMcpGet(logging.Filter):
        def filter(self, record):
            a = record.args
            return not (a and len(a) >= 5 and a[1] == "GET"
                        and str(a[2]).startswith("/mcp") and a[4] == 405)
    logging.getLogger("uvicorn.access").addFilter(_DropMcpGet())
    # Render injects PORT; fall back to MCP_PORT for manual local runs.
    mcp.settings.host = os.environ.get("MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("PORT", os.environ.get("MCP_PORT", "9000")))
    app = _AuthASGI(mcp.streamable_http_app())
    uvicorn.run(app, host=mcp.settings.host, port=mcp.settings.port)


# =============================================================================
# POSTHOG MCP ANALYTICS  — DISABLED
# =============================================================================
# Replaced by the Langfuse-native self-improvement system: the `report_learning`
# tool above (agent → developer), whose calls the web backend logs to Langfuse.
# The old PostHog `instrument()` hook injected a virtual `get_more_tools` tool
# ($mcp_missing_capability) and a per-call intent field ($mcp_intent); both are
# superseded. Kept commented for reference rather than deleted.
#
# def _setup_mcp_analytics():
#     key = os.environ.get("POSTHOG_KEY")
#     if not key:
#         return
#     try:
#         from posthog import Posthog
#         from posthog.mcp import instrument, MCPAnalyticsOptions, MCPAnalyticsContextOptions
#         client = Posthog(key, host=os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com"))
#         instrument(mcp, client, options=MCPAnalyticsOptions(
#             report_missing=True,
#             context=MCPAnalyticsContextOptions(
#                 description=(
#                     "Internal analytics only — never shown to the user and NOT a substitute for your reply. "
#                     "In one short phrase, why are you calling this tool?"
#                 )
#             ),
#         ))
#         atexit.register(client.shutdown)  # flush queued events on process exit
#     except Exception as e:
#         logging.getLogger(__name__).warning("MCP analytics disabled: %s", e)


if __name__ == "__main__":
    # _setup_mcp_analytics()  # PostHog MCP analytics disabled (see above)
    if os.environ.get("MCP_TRANSPORT", "stdio") == "http":
        _VIEWER_MODE = "http"
        # Each session seeds its own placeholder lazily (see _get_session), so the
        # viewer is never blank without a global boot-time model.
        _run_http()
    else:
        # stdio: push models to the standalone ocp_vscode viewer (run separately
        # via `python -m ocp_vscode`) on :3939. No placeholder/init needed.
        _VIEWER_MODE = "stdio"
        mcp.run(transport="stdio")
