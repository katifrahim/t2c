#!/usr/bin/env python3
"""Extract t2c MCP tool calls + results from a Claude Code capture, as ordered JSON.

Input is either:
  - a `claude -p --output-format stream-json` capture (newline-delimited events), or
  - a session transcript ~/.claude/projects/<slug>/<session>.jsonl
Both share the same shape: records with message.content[] holding tool_use / tool_result
blocks. We keep tool_use blocks whose name contains "t2c", match each to its tool_result
by tool_use_id, and emit them in file (chronological) order.

Usage:
    extract_calls.py <capture.jsonl> [--drawing PATH] [-o out.json]
    extract_calls.py --selftest
"""
import argparse, json, re, sys
from datetime import datetime, timezone


def _result_text(content):
    """tool_result.content is a str or a list of {type:text,text:..} blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return "" if content is None else str(content)


def extract(records):
    """records: iterable of parsed JSON objects (one per line). Returns (calls, friction)."""
    calls, order = {}, []          # tool_use_id -> call dict, plus emission order
    friction = ""
    for rec in records:
        if rec.get("type") == "result" and isinstance(rec.get("result"), str):
            friction = rec["result"]          # stream-json final message
        msg = rec.get("message")
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for blk in content:
            if not isinstance(blk, dict):
                continue
            t = blk.get("type")
            if t == "tool_use" and "t2c" in str(blk.get("name", "")):
                tid = blk.get("id")
                calls[tid] = {"seq": len(order) + 1, "tool": blk.get("name"),
                              "input": blk.get("input"), "result": None, "is_error": None}
                order.append(tid)
            elif t == "tool_result":
                tid = blk.get("tool_use_id")
                if tid in calls:
                    calls[tid]["result"] = _result_text(blk.get("content"))
                    calls[tid]["is_error"] = bool(blk.get("is_error"))
            elif t == "text" and msg.get("role") == "assistant" and isinstance(blk.get("text"), str):
                friction = blk["text"]         # transcript fallback: last assistant text
    return [calls[t] for t in order], friction


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("capture", nargs="?")
    ap.add_argument("--drawing", default=None)
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--friction-out", default=None)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()

    if a.selftest:
        _selftest()
        return

    if not a.capture:
        ap.error("capture file required (or --selftest)")
    records = []
    with open(a.capture, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    calls, friction = extract(records)
    out = {"drawing": a.drawing, "generated_at": datetime.now(timezone.utc).isoformat(),
           "source": a.capture, "n_calls": len(calls),
           "n_errors": sum(1 for c in calls if c["is_error"]), "calls": calls}
    text = json.dumps(out, indent=2, ensure_ascii=False)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    else:
        print(text)
    if a.friction_out:
        # The modeller ends with a fenced ```json {"friction":[...], "build_note":"..."} block.
        fj = {"friction": [], "build_note": ""}
        blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", friction or "", re.DOTALL)
        if blocks:
            try:
                d = json.loads(blocks[-1])
                if isinstance(d.get("friction"), list):
                    fj["friction"] = [str(x) for x in d["friction"]]
                fj["build_note"] = str(d.get("build_note", ""))
            except json.JSONDecodeError:
                pass
        if not fj["friction"] and not fj["build_note"]:      # fallback: no valid block
            fj["build_note"] = (friction or "").strip().splitlines()[0][:200] if friction else ""
        open(a.friction_out, "w", encoding="utf-8").write(json.dumps(fj, indent=2, ensure_ascii=False))


def _selftest():
    recs = [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u1", "name": "mcp__t2c__workplane_api",
             "input": {"operations": [{"method": "box", "args": [1, 1, 1]}]}}]}},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "u1",
             "content": [{"type": "text", "text": "{\"status\":\"success\"}"}], "is_error": False}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "u2", "name": "Read", "input": {"file_path": "/x"}}]}},
        {"type": "result", "result": "Done.\n## Friction\nquery_docs lacked X."},
    ]
    calls, friction = extract(recs)
    assert len(calls) == 1, calls                       # Read is dropped, only t2c kept
    assert calls[0]["tool"] == "mcp__t2c__workplane_api"
    assert calls[0]["result"] == '{"status":"success"}'
    assert calls[0]["is_error"] is False
    assert "Friction" in friction
    print("selftest OK")


if __name__ == "__main__":
    main()
