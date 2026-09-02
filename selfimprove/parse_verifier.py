#!/usr/bin/env python3
"""Pull the verifier's final ```json block out of a `claude -p --output-format json`
result. Writes {edits_work, resolved, unresolved, declined} to argv[2]. Same fenced-block
approach as parse_verdict.py (copied, not shared — two callers don't warrant an abstraction)."""
import json, re, sys

raw = json.load(open(sys.argv[1]))
text = raw.get("result") or ""
blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
cand = blocks[-1] if blocks else None
if cand is None:
    m = [x for x in re.findall(r"\{.*?\}", text, re.DOTALL) if '"edits_work"' in x]
    cand = m[-1] if m else None
out = {}
if cand:
    try:
        out = json.loads(cand)
    except json.JSONDecodeError:
        out = {}
# normalise: a broken/empty verdict must NOT read as "all clear"
out.setdefault("edits_work", False)
for k in ("resolved", "unresolved", "declined"):
    if not isinstance(out.get(k), list):
        out[k] = []
open(sys.argv[2], "w").write(json.dumps(out, indent=2))
print("edits_work=", out["edits_work"], "unresolved=", len(out["unresolved"]),
      "resolved=", len(out["resolved"]), "declined=", len(out["declined"]))
