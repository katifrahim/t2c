You are improving the T2C MCP CAD server (Python) in THIS git worktree. A CAD build of an engineering drawing scored below target. An independent reviewer flagged issues whose root cause looks like a T2C **server limitation** — a missing capability, buggy tool logic, or wrong / insufficient tool documentation (the docstrings that are sent to the model as its only context for how the tools work).

Reviewer issues tagged "server-limitation":
{{SERVER_ISSUES}}

The builder's own friction note (where the tools fought it):
{{FRICTION}}

Your job:
1. Read `mcp_server/src/t2c_mcp.py` (and `direct_edit.py` / `step_import.py` if relevant). Diagnose the REAL root cause of the flagged limitation. The docstrings of the big tools (workplane_api, sketch_api, assembly_api, extension_api) are the model's only guide — a wrong, missing, or confusing docstring IS a real bug worth fixing.
2. Make the MINIMAL, well-justified change that removes the limitation: fix or clarify a tool docstring, or fix the tool's logic. Do NOT refactor, rename, reorganize, or add speculative features. Change only what this drawing needed. Edit only files under `mcp_server/src/`.
3. Do NOT touch `web/`, `.github/`, `main`, or anything outside `mcp_server/src/`.
4. Verify: run `mcp_server/.venv/bin/python -m pytest mcp_server/tests -q` from the worktree root and make sure it passes. If your change could regress behaviour, that gate must stay green.
5. Commit your change to this branch with a Conventional Commit message (`fix(t2c): ...`, `docs(t2c): ...`, or `feat(t2c): ...`). No AI attribution lines, no `Co-Authored-By`.
6. Reply with ONE line: what you changed and why.

If, after investigating, the issue is genuinely NOT a server limitation (it was a modeling mistake the builder could have avoided with the current tools), make no code change, do not commit, and reply exactly `NO-EDIT: <one-line reason>`.
