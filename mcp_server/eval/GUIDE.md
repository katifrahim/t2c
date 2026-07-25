# t2c eval — guide

A tiny benchmark for "how good is each model at using the t2c MCP server." Files:

| File | What it is |
|------|------------|
| `provider.mjs` | Runs one model through the MCP tool loop (same stack as `web/`) and returns the transcript + counters. You rarely touch this. |
| `tasks.yaml` | **The tests.** This is what you edit most — the CAD prompts + grading rubrics. |
| `promptfooconfig.yaml` | The model list, the judge, and the deterministic checks. |
| `humanjudge.mjs` | Optional grader for **human-judge mode** (see below). |
| `GUIDE.md` | This file. |

## Setup & run

The eval lives in `mcp_server/eval/`. All paths below are from the repo root.

```bash
# 1. deps (once): symlink web/'s node_modules into the eval dir (already set up)
cd mcp_server/eval && ln -s ../../web/node_modules node_modules

# 2. API key: put your OpenRouter key in mcp_server/eval/.env — promptfoo auto-loads it.
#    OPENROUTER_API_KEY=sk-or-...    (used by both the agent AND the judge)

# 3. start the MCP server in HTTP mode (separate terminal, from repo root)
MCP_TRANSPORT=http PORT=9000 mcp_server/.venv/bin/python mcp_server/src/t2c_mcp.py

# 4. run the eval from mcp_server/eval/ (-j 1 = serial; server has one shared state)
cd mcp_server/eval
npx promptfoo@latest eval -j 1
npx promptfoo@latest view          # sortable model × metric leaderboard
```

## LLM judge vs. human judge

By default the capability columns (`Sketch2D`, `MovableJoint`, …) are graded by the LLM
judge in `promptfooconfig.yaml`. To grade them **yourself** instead, run the same eval
with the human grader:

```bash
npx promptfoo@latest eval -j 1 --grader file://humanjudge.mjs
npx promptfoo@latest view
```

`--grader` swaps the judge for *every* capability column at once (no config edit, no LLM
call, no cost). Each capability cell then comes back **fail / score 0** with that task's
success criteria in its reason. Open the viewer, read each model's transcript (it's the
cell's output — the same thing the LLM judge sees), and **override the cell's pass/fail +
score by hand** in the UI; your ratings persist and export. The deterministic columns
(`NoToolErrors`, `CadOpCount`, …) fill in automatically in both modes.

Everything else — the model run, the transcript, the STEP artifacts, the deterministic
metrics — is identical; only who grades the capability columns changes.

## What is a promptfoo provider? (start here if you're new)

In promptfoo, a **provider** is "the thing that turns a prompt into an output." Normally
it's just a model — you'd write `providers: [openai:gpt-4o]` and promptfoo sends the prompt
straight to that model and grades the reply.

That's not enough for us. To use the t2c server, a model has to do a **multi-step loop**:
call a tool → read the result → call another tool → … → finish. A bare model string can't
do that. So we wrote a **custom provider**, `provider.mjs`: a small piece of code that
promptfoo hands a prompt to (via its `callApi` function), and which internally runs the
whole model-↔-MCP-server loop and hands back one bundled result. promptfoo doesn't know or
care what happened inside — to it, our provider is just a black box: prompt in, output out.

Each entry under `providers:` in the config points at this same file with a different
`config.model`, so "swap the model" = "new provider." (The LLM **judge** is also just a
provider — a model promptfoo calls to grade, set via `defaultTest.options.provider`.)

A custom provider only needs two things, which `provider.mjs` has: an `id()` (its name) and
an `async callApi(prompt, context)` that returns `{ output, metadata }`. That's the whole
interface — [docs](https://www.promptfoo.dev/docs/providers/custom-api/).

## How our provider works

`provider.mjs` mirrors production (`web/app/api/chat/route.js`): it connects to the MCP
server with `@ai-sdk/mcp`, hands those tools to the model via the AI SDK + OpenRouter, and
runs the multi-step tool loop. It returns:

- `output` — a text **transcript** the judge reads: the model's **`REASONING:`** (for
  reasoning models — its private chain-of-thought, exposed by the AI SDK as
  `step.reasoningText`), its **`ASSISTANT:`** prose, and every
  **`CALL toolName {args} -> {json result}`** (the tool result includes geometry props like
  `volume`, `bounding_box`, `center`).
- `metadata.anyToolError` — did any tool return `status:"error"`.

Each *assertion* on a test scores 0–1 and is tagged with a `metric:`. promptfoo averages
per model, per metric → the leaderboard columns. Token usage and cost show automatically.

> Note on reasoning: including the reasoning trace helps the judge assess *planning and
> spatial reasoning*, but don't let it dominate — a model can "sound" right and still build
> the wrong geometry. Keep rubrics anchored on the actual tool results/geometry. Not every
> model emits reasoning; for those, the `REASONING:` lines are simply absent.

## The single most important insight

**The judge cannot see a 3D render.** It only sees the transcript — the tool CALLs, their
JSON results (each includes geometry props: `volume`, `bounding_box`, `center`, and for
assemblies the child part list), and the model's reasoning/prose. So a good rubric is a
**checklist of the visible features the result must have**, which the judge verifies from
that transcript. That's why the rubrics in `tasks.yaml` read like "required: a central hub;
exactly 6 evenly-spaced blades that curve/twist; full marks only if … penalize …". When a
task does pin exact numbers, name them so the judge can check the reported `volume`/
`bounding_box`; when it doesn't, the checklist of features is the grade.

## Two kinds of checks (and when to use each)

**Deterministic** — objective true/false checks. We ship one (tool error); here are more
worth adding, in two groups. The split matters because your prompts are deliberately vague:
a "make a bracket" prompt has no single correct size, so only **Group A** applies to it. Use
**Group B** only on the few tasks where you pin exact dimensions. (They all just read the tool
results already in the transcript, and go in `defaultTest.assert` to run on every task, or
under one task's `assert:`.)

**Group A — prompt-agnostic (work even on vague prompts; add these broadly):**

- **No tool errors** — the one we ship. Did the model form valid calls at all? Highest-signal
  check for this server's tricky JSON mini-language.
- **Built a valid, exportable solid** — the best objective "is the geometry actually real /
  manifold?" check: it asks the server to export the part, and passes only if that succeeds.
- **Produced a model at all** — catches a model that just talked and never built anything.
- **Efficiency** — tool-call count vs. a budget (fewer = better).
- **Docs discipline (optional)** — did it call `query_docs` instead of guessing parameters?

Snippets:

```yaml
# Built a valid, exportable solid (needs -j 1 — checks the live server right after the run)
- type: javascript
  value: |
    const r = await fetch((process.env.T2C_URL ?? 'http://localhost:9000') + '/export?fmt=step');
    return { pass: r.ok, score: r.ok ? 1 : 0, reason: r.ok ? 'valid solid' : `export HTTP ${r.status}` };
  metric: ValidSolid

# Produced a model at all
- type: javascript
  value: "const ok = output.includes('\"status\": \"success\"'); return { pass: ok, score: ok?1:0, reason: ok?'built something':'never built' };"
  metric: BuiltSomething

# Efficiency: tool-call count vs. a budget of 8
- type: javascript
  value: |
    const n = (output.match(/^CALL /gm) || []).length;
    return { pass: n > 0 && n <= 8, score: n === 0 ? 0 : Math.min(1, 8 / n), reason: `${n} tool calls` };
  metric: Efficiency
```

**Group B — ground-truth (only on tasks where you pinned exact dimensions):**

- **Volume within tolerance** — best objective correctness measure when you know the answer.
- **Bounding-box within tolerance** — checks size and orientation at once (cleanest spatial check).
- **Final object type** — e.g. an assembly task must end as an `Assembly`, a 2D task as a `Sketch`.
- **Assembly child count** — e.g. exactly two children for a two-part assembly.

Snippets:

```yaml
# Volume within ±5% of a known answer (here 2280 mm³)
- type: javascript
  value: |
    const m = [...output.matchAll(/"volume":\s*([0-9.]+)/g)];
    const v = m.length ? parseFloat(m.at(-1)[1]) : NaN;   // last volume reported
    const ok = Math.abs(v - 2280) / 2280 < 0.05;
    return { pass: ok, score: ok ? 1 : 0, reason: `volume=${v}` };
  metric: VolumeOK

# Final object is an Assembly
- type: javascript
  value: "const ok = output.includes('\"obj_type\": \"Assembly\"'); return { pass: ok, score: ok?1:0, reason: 'assembly?' };"
  metric: IsAssembly
```

(Bounding-box and child-count follow the same pattern — regex the last `bounding_box` or
`object_count` out of the transcript and compare.)

In short: **Group A = did it drive the server competently; Group B = did it build the exact
thing.** With your vague prompts, rely on Group A + the judge, and keep a few precise tasks
for Group B.

**LLM-as-judge (`llm-rubric`)** — for "is this the right part." This is the workhorse here.
The judge model (`defaultTest.options.provider`) reads `output` and grades it against your
rubric string, returning pass/fail + a 0–1 score + a reason. Anatomy of a strong rubric:

1. State the must-have **features** (dimensions, holes, fillets, orientation).
2. Give the **expected numbers** (volume, bbox, center) so the judge can verify.
3. Define **"full marks only if …"** and **"penalize …"** so scoring is consistent.
4. Reward the right **approach** (e.g. "uses assembly_api, not a boolean union").

Want a graded 1–5 score with chain-of-thought instead of pass/fail? Swap `llm-rubric` for
`g-eval`. Want multiple rubric facets as separate columns? Add several `llm-rubric`
assertions with different `metric:` tags (e.g. `Dimensions`, `Approach`, `Orientation`).

## Writing tasks that actually separate good models from great ones

The server's hard parts — where models fail and the benchmark gets its signal:

- **The JSON mini-language.** `{"_ref": name}` to reuse stored parts, `{"_type": ...}` to
  build Vectors/Planes/selectors inline, `{"_expr": "30/4"}` for computed dimensions. Tasks
  that force these (reuse a stored solid, compute a dimension, pass a typed selector)
  expose weak models fast.
- **Selectors & feature targeting.** "fillet only the vertical edges", "drill the top
  face" → forces `edges('|Z')`, `faces('>Z')`, or typed selectors. Vague models grab the
  wrong faces.
- **Spatial reasoning / orientation.** Named planes, axes, ordered rotations, "branch points
  sideways". The biggest differentiator — see the *Pipe tee* and *Tilted slab* tasks.
- **Multi-step state & planning.** `store_as` / `_ref` / `select_model`, build A then B then
  combine. Tests memory and whether the model plans before calling.
- **Assemblies.** `assembly_api` add → constrain → solve with the right constraint types is
  genuinely hard — see the *Door hinge* (must stay multi-part to pivot) and *Toy car* tasks.
- **Docs discipline.** Good models call `query_docs` when unsure instead of hallucinating
  parameters. You can reward this in a rubric.

On prompt style: real users are **lazy and vague** ("make a phone stand", "design a small
gear") — so most tasks should be too. That's the realistic test: can the model make sound
CAD *decisions* from an underspecified ask? Grade those with the judge (reasonableness +
validity) and Group-A deterministic checks — there's no single correct volume to check. Keep
a *few* tasks with **pinned exact dimensions** as well, because those are the only ones where
Group-B numeric checks (volume/bbox) work and where you can measure precision objectively.
A good suite mixes both, and varies domain (brackets, enclosures, gears, pipes, household
objects) so you're not just testing one trick.

Other tips: keep a couple of tasks brutally hard so top models don't all tie; raise
`maxSteps` for multi-part tasks. For **consistency**, re-run a task a few times and watch the
score spread — `npx promptfoo@latest eval --repeat 3 --filter-pattern "Pipe tee"` (models vary
even at low temperature, so judge trends, not single runs).

## Adding things

- **A model:** copy a `providers:` entry, change `label` + `config.model` (IDs in
  `web/lib/models.js`). `reasoning: high|medium|low` is optional.
- **A task:** add a block to `tasks.yaml` (instruction + one or more `llm-rubric`).
- **The judge:** change `defaultTest.options.provider`. A stronger judge grades more
  reliably but costs more per run.

## Notes / tradeoffs I made

- **HTTP transport only.** Production uses HTTP; stdio was dropped for simplicity. To use
  stdio, swap the `@ai-sdk/mcp` transport for a stdio one and skip the `/clear` reset.
- **No artifact (STL/STEP) export.** The transcript already carries the geometry numbers
  the judge needs. If you want to *see* each model's part, the server's `/export?fmt=stl`
  and `/model` routes are live while it runs — add ~5 lines to `provider.mjs` to save them.
- **One deterministic check.** By design — you asked for judge-first grading.
