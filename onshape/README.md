# onshape → verified text2cad templates

Mines **natively-built** Onshape Part Studios and transpiles their parametric design
history into MCP tool-call sequences (`templates.steps`) that the text2cad agent can
replay/adapt. Every emitted template is **geometrically verified** against Onshape's
own mass-properties before it is kept — unverifiable models are flagged and skipped,
never shipped.

## Why not just replay Onshape's feature tree?

A naive dump of Onshape features fails because (a) it's a *foreign* representation the
LLM must translate on every replay, (b) geometry references (which edges a fillet
rounds) are opaque topological ids, and (c) it's never executed or checked. This
package instead:

1. Emits the agent's **native** MCP calls (`workplane_api`/`sketch_api` JSON).
2. Routes every selection through **real 3D space**: Onshape's engine (FeatureScript)
   resolves each modifier's target edges/faces to 3D points, which become
   `NearestToPointSelector` selections in CadQuery. Both engines agree on mm
   coordinates, so no opaque naming is involved.
3. **Verifies** by executing the emitted steps through the real MCP code path and
   comparing the resulting solid to Onshape's mass-properties within tolerance.

## Pipeline

```
client.py    Onshape REST auth + typed Part Studio calls
classify.py  native vs imported                         (STAGE 2)
extract.py   feature tree + solved sketches + FS geometry (STAGE 1/3)
ir.py        engine-neutral intermediate representation
normalize.py Onshape features -> IR                       (STAGE 3)
emit.py      IR -> MCP steps JSON                          (STAGE 4)
verify.py    execute via real MCP; compare to Onshape     (STAGE 5)
pipeline.py  orchestrate one URL -> {steps, verified, report}
```

## Native vs imported — the signal

A Part Studio is **non-native** if its feature tree contains an import-family feature
(`importForeign`, `importDerived`, `import`, `derived`). Verified empirically:

| model  | first features                              | verdict |
|--------|---------------------------------------------|---------|
| washer | newSketch, extrude, …, booleanBodies, fillet | native  |
| gear   | **importForeign** (Import 1), newSketch, …   | imported |

⚠️ The top-level `imports` field is **not** a signal — every model lists
`onshape/std/geometry.fs` there (the standard FeatureScript library). Classification
keys off feature types only. Suppressed import features are treated as inert.

## Setup / usage

Runs in the `mcp_server` venv (CadQuery 2.7.0). Onshape API keys are read from
`ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` / `ONSHAPE_BASE_URL`, falling back to
`../onshape-exp/.env`.

```bash
# from repo root
mcp_server/.venv/bin/python -m onshape.classify "<part-studio-url>"   # native? (exit 0/1)
mcp_server/.venv/bin/python -m onshape.pipeline  "<part-studio-url>"  # transpile + verify
mcp_server/.venv/bin/python -m pytest onshape/tests
```
