# t2c self-improvement loop (serial MVP)

One worktree = one drawing = one branch. A headless agent builds a CAD replica of a
drawing with the **t2c MCP server only**; a fresh-context agent grades it against the
drawing; when the root cause is a t2c limitation, an editor agent fixes the server
(docs or logic), verifies with `pytest`, and commits to this branch. Loop until the
judge scores ≥ target.

## Roles (each a separate, fresh `claude -p` process)
- **Modeler** (`prompts/modeler.md`) — MCP-only, cannot edit code. Builds, or fixes the
  live model. Ends with a `## Friction` note (where the tools fought it).
- **Judge** (`prompts/judge.md`) — fresh context, sees only the drawing + `calls.json`.
  Emits `{accuracy, summary, issues[{severity,description,root_cause}]}`.
- **Editor** (`prompts/editor.md`) — the only code-editor. Fixes `mcp_server/src/`, runs
  `pytest`, commits. Fires when the judge tags a `server-limitation` or the builder's
  friction is substantive. Rebuild-fresh after an edit *is* the verification.

## Why a per-worktree venv
`t2c_mcp` is editable-installed via a meta-path finder that maps `src` → **main's** src,
so pytest/imports would ignore worktree edits. `setup_worktree.sh` gives the worktree its
own tiny venv that shares main's heavy deps (cadquery/OCP/vtk via one path `.pth`) but
registers its own editable `src` → this worktree.

## Run
```bash
selfimprove/setup_worktree.sh                 # once per worktree
selfimprove/run.sh "/Users/apple/Desktop/Assy/Assy 6.pdf"
```
Tunables (env): `TARGET` (92), `MAX_ITERS` (5), `PORT` (9187), `SID`, `MCP_TOKEN`.
Per-iteration artifacts land in `runs/<ts>/` (git-ignored); the human-readable trail is
`SELFIMPROVE_LOG.md`.

## Extract tool calls from any session (standalone)
`extract_calls.py <capture.jsonl>` turns a `--output-format stream-json` capture (or a
`~/.claude/projects/.../<session>.jsonl` transcript) into ordered `{tool,input,result}`
JSON of just the t2c calls. `--selftest` runs a built-in check.

## Deferred (designed-for, not built)
Parallel worktrees (one drawing each, human-merged); richer judge inputs (offscreen PNG
renders + `inspect_model` geometry — venv already has vtk/OCP/PIL); a 24/7 queue driver.
