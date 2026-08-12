# Imported-CAD editing — state, design directions, and handoff

Branch: `feat/cad-parser` (off `main`). Goal: let the text2cad AI **read and edit imported CAD
models** (STEP) with, ideally, flexibility approaching what it has when it builds a model from
scratch.

> ⚠️ **Read this first — this document is not authoritative.** It records what one working
> session built and learned. It is incomplete, parts are unproven at scale, and some
> conclusions may be wrong. Treat every claim as a starting hypothesis to **re-verify**, not a
> fact. The area is genuinely hard and under-researched here — do your own research, challenge
> these choices, and prefer evidence over anything written below. Do not become anchored to
> this approach.

---

## 1. What exists now (built + tested; 79 tests pass)

**Parser — `mcp_server/src/step_import.py`**
- `read_step(path)` — OCP `STEPCAFControl_Reader` + XCAF → a CadQuery object (Workplane for one
  part, Assembly for many) + `meta` (header, parts with names/colors/placement, best-effort PMI).
  De-duplicates repeated STEP part names (assemblies often reuse names).
- `describe_shape(obj, meta, budget)` — tiered JSON: summary + per-solid faces/edges (surface/
  curve types + dimensions) + cheap enrichment (primitive label, holes = concave cylinders,
  fillets). **Large assemblies (>`PART_BUDGET`=40 parts)** get a compact per-part summary (bbox
  + face count, no GProp volume) capped to the 80 largest parts, to stay fast + small.
- `to_ap242()` / `ensure_ap242_schema()` — write AP242. **Non-obvious:** construct one
  `STEPControl_Writer()` first, THEN `Interface_Static.SetIVal_s("write.step.schema", 5)`
  (=AP242DIS). String set does not work; AP242 `FILE_SCHEMA` is multi-line.

**Direct editing — `mcp_server/src/direct_edit.py`** (edits a dumb solid in place, not CSG)
- `resize_hole` (defeature + re-cut; `scope="one"` default, `"matching"` opt-in resizes the
  equal-diameter set), `remove_feature` (`BRepAlgoAPI_Defeaturing`), `push_pull_face`
  (`BRepFeat_MakePrism`, with fillet suppress+replay and a plain-extend fallback + validity
  check), `shell` (`MakeThickSolid`), `draft_face` (`DraftAngle`).
- Selection: point-on-feature → nearest-**surface** resolution (`BRepExtrema`).

**MCP surface — `mcp_server/src/t2c_mcp.py`**
- `POST /import` (converts to AP242, stores active, returns description; rejects non-STEP 415),
  `inspect_model(name, detail=summary|standard|full)`, `edit_model(operations, name, store_as)`,
  `POST /session/adopt` (re-homes CAD state from a new chat's local id to the persistent chat id
  on first message).

**Web — `web/`**
- Top-bar Import button → `/api/import`; description injected into the next prompt (composer
  chip); chip cleared on chat switch. `/api/session/adopt`, `/api/import` proxies.

Test STEP files live on the user's Desktop: `Holder Refined.step` (single-part bracket, 287
faces) and `Hexapod.STEP` (17 MB, 1250-part assembly).

---

## 2. Known limitations / suspected weak spots (verify these)

- **Selection stability (topological naming).** Features are referenced by coordinate. After an
  edit rebuilds the solid, a coordinate may resolve to a different face. This is the classic
  topological-naming problem and is probably the biggest reliability risk. Not solved.
- **`push_pull_face`** cannot cleanly extend some complex faces (kernel returns an unsound
  solid) — it now errors clearly instead of producing garbage, but coverage is partial.
- **Big-assembly performance.** `/import` on the 1250-part hexapod ≈ 34 s (mostly viewer
  tessellation + a double CAF read: `to_ap242` then `read_step`). Not optimised. A frontend/
  proxy timeout could still bite on huge files. Consider a single CAF read, and lazy/again
  tessellation.
- **PMI reading is unvalidated** against a real MBD/PMI file (the models tested have none). The
  code uses the correct XCAF API but returns empty in practice so far.
- **Direct-edit op set is small.** No edit-fillet/chamfer, move-hole, pattern-edit yet.
- **Fully general "move any face + auto-heal"** is not possible in pure Python: OCP cannot
  subclass `BRepTools_Modification` (no constructor/trampoline), the wheel ships no headers, and
  a separate C++ extension can't share OCP's pybind type-casters. A C++ trampoline would require
  rebuilding OCP. Unverified whether that is worth it.

---

## 3. First-principles framing (one hypothesis, not a conclusion)

A scratch model **is a program** (an editable op sequence); an imported model **is a state** (a
dumb B-rep with no recipe). Closing that gap has three broad strategies:

- **A. Edit the state directly** (direct modelling) — what `edit_model` does.
- **B. Recover a program from the state** (feature recognition / reverse engineering).
  **Dropped by user decision: unreliable, unsolved research problem. Do not pursue.**
- **C. Attach a program going forward** — treat the dumb solid as an immutable *base*, and record
  every later edit as an editable history on top (how SolidWorks/Fusion handle imported bodies).

Everything below assumes **A + C**, not B.

## 4. Design directions worth investigating (prioritised guesses)

These are directions, not a committed plan. Each needs its own research + validation.

1. **Selection / reference layer (highest leverage, most uncertain).** Make referencing a
   specific face/edge reliable and **stable across edits**. Options to explore: coordinate
   selectors (current), persistent-id tagging, re-matching by geometric signature after each
   rebuild, or a hybrid. This gates every other capability. The topological-naming literature is
   relevant.
2. **Base-plus-history frame (Strategy C).** Store the dumb import as a base and record the AI's
   edits as a re-runnable, re-editable sequence — so the AI gets scratch-like flexibility for
   everything it *adds*. t2c already stores op sequences (templates); investigate reusing that.
   Integrate with existing tools: constructive edits via `workplane_api(start_from=<import>)`
   using coordinate selectors; direct edits via `edit_model`. Both already run on the same stored
   object.
3. **Generalise dependent-feature handling.** The suppress+replay written for `push_pull_face`
   (carry edge fillets) should become a shared service used by resize/offset/remove, so no op
   ever tears a blend. Current implementation is best-effort and only handles rounds (not
   chamfers).
4. **Dynamic relationship engine (not per-op hardcoding).** The user explicitly wants this
   general, not coded per operation. The field calls it **Variational Direct Modelling**: a
   constraint layer over the B-rep + a solver that re-solves after edits (this is how Siemens
   Synchronous Technology works). Sub-parts: (a) deterministic relationship *detection*
   (symmetry, patterns, equal-dim, coaxial, tangent), (b) *maintenance* — trivial for
   independent regularities (apply the same delta to the related set), but hard for coupled
   constraint networks, which need a real 3D geometric constraint solver. Open-source 3D GCS is
   immature (SolveSpace/Frontier/NeoGeoSolver solve their own entities, not OCCT B-rep faces);
   the commercial standard is Siemens **D-Cubed 3D DCM** (licensed). A pragmatic middle path: one
   op-agnostic detector + one generic "apply delta to related set" wrapper + let the LLM decide
   *scope* (which relationships to honour is design intent, not a mechanical fact). **`resize_hole`
   `scope="matching"` is a hardcoded prototype of this — it should move into the shared layer.**
5. **Lean on the self-diagnostic feedback loop.** `edit_model` returns `valid` + volumes; imperfect
   selection is survivable if the AI checks and retries. Strengthen this.

## 5. Objective note on "100%"

Any B-rep can in principle become any other via booleans + local ops, so most modifications are
*reachable*. Practical flexibility is bounded by: (1) can the AI select the target, (2) does an op
exist, (3) does it update dependents, (4) is the result healed/valid. All four are dials we can
turn up. One gap is fundamental: editing the *base's own internal feature parameters* is never as
clean as changing a number in a recipe that does not exist — there, only direct editing (A)
applies. Scratch-parity is realistic for what the AI *adds* (C), asymptotic for the base itself.

## 6. Key facts to carry forward (re-verify)

- OCCT/OCP has: `BRepAlgoAPI_Defeaturing`, `BRepFeat_*`, `BRepOffsetAPI_*`, `BRepFilletAPI_*`,
  `BRepTools_ReShape`, `ShapeFix`, `ShapeUpgrade_UnifySameDomain`. All present in this OCP 7.8.1.
- No commercial "Direct Modelling" product from Open Cascade (an earlier claim was wrong — it is
  a forum page; paid components are Collision/Parasolid/ACIS/Canonical-Recognition/etc.).
- OCP cannot subclass `BRepTools_Modification` (verified).
- `HashCode` is gone in OCCT 7.8 — use `IsSame` for topological identity.
- See memory note `t2c-cad-parser-and-edit` for a shorter index of the same facts.

## 7. How to continue

1. `git checkout feat/cad-parser`; run `cd mcp_server && .venv/bin/python -m pytest -q` (expect
   79 passing).
2. Reproduce imports with the two Desktop STEP files.
3. Do independent research on the topological-naming problem and variational direct modelling
   before committing to an architecture. Challenge section 4.
