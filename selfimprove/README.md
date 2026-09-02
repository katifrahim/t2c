# t2c self-improvement loop (serial MVP)

One worktree = one drawing = one branch. Four fresh-context `claude -p` agents improve the
**t2c server** through a build → judge → (editor↔verifier) loop until the model scores ≥95%
or no server fixes remain. Everything a build reveals is fuel for improving the server.

There is **no LLM orchestrator** — `run.sh` (a bash script) sequences the agents. They share
nothing in memory; they hand off through files in `runs/<ts>/` (a blackboard) and through
git. The flow is always **build → judge → editor**, never judge → modeler: fixing one model
would patch the symptom, not the server that misled it.

## Roles & access matrix (each a separate, fresh `claude -p`, isolated context)
| Agent | t2c MCP | Read `mcp_server/src` | Edit code |
|---|---|---|---|
| **Modeller** | ✅ (stdio, worktree code) | ❌ enforced (scratch cwd) | ❌ |
| **Judge** | ✅ | ✅ read | ❌ |
| **Editor** | ❌ | ✅ read+write | ✅ |
| **Verifier** | ✅ | ✅ read | ❌ |

- **Modeller** (`prompts/modeler.md`) — builds with t2c only; runs in a scratch cwd so it
  cannot read the server source. Ends with a `## Friction` self-report of missing/wrong
  tools/docs (a signal the tool calls alone don't reveal). Fresh stdio t2c per build ⇒ clean
  store + auto-loads the editor's latest commit.
- **Judge** (`prompts/judge.md`) — sees drawing + `calls.json` (reasoning stripped, so it
  stays unbiased) **+ friction**. It has t2c + read-source, so it **empirically verifies**
  each friction claim (reproduce via t2c / read source) before folding it into one issue
  list. `accuracy` stays purely geometric. Output: prose + fenced JSON
  `{accuracy, summary, issues[{severity,description,root_cause}]}`.
- **Editor** (`prompts/editor.md`) — the only code-editor, **no t2c**. Gets the judge's
  verified issue list (+ any verifier `unresolved` + prior commits). Fixes each server root
  cause or **declines** genuine non-server issues; empirically reproduces before writing;
  runs `pytest`, deletes scratch, commits `[iter N.M]`.
- **Verifier** (`prompts/verifier.md`) — independent check (t2c + read-source, no edit).
  Confirms the editor's edits actually work on the live server and classifies each issue
  `resolved` / `unresolved` / `declined`. Drives the inner loop until nothing is `unresolved`.

## Artifacts per iteration (`runs/<ts>/`, git-ignored)
`modeller.N.transcript.jsonl` (stream-json = full transcript + extractor input) ·
`calls.N.json` (extracted t2c calls) · `friction.N.md` (modeller self-report, → judge) ·
`judge.N.{raw.json,json,transcript.jsonl}` · `editor.N.M.{json,transcript.jsonl}` ·
`verifier.N.M.{raw.json,json,transcript.jsonl}`. Non-modeller `*.transcript.jsonl` are
Claude Code's own session logs, copied in by `session_id`. Commit tag `[iter N.M]` joins
commits ↔ transcripts ↔ artifacts at merge time.

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
