You are improving the T2C MCP CAD server (Python) in THIS git worktree. Builds of an engineering drawing keep scoring below target. Your job is to make the T2C server itself better so the recurring failures stop.

Changes ALREADY made on this branch this run — do NOT repeat or re-document these:
{{PRIOR_COMMITS}}

EVERY friction note the builder has reported so far this run (oldest first). Issues that RECUR across several notes are your highest-value targets:
{{ALL_FRICTION}}

The independent reviewer's "server-limitation" issues from the latest round:
{{SERVER_ISSUES}}

Choose the SINGLE highest-value problem to fix now, prioritising in this order:
1. A problem that RECURS across several friction notes (a persistent blocker beats a one-off complaint).
2. Higher severity / larger fidelity impact.
3. Something NOT already covered by the changes listed above.
A hard blocker that needs a real code FEATURE (a new helper method, or turning a silent failure into a clear error) OUTRANKS an easy docstring tweak. Do not keep picking the easy doc fix while a bigger blocker recurs round after round.

Then fix exactly that one problem, editing only files under `mcp_server/src/` (plus `mcp_server/tests/` for a regression test):

- **You MUST empirically verify the real behaviour before you write anything.** The builder's friction is a symptom report and is often MISDIAGNOSED (e.g. it blames "the axis touching the profile" when the true cause is a *perpendicular* axis). Write a throwaway script run with `mcp_server/.venv/bin/python` that REPRODUCES the reported failure and CONFIRMS your fix, and document/parse ONLY what you actually observed — never the builder's guess verbatim.
- If the fix is a **docstring**: the docstring is the model's ONLY guide, so it must be exactly correct — cite the real trigger and the real symptom (raised error vs silent wrong result) you observed.
- If the fix is **code** (new capability, or converting a silent degenerate result into a raised error, or a genuine bug): implement it minimally and add/extend a test in `mcp_server/tests/` that fails without your change.

Rules:
- Edit only `mcp_server/src/` and `mcp_server/tests/`. Never touch `web/`, `.github/`, `main`, or anything else.
- No refactors, renames, or speculative features. Change only what removes this one blocker.
- Run `mcp_server/.venv/bin/python -m pytest mcp_server/tests -q` from the worktree root; it must pass.
- **DELETE every throwaway/scratch file you created** (verification scripts, temp models, `slottest.py`-style files). After committing, `git status` must show nothing but your intended `mcp_server/` change.
- Commit to this branch with a Conventional Commit message (`fix(t2c)`/`docs(t2c)`/`feat(t2c)`: ...). No AI attribution, no `Co-Authored-By`.
- Reply with ONE line: what you changed and why.

If, after investigating, nothing here is genuinely a T2C server limitation, make no change, do not commit, and reply exactly `NO-EDIT: <one-line reason>`.
