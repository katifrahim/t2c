#!/usr/bin/env python3
"""Extract the last fenced ```json {...}``` block from a `claude -p --output-format json`
result (the agent is told to end its reply with one). Writes the parsed object to argv[2],
or {} if none/unparseable. Callers apply their own field defaults, so an unparseable verdict
safely reads as "missing everything" rather than a false pass.
Usage: parse_json_block.py <claude_raw.json> <out.json>
"""
import json, re, sys

raw = json.load(open(sys.argv[1]))
text = raw.get("result") if isinstance(raw, dict) else str(raw)
text = text or ""
blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
cand = blocks[-1] if blocks else None
if cand is None:                                   # fallback: last {...} that looks like an object
    m = re.findall(r"\{.*\}", text, re.DOTALL)
    cand = m[-1] if m else None
out = {}
if cand:
    try:
        out = json.loads(cand)
    except json.JSONDecodeError:
        out = {}
open(sys.argv[2], "w").write(json.dumps(out, indent=2))
print(json.dumps(out)[:200])
