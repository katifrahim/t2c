# t2c self-improvement loop

One worktree = one drawing = one branch. Four fresh-context `claude -p` agents improve the
**t2c server** through a build → judge → (editor↔verifier) loop until the model scores ≥95%,
accuracy stagnates for 4 builds, or `MAX_ITERS`. Everything a build reveals is fuel for
improving the server.

There is **no LLM orchestrator** — `run.sh` (a bash script) sequences the agents. They share
nothing in memory; they hand off through files in `runs/<ts>/` (a blackboard) and through git.
The flow is always **build → judge → editor↔verifier**, never judge → modeller: fixing one
model would patch the symptom, not the server that misled it.

Each agent's prompt is written **first-person** — it knows only its own task and never that
other agents or a loop exist. Every input is defined by *what it is*, not who produced it.

## Roles & access matrix (each a fresh, isolated `claude -p`)
| Agent | t2c MCP | Read `mcp_server/src` | Edit code | Docs lookup |
|---|---|---|---|---|
| **Modeller** | ✅ (incl. `report_learning`, mirrors prod) | ❌ enforced (scratch cwd) | ❌ | — |
| **Judge** | ✅ | ✅ read | ❌ | context7 + WebSearch |
| **Editor** | ✅ (to reproduce) | ✅ read+write | ✅ | context7 + WebSearch |
| **Verifier** | ✅ (+ read-only git) | ✅ read | ❌ | context7 + WebSearch |

- **All four get t2c.** Bugs in the MCP dispatch/reporting layer only reproduce *through* the
  t2c interface, so the Editor reproduces there too. Its t2c reflects the code as of session
  start (stdio, no hot-reload), so it reproduces with t2c but verifies its *fix* with python/pytest.
- `report_learning` stays on the **Modeller** (prod parity; the Judge reads its calls, so nothing
  escapes the loop) and is removed from the other three (no one reads their calls → data could leak out).
- **Modeller** (`prompts/modeler.md`) — builds with t2c only, in a scratch cwd so it cannot read
  the source. Ends with a fenced-json `{friction, build_note}` self-report of tool/doc gaps.
- **Judge** (`prompts/judge.md`) — drawing + `calls.json` + friction. Scores accuracy purely on
  geometry, then **empirically verifies** each friction claim via t2c/source and folds only the
  reproduced ones into one issue list. Emits `{accuracy, justification, issues[]}`.
- **Editor** (`prompts/editor.md`) — fixes each server root cause or **declines** genuine
  non-server issues; reproduces via t2c, verifies fixes via pytest, deletes scratch, commits
  `[iter N.M]`. Emits `{fixed, declined, commits}`.
- **Verifier** (`prompts/verifier.md`) — reads the actual commit diffs (git) + reproduces on the
  new code (t2c); classifies each issue `resolved`/`unresolved`/`declined` + `edits_work`. Drives
  the inner loop until nothing is `unresolved`.

## Guarantees `run.sh` enforces
- **HARD-EXITS** if `claude` is not `claude-patched` (spawned agents need full, untruncated MCP
  tool docstrings) with a message to repin. Exports `ENABLE_TOOL_SEARCH=0` so all t2c schemas load up front.
- Modeller wall: scratch cwd + `--add-dir` only the drawing folder ⇒ it cannot read `mcp_server/src`.

## Artifacts per iteration (`runs/<ts>/`, git-ignored)
`modeller.N.transcript.jsonl` (stream-json = transcript + extractor input) · `calls.N.json`
(extracted t2c calls) · `friction.N.json` (modeller self-report → judge) · **`CAD.N.step`**
(the built model, replayed from `calls.N.json` — open it in a 3D viewer to sanity-check the
judge's accuracy) · `judge.N.{raw.json,json,transcript.jsonl}` · `editor.N.M.{raw.json,json,transcript.jsonl}`
· `verifier.N.M.{raw.json,json,transcript.jsonl}`. Non-modeller `*.transcript.jsonl` are Claude
Code's own session logs, copied in by `session_id`. Commit tag `[iter N.M]` joins commits ↔
transcripts ↔ artifacts at merge time. Human-readable trail: `SELFIMPROVE_LOG.md` (structured
per outer/inner iteration, per agent, via `log_writer.py`).

## Helpers
`extract_calls.py` (stream-json → `calls.N.json` + fenced-json friction; `--selftest`) ·
`export_model.py` (replay `calls.N.json` through t2c_mcp → `CAD.N.step`) · `parse_json_block.py`
(pull the fenced-json verdict from judge/editor/verifier) · `tool_stats.py` (count every tool
action) · `log_writer.py` (all markdown formatting) · `render_prompt.py` (`{{VAR}}` → env) ·
`setup_worktree.sh` (isolated venv: own editable `src`, shared heavy deps).

## Run
```bash
selfimprove/setup_worktree.sh                 # once per worktree
selfimprove/run.sh "/Users/apple/Desktop/assy/Assy 6"   # a FOLDER of page images (PNG/JPG)
```
Tunables (env): `TARGET` (95), `STAGNANT` (4), `INNER_CAP` (4), `MAX_ITERS` (12),
`MODELER_TURNS` (200, lower for a fast smoke), `EFFORT` (medium — see run.sh for why).
`CONTEXT7_API_KEY` is read from the git-ignored `.env` at the worktree root.

The drawing is a **folder of page images**, one image per sheet (no PDFs: `Read` on a PDF needs
poppler and a `pages` argument, and returns a bare size stub without them).

## Deferred (designed-for, not built)
Parallel worktrees (one drawing each, human-merged); a 24/7 queue driver.
