#!/usr/bin/env python3
"""Count EVERY tool action an agent took, from its transcript jsonl (stream-json or the
copied session log). Actions only — thinking/text is ignored. Prints JSON:
  {"counts": {"<tool>": n, ...}, "total": int, "errors": int}
errors = tool_results flagged is_error (any tool). MCP names are shortened:
mcp__t2c__workplane_api -> workplane_api ; mcp__context7__query-docs -> context7:query-docs.
Usage: tool_stats.py <transcript.jsonl>
"""
import collections, json, sys

def short(name: str) -> str:
    if name.startswith("mcp__"):
        parts = name.split("__")            # ['mcp', '<server>', '<tool>']
        srv, tool = (parts[1] if len(parts) > 2 else ""), parts[-1]
        return tool if srv == "t2c" else f"{srv}:{tool}"
    return name

def stats(path: str) -> dict:
    counts = collections.Counter()
    errors = 0
    try:
        fh = open(path, encoding="utf-8")
    except OSError:
        return {"counts": {}, "total": 0, "errors": 0}
    for line in fh:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        for blk in (msg.get("content") or []):
            if not isinstance(blk, dict):
                continue
            if blk.get("type") == "tool_use":
                counts[short(str(blk.get("name", "")))] += 1
            elif blk.get("type") == "tool_result" and blk.get("is_error"):
                errors += 1
    return {"counts": dict(counts), "total": sum(counts.values()), "errors": errors}


if __name__ == "__main__":
    print(json.dumps(stats(sys.argv[1])))
