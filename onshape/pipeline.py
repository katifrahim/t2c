"""Orchestrate one Part Studio: classify -> extract -> normalize -> emit -> verify.

Only fully-supported models are geometrically verified against Onshape end to end
here; models with unsupported features report what they'd need (rollback-truncation
and richer feature coverage land in later milestones).

Usage:  python -m onshape.pipeline <part-studio-url> [--out FILE.json]
"""
from __future__ import annotations

import json
import sys

from onshape.classify import classify
from onshape.client import Onshape, parse_url
from onshape.emit import emit_model
from onshape.extract import resolve_created_faces
from onshape.normalize import _msg, normalize
from onshape.verify import fetch_ground_truth, run_steps, compare, Geometry

_MODIFIERS = {"fillet", "chamfer"}


def run(url: str, api: Onshape | None = None) -> dict:
    api = api or Onshape()
    ps = parse_url(url)

    cls = classify(ps, api)
    if not cls.native:
        return {"url": url, "status": "skipped", "reason": cls.reason}

    features = api.features(ps)
    sketches = api.sketches(ps)
    # Resolve modifier features' target edges to 3D points (one FeatureScript call).
    mod_ids = [_msg(f)["featureId"] for f in features.get("features", [])
               if _msg(f).get("featureType") in _MODIFIERS and not _msg(f).get("suppressed")]
    targets = resolve_created_faces(api, ps, mod_ids)
    model = normalize(features, sketches, url=url, targets=targets)
    steps = emit_model(model)

    out: dict = {
        "url": url,
        "status": "ok",
        "supported_ops": len(model.ops),
        "unsupported": model.unsupported,
        "steps": steps,
    }

    # Verify only when nothing was left unsupported (a self-contained reconstruction).
    if not model.unsupported and steps:
        truth = fetch_ground_truth(ps, api)
        try:
            props = run_steps(steps)
            res = compare(Geometry.from_mcp_props(props), truth)
            out["verified"] = res.ok
            out["verify"] = res.to_dict()
        except Exception as e:  # noqa: BLE001
            out["verified"] = False
            out["verify"] = {"ok": False, "reason": f"execution error: {e}"}
    else:
        out["verified"] = None
        out["verify"] = {"reason": "model has unsupported features; partial support only"}
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    url = argv[0]
    out_file = None
    if "--out" in argv:
        out_file = argv[argv.index("--out") + 1]

    result = run(url)
    summary = {k: v for k, v in result.items() if k != "steps"}
    print(json.dumps(summary, indent=2))
    if out_file:
        json.dump(result, open(out_file, "w"), indent=2)
        print(f"\nwrote {out_file} ({len(result.get('steps', []))} steps)")
    return 0 if result.get("verified") in (True, None) else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
