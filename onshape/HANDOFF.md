# feat/onshape — Handoff / Resume Guide

Everything a fresh session needs to continue the Onshape→text2cad reconstruction work.
Read this, then `README.md`. Companion memory: `onshape-transpiler-pipeline`,
`onshape-api-rate-limits` (auto-loaded).

## What this project is

Take a **natively-built Onshape Part Studio** and reconstruct its parametric feature
history as a sequence of **MCP `workplane_api` tool calls** (`templates.steps` format:
`[{step, toolName, input}]`) that rebuild the exact solid. These become verified templates
the text2cad AI agent can replay and adapt. Correctness is guaranteed by verifying every
feature against Onshape's own geometry.

The **KPI is L4 "Speed_toolhead"** (169 features) and models beyond it. So the whole design
is about being **universal and automatic** — no per-model hand-tuning.

## SESSION UPDATE (2026-08-05, later) — goal reframed + L4 tail started

**Goal reframed** (see `~/.claude/plans/hey-continue-with-the-prancy-bentley.md`): the target
is **100% coverage of Onshape's op vocabulary** for onshape-NATIVE models, not just L4's few
missing features. Key findings this session:
- Onshape exposes **91 UI features** (`featurespecs`), all decomposing into **~49 `op*`
  primitives** (`opExtrude`, `opRevolve`, `opMoveFace`, `opReplaceFace`, `opDraft`, …). Target
  the primitives → universal + model-agnostic.
- **Direct-edit ops are NOT impossible.** We have native models' full history, so it's
  deterministic transpilation (vs. ML B-rep→program, which is the no-history problem). All
  the direct-edit OCCT algorithms exist in our OCP kernel: `BRepTools_Modifier`,
  `BRepAlgoAPI_Defeaturing` (deleteFace), `BRepOffsetAPI_DraftAngle` (draft) — proven on a box
  in `onshape/tests/test_direct_edit.py`. Plan: **editable-first** (re-express as boolean/
  sketch ops), escalate to an OCCT primitive only when the oracle rejects the re-expression.
  `BRepTools_Modification` can't be subclassed in OCP → general moveFace uses boolean-delta /
  `BRepTools_ReShape` / `BRepFeat`, not the Modifier.

**Done this session:** multi-key API **failover** + persisted lockout (`client.py`,
`out/key_state.json`) + **cache-only offline mode** (auto-on when all keys capped, raises
`CacheMiss`); `reconstruct(stop_after=N)` for cheap prefix runs; **datum no-ops**
(cPlane/cPoint/mateConnector — verified: L4 f2 OK); **revolve** + **mirror** translators, each
with a self-validating synthetic test (torus / two-body mirror); full **L4 oracle cached
170/170**.

**BLOCKED (~21h):** both API keys hit their daily cap. Cached for L4: oracle 170/170,
sketches, features, **27/59 extrude caps, 0/32 modifier faces**. Offline chain reconstructs
f0–f5 then breaks at f6 (up-to-face extrude needs uncached caps). **Next reset (task #5):**
fetch the missing ~64 calls (32 caps + 32 fillet/chamfer modifier faces) → L4 becomes fully
reconstructable offline; then verify revolve@13/@80 + mirror against the real oracle and
continue sweep/shell/hole/draft (need path/point/face resolution — oracle-dependent).
Keys: A `on_jG5pI…` (primary), B `on_keK1…` (`_2`); both in `../../onshape-exp/.env`.

## Current status (2026-08-05)

**The closed-loop engine works and generalizes. All 5 bench models reconstruct feature-by-
feature and verify end-to-end, with zero per-model hand-coding:**

| model | features | steps | element id (doc a4efb14d… unless noted) |
|-------|----------|-------|------------------------------------------|
| washer | 12/12 | 22 | own doc: f4c92562fed11c3d67edd158 / w bfa6dec1a7760d3d1d440e94 / e 7182f8192d4c08e77e9b536f |
| can_jig | 2/2 | 1 | e 6ba2f6a00d1ef96f6aa328aa |
| bearing_press | 6/6 | 6 | e da26d0fee1417d7651249e92 |
| backstop | 8/8 | 7 | e ccd70b45558f8c21daf09729 |
| bearing_presser | 8/8 | 11 | e 6bf352bf24b7ccec9bbc9826 |

All non-washer models are **elements in doc `a4efb14ded413c6a7156d0a7`, workspace
`90a4b04ac181a77e02156549`** (the same doc as L4). L4 itself: `e c24e56878804dbda7d1c08cf`.
To list/rename elements: `GET /documents/d/<did>/w/<wid>/elements`.

Outputs land in `onshape/out/*_recon.json` (gitignored — regenerable). Each has
`{model, url, verified, verify:{deltas}, features_matched, n_steps, steps, report}`.

## How to run

```bash
cd /Users/apple/Desktop/t2c
# reconstruct + verify + write steps:
mcp_server/.venv/bin/python -m onshape.recon "<url>" --name NAME --out onshape/out/NAME_recon.json
# tests:
mcp_server/.venv/bin/python -m pytest onshape/tests -q
```

Debugging a feature that reports `[NOT EXACT]`: dump every candidate's (bodies, volume) vs
the oracle for feature i:
```python
# in a script guarded by if __name__=='__main__':
from onshape.recon import reconstruct
reconstruct(api, ps, dump={7})   # dumps candidates for feature index 7
```

**Iteration is offline** once a model is fetched (its rollback FeatureScript calls are
cached to `onshape/out/cache/`). Filter viewer noise with
`grep -vE "warning|camera"`.

## The architecture (why it's built this way)

Old = open-loop transpiler (`normalize.py`/`emit.py`/`pipeline.py`, retiring): hand-code
each feature's conventions, verify once at the end. Every wrong convention = a manual
debugging session. Died on L1 (annulus, extrude sign, fillet). **Does not scale.**

New = **closed loop** (`oracle.py` + `recon.py`). Per feature:
1. a translator emits **candidates** enumerating the ambiguous choices;
2. each runs through the real `t2c_mcp` engine in a crash-isolated worker;
3. the **oracle** (Onshape's exact per-feature geometry) picks the match.

Conventions are *discovered*, not encoded. Errors localize to the feature. Gaps fail loud.

### Files (all under `onshape/`)
- **`oracle.py`** — `body_states(api, ps, n)` → `{rollbackIdx: State}`; `State.bodies` are
  `Body(volume, area, centroid, bbox)` fingerprints. `Body.matches`/`State.matches` are the
  accept test (rel vol/area, abs bbox+centroid). This is the ground truth.
- **`recon.py`** — the heart:
  - `Engine` (in-process) and `WorkerEngine` (persistent child; `try_candidate` enforces a
    timeout, on hang kills+respawns+replays committed steps). Both expose
    `name/live/steps/try_candidate/commit/edges/state_of`.
  - `Candidate(payloads, live_after, label, skip_failures, target, base_live)` — a way to
    realize a feature. `skip_failures` = a resilient chain (per-edge fillet: skip edges
    OCCT rejects, re-chain start_from).
  - translators: `extrude_candidates`, `region_extrude_candidates`, `pattern_candidates`,
    `boolean_candidates`, `round_candidates` (fillet/chamfer). Shared op logic in
    `_op_candidates` (new/add-sep/merge/cut/cut-all).
  - `pick(engine, cands, target)` → `(candidate, exact)`; `reconstruct(api, ps)` = the loop.
  - `_cli` = the `python -m onshape.recon` entry.
- **`extract.py`** — rollback FeatureScript: `resolve_extrude_caps` (per-extrude cap faces),
  `resolve_modifier_faces` (fillet/chamfer created faces at THEIR rollback), `resolve_body_flow`.
- **`normalize.py`** — `_sketch_profiles`, `caps_to_profiles` (flat), `caps_to_regions`
  (loops grouped by face), `_cap_distance`, param parsers (`parse_length_mm`, `_parse_angle_deg`).
- **`verify.py`** — `run_steps_guarded` (spawn subprocess), `fetch_ground_truth`, `compare`.
- **`client.py`** — REST, disk cache (GETs + /featurescript POSTs incl. `?rollbackBarIndex`),
  429 backoff that FAILS FAST on a multi-hour Retry-After (`RateLimited`).
- **`ir.py`** — `Plane/Profile/Curve/...` dataclasses (shared with old pipeline).

### How to add a feature type (the L4 tail: revolve, sweep, shell, mirror, hole, moveFace, cPlane)
1. In `reconstruct`, add an `elif ft == "<type>":` branch that reads params (via
   `_params`/`_enum`/`parse_length_mm`) and calls a new `X_candidates(engine, ..., eng.live)`.
2. Write `X_candidates` returning `Candidate`s that enumerate the ambiguous choices. Reuse
   `_op_candidates` for how a built tool joins the world; reuse `_edge_selector`/
   `engine.edges()` for face/edge references.
3. The oracle verifies automatically. Prove it on a model that uses the feature; add a unit
   test in `onshape/tests/test_recon.py`.
Most CadQuery equivalents exist: `revolve`, `sweep`, `shell`, `mirror`, `hole`, `split`.

## What's next (priority order)

1. **cPlane (datum planes)** — L4 has 5. Resolve construction-plane geometry (offset/
   angled/through-points) so extrudes/sketches on them work. Probably a FeatureScript
   resolve + feed as a candidate plane (the candidate-plane machinery already exists).
2. **revolve / sweep** — 2 + 3 in L4. `revolve_candidates` (axis search like patterns) and
   `sweep_candidates` (profile + path). Both map to CadQuery ops.
3. **shell / hole** — `.shell(thickness, faces)` and hole; faces via `engine.edges()`-style
   face fingerprints (add a `faces` worker query mirroring `edges`).
4. **mirror** — 7 in L4. `.mirror(plane)`; the mirror plane is searchable like pattern axes.
5. **moveFace / replaceFace** — 13 + 2 in L4. Direct-editing; hardest, no clean CadQuery
   equivalent. This is where the **B-rep fallback question** finally arises (see below).
6. **Universal reference resolution** — generalize `_edge_selector` into one face/edge
   fingerprint mechanism (centroid/normal/area/loop-sig) used by all feature types.
7. **Run L4 end-to-end** — the real test. The WorkerEngine's crash isolation was built for
   this scale; expect to add feature types iteratively, verifying per-feature.

### The deferred B-rep fallback decision (revisit at step 5)
For features that genuinely can't be replayed (some moveFace/replaceFace/lighten), the
option is to import the exact Onshape B-rep (STEP/Parasolid) as a frozen sub-part. The user
is wary: a B-rep blob is opaque — the agent can't understand/edit it (you can still
fillet/boolean on top). Recommendation stands: **all-parametric by default; B-rep only as a
last resort for a feature that provably can't be replayed, clearly flagged.** Do NOT
implement it until a real feature forces the question; confirm with the user first.

## Hard-won gotchas (don't relearn these)

- **API is a hard ~daily cap, per-ACCOUNT** (a second key does NOT dodge it; `Retry-After`
  seen 44k–77k s ≈ 12–21h). Never burst. Probe status with ONE no-retry call. The cache +
  `resolve_*` design is what lets you iterate offline — protect it. See memory
  `onshape-api-rate-limits`.
- **Caching bug (fixed, don't reintroduce):** rollback calls are `/featurescript?rollbackBarIndex=N`;
  the cache-eligibility check must strip the query string, else these (the priciest calls)
  never cache and burn quota.
- **spawn needs a `__main__` guard** on any entry that calls `reconstruct` (WorkerEngine
  uses spawn; without the guard the child re-imports and recurses).
- **Viewer disabled in the engine** (`_mcp._show` no-op) — tessellating every candidate OOMs.
- **`Body.matches` includes centroid** — needed to disambiguate placement (a boss on the
  left vs right of a symmetric base share vol/area/bbox). If two candidates tie, a stronger
  fingerprint is the fix, not looser tolerance.
- **Do NOT modify production `mcp_server/src/t2c_mcp.py`** for reconstruction robustness —
  it belongs in the candidate search. An in-engine fillet fallback was made then reverted.
  Per CLAUDE.md, any t2c_mcp.py change in a PR needs the `run-ci` label (ask the user first).
- **Onshape kernel is Parasolid; ours is OCCT.** OCCT rejects some exact-degenerate ops
  (chamfer that exactly consumes a feature) → offer a ×0.9999 perturbed amount.
- **CLAUDE.md rules:** Conventional Commits, no AI attribution in commits/PRs, branch off
  `main` (not feature branches), Git Pull Protocol (fetch + diff before pull).
- Onshape API keys live in `../../onshape-exp/.env` (i.e. `/Users/apple/Desktop/onshape-exp/.env`).
  Never print/commit them.

## Recent commit trail (feat/onshape, newest last)
- cache rollback /featurescript calls (query string defeated cache)
- fail fast on daily-cap 429 instead of sleeping ~21h
- per-feature geometric oracle (closed-loop foundation)
- closed-loop reconstruction engine + extrude variant search
- full closed-loop driver reconstructs washer 12/12, verified
- recon CLI + core generalizes to can_jig & bearing_press
- search circular-pattern axis; candidate-dump debug hook
- exact multi-region extrudes via per-face region union
- **bearing_presser 8/8 — robust edges, candidate planes, centroid** (latest)
