#!/usr/bin/env python3
"""Pull the judge's final ```json verdict block out of a `claude -p --output-format json`
result. Writes the parsed verdict to argv[2]. Robust to prose before/after the block."""
import json, re, sys

raw = json.load(open(sys.argv[1]))
text = raw.get("result") or ""
# Prefer the LAST fenced ```json { ... } ``` block (the judge is told to end with it).
blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
cand = blocks[-1] if blocks else None
if cand is None:                                   # fallback: last brace group mentioning accuracy
    m = [x for x in re.findall(r"\{.*?\}", text, re.DOTALL) if '"accuracy"' in x]
    cand = m[-1] if m else None
out = {}
if cand:
    try:
        out = json.loads(cand)
    except json.JSONDecodeError:
        out = {}
open(sys.argv[2], "w").write(json.dumps(out, indent=2))
iss = out.get("issues")
print("accuracy=", out.get("accuracy"), "issues=", len(iss) if isinstance(iss, list) else 0)
