You improve the T2C MCP CAD server (Python) in this git worktree. A critical review of the server's behaviour while it was used to build a CAD model produced the issue list below. Your job: for each issue with a genuine root cause in the server, fix that root cause in the server code, so the same problem stops happening.

Changes already committed to the server this session — do NOT repeat or re-document these:
{{PRIOR_COMMITS}}

The issues to address (each tagged `severity` / `source`, where source is provenance: "friction-derived" = a tool/doc problem already reproduced against the server, "comparison-derived" = a drawing-vs-model discrepancy. ANY issue — whatever its source — may have a server root cause, e.g. a docstring that misled the build, so determine the real cause of each one yourself and weigh them all):
{{ISSUES}}
{{UNRESOLVED_BLOCK}}
Your tools: you can edit files under `mcp_server/src/` (and add tests under `mcp_server/tests/`), run Python and pytest, run git, and search the web / library docs. You also have the T2C CAD tools themselves — use them to REPRODUCE how the server behaves.

CRITICAL about the T2C tools you can call: they run the server code as it was when this session started and do NOT reflect edits you make now (no hot-reload). So: use the T2C tools to *reproduce* a reported problem as it originally occurs; but *verify your fix* with a throwaway Python script and `pytest` (a fresh `import` picks up your edit) — the T2C tools will still show the old behaviour after you edit.

For each issue, EITHER fix it OR decline it:
- **Fix:** first REPRODUCE the real behaviour (call the T2C tool and/or run a small Python script and read the exact source) — the issue description can be incomplete or wrong, so document/implement only what you actually observe. Then make the MINIMAL change: correct a docstring (it must be exactly true against real behaviour — cite the real trigger and symptom, raised-error vs silent-wrong-result), or fix the tool logic (implement minimally; add a `mcp_server/tests/` test that fails without your change). Prefer a real capability/clear-error fix over an easy doc tweak when a hard blocker recurs.
- **Decline:** only if the issue is genuinely NOT a server problem — e.g. the drawing was misread, a part miscounted, or a modelling choice no docstring/code change could have prevented. Do not decline something a clearer doc or a new capability could have prevented.

Rules:
- Edit only `mcp_server/src/` and `mcp_server/tests/`. Never touch `web/`, `.github/`, `main`, or anything else.
- No refactors, renames, or speculative features. Change only what removes a real blocker.
- Run `mcp_server/.venv/bin/python -m pytest mcp_server/tests -q`; it must pass.
- **DELETE every throwaway/scratch file you created.** After committing, `git status` must show nothing but your intended `mcp_server/` changes.
- Commit each fix with a Conventional Commit message and APPEND the tag `[iter {{ITER_LABEL}}]` to the subject, e.g. `fix(t2c): raise on perpendicular revolve axis [iter {{ITER_LABEL}}]`. No AI attribution, no `Co-Authored-By`. Several small commits this session is fine.
- Do not use the report_learning tool.

End your reply with a SINGLE fenced JSON block and nothing after it:

```json
{
  "fixed": ["one line per issue you fixed: what you changed and why it is correct"],
  "declined": ["one line per issue you declined: the issue + why it is not a server problem"],
  "commits": ["<short-sha> <commit subject>", "..."]
}
```

If NOTHING in the list is a genuine server problem, make no change, do not commit, leave `fixed`/`commits` empty and list everything under `declined`.
