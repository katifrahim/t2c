"""Execute emitted MCP steps through the real t2c_mcp engine and compare the
resulting solid to Onshape's own geometry. This closed loop is the reliability
guarantee: a template is kept only if it provably reproduces the source model.

Onshape reports mass-properties in SI (metres); the MCP/CadQuery engine works in
millimetres, so ground truth is converted to mm here before comparison.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import os
import sys
from dataclasses import dataclass, field

from onshape.client import Onshape, PartStudio

# Import the real MCP server module (its engine is the production code path).
_MCP_SRC = os.path.join(os.path.dirname(__file__), "..", "mcp_server", "src")
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)
import t2c_mcp as _mcp  # noqa: E402


# --- executing steps in-process --------------------------------------------------
def _tool(name: str):
    fn = getattr(_mcp, name, None)
    if not inspect.iscoroutinefunction(fn):
        raise RuntimeError(f"MCP tool '{name}' is not a callable coroutine")
    return fn


def _reset_session() -> None:
    """Drop the local session so each model executes from an empty workspace."""
    _mcp._sessions.pop(_mcp._LOCAL_SID, None)


async def _run_steps_async(steps: list[dict]) -> dict:
    """Execute steps ({toolName, input}) in order; return the final object's props."""
    _reset_session()
    last = None
    for i, step in enumerate(steps):
        name = step["toolName"]
        payload = dict(step["input"])
        out = await _tool(name)(ctx=None, **payload)
        res = json.loads(out) if isinstance(out, str) else out
        if res.get("status") != "success":
            raise RuntimeError(f"step {i} ({name}) failed: {res.get('message') or res}")
        last = res
    if last is None:
        raise RuntimeError("no steps executed")
    return last.get("properties", {})


def run_steps(steps: list[dict]) -> dict:
    return asyncio.run(_run_steps_async(steps))


# --- ground truth ----------------------------------------------------------------
@dataclass
class Geometry:
    volume: float          # mm^3
    area: float            # mm^2
    center: list[float]    # mm
    bbox: dict             # {xmin..zmax} in mm

    @staticmethod
    def from_mcp_props(p: dict) -> "Geometry":
        return Geometry(
            volume=p.get("volume", 0.0),
            area=p.get("area", 0.0),
            center=p.get("center", [0, 0, 0]),
            bbox=p.get("bounding_box", {}),
        )


def _si_scalar(v, factor):
    """Onshape returns some scalars as [value, low, high]; take the value."""
    x = v[0] if isinstance(v, (list, tuple)) else v
    return float(x) * factor


def fetch_ground_truth(ps: PartStudio, api: Onshape | None = None) -> Geometry:
    api = api or Onshape()
    mp = api.massproperties(ps)
    body = mp.get("bodies", {}).get("-all-") or next(iter(mp.get("bodies", {}).values()), {})
    volume = _si_scalar(body.get("volume", [0]), 1e9)   # m^3 -> mm^3
    # Onshape reports solid surface area under "periphery" (m^2), not "area".
    area = _si_scalar(body.get("periphery", body.get("area", [0])), 1e6)
    centroid = [c * 1e3 for c in (body.get("centroid") or [0, 0, 0])[:3]]  # m -> mm
    try:
        bb = api.bounding_boxes(ps)
        bbox = {
            "xmin": bb["lowX"] * 1e3, "ymin": bb["lowY"] * 1e3, "zmin": bb["lowZ"] * 1e3,
            "xmax": bb["highX"] * 1e3, "ymax": bb["highY"] * 1e3, "zmax": bb["highZ"] * 1e3,
        }
    except Exception:
        bbox = {}
    return Geometry(volume=volume, area=area, center=centroid, bbox=bbox)


# --- comparison ------------------------------------------------------------------
@dataclass
class VerifyResult:
    ok: bool
    reason: str
    deltas: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"ok": self.ok, "reason": self.reason, "deltas": self.deltas}


def _rel(a, b):
    denom = max(abs(b), 1e-9)
    return abs(a - b) / denom


def compare(candidate: Geometry, truth: Geometry, *, vol_tol=0.01, area_tol=0.02,
            bbox_tol_mm=0.2) -> VerifyResult:
    """Pass if volume & surface area are within tolerance and the bounding box
    extents agree. Absolute bbox tol absorbs the engine's ~1e-7 tessellation pad."""
    dv, da = _rel(candidate.volume, truth.volume), _rel(candidate.area, truth.area)
    deltas = {
        "volume": {"candidate": candidate.volume, "truth": truth.volume, "rel": dv},
        "area": {"candidate": candidate.area, "truth": truth.area, "rel": da},
    }
    fails = []
    if dv > vol_tol:
        fails.append(f"volume off {dv:.3%}")
    if da > area_tol:
        fails.append(f"area off {da:.3%}")
    if candidate.bbox and truth.bbox:
        worst = 0.0
        for k in ("xmin", "ymin", "zmin", "xmax", "ymax", "zmax"):
            if k in candidate.bbox and k in truth.bbox:
                worst = max(worst, abs(candidate.bbox[k] - truth.bbox[k]))
        deltas["bbox_max_abs_mm"] = worst
        if worst > bbox_tol_mm:
            fails.append(f"bbox off {worst:.3f}mm")
    ok = not fails
    return VerifyResult(ok=ok, reason="match" if ok else "; ".join(fails), deltas=deltas)


def verify(steps: list[dict], ps: PartStudio, api: Onshape | None = None,
           **tol) -> VerifyResult:
    """Full loop: run steps, fetch Onshape geometry, compare."""
    api = api or Onshape()
    truth = fetch_ground_truth(ps, api)
    try:
        props = run_steps(steps)
    except Exception as e:  # execution failure is a verification failure
        return VerifyResult(ok=False, reason=f"execution error: {e}")
    return compare(Geometry.from_mcp_props(props), truth, **tol)
