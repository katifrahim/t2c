"""Engine-neutral intermediate representation (IR).

The IR is the contract between extraction (Onshape-specific) and emission
(MCP-specific). It carries only geometry-level intent in millimetres — planes,
2D profiles, and typed operations — with each operation's geometric targets
resolved to concrete 3D points so emission never depends on opaque topological ids.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Canonical planes CadQuery/​the MCP name directly.
_CANON = {
    (0, 0, 1): "XY", (0, 0, -1): "XY",
    (0, 1, 0): "XZ", (0, -1, 0): "XZ",
    (1, 0, 0): "YZ", (-1, 0, 0): "YZ",
}


@dataclass
class Plane:
    """A sketch/work plane: a canonical name when axis-aligned at the origin, else a
    full origin+xDir+normal frame the MCP can build with {"_type": "Plane", ...}."""

    name: str | None = None          # "XY"/"XZ"/"YZ" when canonical
    origin: list[float] | None = None
    x_dir: list[float] | None = None
    normal: list[float] | None = None

    @classmethod
    def from_matrix(cls, matrix: list[float] | None) -> "Plane":
        """Onshape sketchMatrix is a 16-element row-major 4x4. Columns 0/1/2 are the
        plane's x/y/z (normal) axes; column 3 is the origin (metres → mm)."""
        if not matrix or len(matrix) < 16:
            return cls(name="XY")
        origin = [matrix[3] * 1e3, matrix[7] * 1e3, matrix[11] * 1e3]
        x_dir = [matrix[0], matrix[4], matrix[8]]
        normal = [matrix[2], matrix[6], matrix[10]]
        key = tuple(round(n) for n in normal)
        if all(abs(o) < 1e-6 for o in origin) and key in _CANON:
            return cls(name=_CANON[key])
        return cls(origin=[round(o, 6) for o in origin],
                   x_dir=[round(d, 9) for d in x_dir],
                   normal=[round(d, 9) for d in normal])

    def is_canonical(self) -> bool:
        return self.name is not None


# --- profile curves (all coords in mm, on the sketch's local 2D frame) -----------
@dataclass
class Curve:
    kind: str                        # "line" | "arc" | "circle"
    data: dict = field(default_factory=dict)


@dataclass
class Profile:
    """One closed region of a sketch (outer boundary; inner boundaries = holes)."""

    curves: list[Curve] = field(default_factory=list)


# --- operations ------------------------------------------------------------------
@dataclass
class Op:
    """Base for every IR operation. `kind` discriminates; `source` keeps the Onshape
    feature id/name/type for traceability and debugging."""

    kind: str
    source: dict = field(default_factory=dict)


@dataclass
class Sketch(Op):
    plane: Plane = field(default_factory=Plane)
    profiles: list[Profile] = field(default_factory=list)
    kind: str = "sketch"


@dataclass
class Extrude(Op):
    profile_ref: str = ""            # which sketch this extrudes
    distance: float = 0.0            # mm; signed for direction
    symmetric: bool = False
    op: str = "new"                  # new | add | cut | intersect
    taper: float | None = None
    kind: str = "extrude"


@dataclass
class Revolve(Op):
    profile_ref: str = ""
    axis_start: list[float] = field(default_factory=lambda: [0, 0, 0])
    axis_end: list[float] = field(default_factory=lambda: [0, 0, 1])
    angle: float = 360.0
    op: str = "new"
    kind: str = "revolve"


@dataclass
class Fillet(Op):
    # One representative 3D point per target edge (from FeatureScript), so emission
    # selects edges by NearestToPoint instead of any opaque id.
    edge_points: list[list[float]] = field(default_factory=list)
    radius: float = 0.0
    kind: str = "fillet"


@dataclass
class Chamfer(Op):
    edge_points: list[list[float]] = field(default_factory=list)
    distance: float = 0.0
    kind: str = "chamfer"


@dataclass
class CircularPattern(Op):
    # Patterns the most-recent body around an axis into `count` instances.
    count: int = 1
    angle: float = 360.0          # degrees swept
    equal_space: bool = True
    axis_origin: list[float] = field(default_factory=lambda: [0, 0, 0])
    axis_dir: list[float] = field(default_factory=lambda: [0, 0, 1])
    kind: str = "circular_pattern"


@dataclass
class Boolean(Op):
    op: str = "union"             # union | cut | intersect
    kind: str = "boolean"


@dataclass
class Model:
    """A whole Part Studio, normalized to an ordered op list. `unsupported` records
    features we could not faithfully translate (drives rollback-truncation)."""

    units: str = "millimeter"
    ops: list[Op] = field(default_factory=list)
    source_url: str = ""
    unsupported: list[dict] = field(default_factory=list)  # {index, type, name, reason}
