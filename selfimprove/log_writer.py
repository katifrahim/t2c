#!/usr/bin/env python3
"""Append a well-structured markdown block to SELFIMPROVE_LOG.md — one section per agent,
mirroring the loop's flow. All log formatting lives here so run.sh stays clean and the log
stays consistent. Full text, no truncation.

Sections (argv[2]) and their args:
  outer_header  N
  modeller      N  CALLS_JSON  FRICTION_JSON  TRANSCRIPT  CAD_STEP
  judge         N  TRANSCRIPT  JUDGE_JSON
  inner_header  N  M
  editor        N  M  TRANSCRIPT  EDITOR_JSON  N_ISSUES  N_UNRESOLVED  COMMITS
  verifier      N  M  TRANSCRIPT  VERIFIER_JSON
  outcome       OUTCOME  BEST  N
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tool_stats import stats  # noqa: E402


def load(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}


def usage(transcript):
    s = stats(transcript)
    items = sorted(s["counts"].items(), key=lambda kv: -kv[1])
    line = " · ".join(f"{k} ×{v}" for k, v in items) or "(none)"
    return f"{line} — {s['total']} calls, {s['errors']} errors"


def bullets(items, indent=""):
    items = [str(x).strip() for x in (items or []) if str(x).strip()]
    return "\n".join(f"{indent}- {x}" for x in items) if items else f"{indent}- (none)"


def main():
    log, section, a = sys.argv[1], sys.argv[2], sys.argv[3:]
    out = []
    if section == "outer_header":
        out = ["", f"### ▸ Outer Iteration {a[0]}"]

    elif section == "modeller":
        N, calls_p, fric_p, tr, cad = a
        c = load(calls_p); f = load(fric_p)
        cad_rel = os.path.relpath(cad, os.path.dirname(os.path.dirname(log))) if cad and os.path.exists(cad) else "(export failed)"
        out = [
            "", "#### Modeller",
            f"- **Input:** drawing `{os.path.basename(c.get('drawing') or '?')}`",
            f"- **Output:** model `{cad_rel}` · {c.get('n_calls', 0)} t2c calls · "
            f"{c.get('n_errors', 0)} t2c errors · {len(f.get('friction', []))} friction points",
            f"- **Build note:** {f.get('build_note', '(none)')}",
            f"- **Actions:** {usage(tr)}",
            "- **Friction (tool/doc gaps the build hit):**",
            bullets(f.get("friction", []), "  "),
        ]

    elif section == "judge":
        N, tr, jp = a
        j = load(jp)
        iss = j.get("issues", [])
        issue_lines = [f"[{i.get('severity')} / {i.get('root_cause')}] {(i.get('description') or '').strip()}" for i in iss]
        out = [
            "", "#### Judge",
            f"- **Input:** drawing + `calls.{N}.json` + `friction.{N}.json`",
            f"- **Verification effort:** {usage(tr)}",
            f"- **Accuracy: {j.get('accuracy', '?')}%**",
            f"- **Justification:** {(j.get('justification') or j.get('summary') or '(none)').strip()}",
            f"- **Issues (severity / root-cause — description) [{len(iss)}]:**",
            bullets(issue_lines, "  "),
        ]

    elif section == "inner_header":
        out = ["", f"#### ▸ Inner Iteration {a[0]}.{a[1]}"]

    elif section == "editor":
        N, M, tr, ep, nissues, nunres, commits = a
        e = load(ep)
        clines = [x for x in commits.splitlines() if x.strip()]
        out = [
            "", "##### Editor",
            f"- **Input:** {nissues} issues" + (f", {nunres} unresolved carried over" if int(nunres or 0) else ""),
            f"- **Actions:** {usage(tr)}",
            f"- **Fixed [{len(e.get('fixed', []))}]:**", bullets(e.get("fixed", []), "  "),
            f"- **Declined [{len(e.get('declined', []))}]:**", bullets(e.get("declined", []), "  "),
            "- **Commits this round:**", bullets(clines or ["(none)"], "  "),
        ]

    elif section == "verifier":
        N, M, tr, vp = a
        v = load(vp)
        r, u, d = v.get("resolved", []), v.get("unresolved", []), v.get("declined", [])
        out = [
            "", "##### Verifier",
            f"- **Actions:** {usage(tr)}",
            f"- **edits_work:** {v.get('edits_work', False)}",
            f"- **Resolved [{len(r)}]:**", bullets(r, "  "),
            f"- **Unresolved [{len(u)}]:**", bullets(u, "  "),
            f"- **Declined [{len(d)}]:**", bullets(d, "  "),
        ]

    elif section == "note":
        out = [f"- _{a[0]}_"]

    elif section == "outcome":
        outcome, best, N = a
        out = ["", f"### ▸ Outcome: **{outcome}** — best accuracy **{best}%** after {N} outer iteration(s)", ""]

    else:
        return
    open(log, "a", encoding="utf-8").write("\n".join(out) + "\n")


if __name__ == "__main__":
    main()
