# t2c self-improvement loop (serial MVP)

One worktree = one drawing = one branch. A headless agent builds a CAD replica of a
drawing with the **t2c MCP server only**; a fresh-context agent grades it against the
drawing; then an editor agent fixes the **server** root cause (a modeling mistake is a
symptom of a weak/misleading tool or doc), verifies with `pytest`, and commits to this
branch. Rebuild on the improved code. Loop until ≥ target or no server fixes remain.

There is **no LLM orchestrator** — `run.sh` (a bash script) sequences the three agents.
They share nothing in memory; they hand off through files in `runs/<ts>/` (a blackboard)
and through git (the server code). The flow is always **build → judge → editor**, never
judge → modeler: fixing one model would patch the symptom, not the server that misled it.

## Roles (each a separate, fresh `claude -p` process — isolated context)
- **Modeler** (`prompts/modeler.md`) — MCP-only, cannot edit code (`dontAsk` + t2c tools).
  Uses a fresh **stdio** t2c spawned from THIS worktree's code, so every build runs the
  latest committed edits with a clean store. Ends with a `## Friction` note — its own
  report of where a capability/doc was missing (signal the tool calls alone don't show).
- **Judge** (`prompts/judge.md`) — fresh context, sees only the drawing + `calls.json`
  (reasoning stripped, so it stays unbiased). Emits prose + a fenced JSON verdict
  `{accuracy, summary, issues[{severity,description,root_cause}]}`.
- **Editor** (`prompts/editor.md`) — the only code-editor. Gets ALL judge issues + the
  full friction history this run + the run's prior commits; picks the highest-value
  RECURRING blocker; **empirically reproduces** the behaviour before writing (the builder
  often misdiagnoses); fixes `mcp_server/src/`, runs `pytest`, deletes scratch, commits.

## Artifacts per iteration (`runs/<ts>/`, git-ignored)
`model.N.jsonl` (modeler's full stream-json transcript) · `calls.N.json` (extracted t2c
calls, judge input) · `friction.N.md` (modeler's self-report) · `judge.N.{raw,}.json` +
`judge.N.transcript.jsonl` · `editor.N.json` + `editor.N.transcript.jsonl`. The
`*.transcript.jsonl` are Claude Code's own full session logs, copied in for debugging.

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
Tunables (env): `TARGET` (92), `MAX_ITERS` (6). No ports/tokens — t2c runs over stdio.
The human-readable trail is `SELFIMPROVE_LOG.md`.

## Extract tool calls from any session (standalone)
`extract_calls.py <capture.jsonl>` turns a `--output-format stream-json` capture (or a
`~/.claude/projects/.../<session>.jsonl` transcript) into ordered `{tool,input,result}`
JSON of just the t2c calls. `--selftest` runs a built-in check.

## Deferred (designed-for, not built)
Parallel worktrees (one drawing each, human-merged); richer judge inputs (offscreen PNG
renders + `inspect_model` geometry — venv already has vtk/OCP/PIL); a 24/7 queue driver.
