You are improving the T2C MCP CAD server (Python) in THIS git worktree, so its recurring build failures stop. You edit the server CODE only — you do NOT have the t2c CAD tools; you reproduce behaviour with throwaway `mcp_server/.venv/bin/python` scripts.

Changes ALREADY made on this branch this run — do NOT repeat or re-document these:
{{PRIOR_COMMITS}}

The reviewer's VERIFIED issue list for this iteration (it already reproduced these against the real server). Each is tagged `root_cause` "server-limitation" or "modeling" — but even a "modeling" issue can have a server root cause (e.g. a docstring that misled the builder), so treat every one as a candidate to fix in the server:
{{ISSUES}}
{{UNRESOLVED_BLOCK}}
Your goal: resolve every issue that has a genuine SERVER root cause, this round. For each, EITHER fix it in the server OR decline it (below). Do the highest-severity / recurring blockers first; a real code FEATURE (a new helper, or turning a silent failure into a raised error) outranks an easy docstring tweak when a hard blocker recurs.

Fix each issue by editing only `mcp_server/src/` (plus `mcp_server/tests/` for a regression test):
- **Empirically verify the real behaviour BEFORE you write.** The description can be incomplete or wrong. Write a throwaway script run with `mcp_server/.venv/bin/python` that REPRODUCES the problem and CONFIRMS your fix; document/implement ONLY what you actually observed — never a guess.
- Docstring fix: the docstring is the model's only guide, so it must be exactly correct — state the real trigger and real symptom (raised error vs silent wrong result).
- Code fix: implement minimally; add/extend a test in `mcp_server/tests/` that fails without your change.
- Make a focused commit per fix (several commits this round is fine).

DECLINE an issue only if it is genuinely NOT a server problem — e.g. the builder misread a drawing dimension or miscounted parts, and no docstring or code change could have prevented it. State it in your reply as `DECLINE: <issue> — <one-line reason>`. Do not decline something a clearer doc or a new tool could have prevented.

Rules:
- Edit only `mcp_server/src/` and `mcp_server/tests/`. Never touch `web/`, `.github/`, `main`, or anything else.
- No refactors, renames, or speculative features.
- Run `mcp_server/.venv/bin/python -m pytest mcp_server/tests -q`; it must pass.
- **DELETE every throwaway/scratch file you created.** After committing, `git status` must show nothing but your intended `mcp_server/` changes.
- Commit with a Conventional Commit message and APPEND the tag `[iter {{ITER_LABEL}}]` to the subject, e.g. `fix(t2c): raise on perpendicular revolve axis [iter {{ITER_LABEL}}]`. No AI attribution, no `Co-Authored-By`.
- Reply with a short list: what you FIXED (one line each) and what you DECLINED (with reasons).

If NOTHING in the list is a genuine server limitation, make no change, do not commit, and reply exactly `NO-EDIT: <one-line reason>`.
