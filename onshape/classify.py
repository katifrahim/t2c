"""Classify a Part Studio as natively-built vs. containing imported geometry.

Only end-to-end native models carry a usable parametric design history. A model is
NON-native if its feature tree contains an import-family feature:
  - importForeign  — foreign CAD (STEP/IGES/Parasolid/mesh/...) with no history
  - importDerived  — geometry derived from another element (history lives elsewhere)
  - import         — generic import

Empirically grounded (onshape/README.md): the top-level `imports` field is NOT a
signal — every model lists `onshape/std/geometry.fs` there (the standard library).
The reliable signal is the feature type itself.

Usage:  python -m onshape.classify <part-studio-url>
"""
from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field

from onshape.client import Onshape, PartStudio, parse_url

# Substring match (lowercased) against featureType. "import" covers importForeign /
# importDerived / import; "derived" covers derive features that reference external geometry.
_IMPORT_MARKERS = ("import", "derived")


@dataclass
class Classification:
    native: bool
    reason: str
    ps: PartStudio
    feature_count: int = 0
    import_features: list[dict] = field(default_factory=list)  # {index, type, name, suppressed}
    type_counts: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "native": self.native,
            "reason": self.reason,
            "url": self.ps.url,
            "feature_count": self.feature_count,
            "import_features": self.import_features,
            "type_counts": self.type_counts,
        }


def _is_import_type(ft: str) -> bool:
    ft = (ft or "").lower()
    return any(m in ft for m in _IMPORT_MARKERS)


def classify(ps: PartStudio, api: Onshape | None = None) -> Classification:
    api = api or Onshape()
    feats = api.features(ps).get("features", [])
    type_counts = dict(Counter(f.get("featureType") for f in feats))

    imports = [
        {
            "index": i,
            "type": f.get("featureType"),
            "name": f.get("name"),
            "suppressed": bool(f.get("suppressed")),
        }
        for i, f in enumerate(feats)
        if _is_import_type(f.get("featureType"))
    ]

    # An import feature is disqualifying only if it actually contributes geometry
    # (a suppressed import is inert).
    active = [im for im in imports if not im["suppressed"]]
    if active:
        native = False
        reason = "imported geometry: " + ", ".join(
            f"{im['type']}({im['name']})" for im in active
        )
    else:
        native = True
        reason = "no import-family features"
        if imports:  # suppressed imports present but inert
            reason += " (suppressed imports ignored)"

    return Classification(
        native=native,
        reason=reason,
        ps=ps,
        feature_count=len(feats),
        import_features=imports,
        type_counts=type_counts,
    )


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    import json

    c = classify(parse_url(argv[0]))
    print(json.dumps(c.to_dict(), indent=2))
    return 0 if c.native else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
