# onshape → verified text2cad templates

Mines **natively-built** Onshape Part Studios and transpiles their parametric design
history into MCP tool-call sequences (`templates.steps`) that the text2cad agent can
replay/adapt. Every emitted template is **geometrically verified** against Onshape's
own mass-properties before it is kept — unverifiable models are flagged, never shipped.

Branch: `feat/onshape`. Runs in the `mcp_server` venv (CadQuery 2.7.0). Onshape API
keys from `ONSHAPE_ACCESS_KEY/SECRET_KEY`, falling back to `../../onshape-exp/.env`.

```bash
mcp_server/.venv/bin/python -m onshape.classify "<url>"   # native? (exit 0/1)
mcp_server/.venv/bin/python -m onshape.pipeline  "<url>"  # transpile + verify
mcp_server/.venv/bin/python -m pytest onshape/tests
```

## Pipeline

```
client.py    Onshape REST auth + typed calls; 429 backoff, throttle, disk cache
             (incl. /featurescript POSTs) so reconstruction iterates OFFLINE
classify.py  native vs imported (import-family feature types; NOT the imports field)
extract.py   FeatureScript resolution (see below)
ir.py        engine-neutral IR (Plane/Profile/Curve/Extrude/Revolve/Fillet/Chamfer/
             CircularPattern/Boolean/Model; Model.body_flow)
normalize.py Onshape features -> IR
emit.py      IR -> {step,toolName,input} steps (the templates.steps format)
verify.py    execute steps via the REAL t2c_mcp engine in a spawn subprocess (hang-proof)
             + compare volume/area(=Onshape "periphery")/bbox, converting SI->mm
pipeline.py  classify -> extract -> normalize -> emit -> verify
```

## The key technique: rollback evaluation

Onshape's `/featurescript?rollbackBarIndex=N` evaluates the model at feature N's
checkpoint — **before later features fragment its geometry**. This dissolved the
opaque-`qCompressed`-query wall (region/body references don't resolve at the final
state; regions are consumed, faces fragmented). `extract.py` uses it for:

- **Per-extrude regions + distance** (`resolve_extrude_caps`): at the extrude's rollback,
  read its created planar cap faces; sample each boundary edge at params 0/0.5/1 (3
  points classify any line/arc/circle); keep caps whose normal ∥ extrude dir; project to
  the sketch 2D frame; assemble loops. Solves **multi-region sketches** and **up-to-*
  terminations** (distance = far cap's normal-offset). Also incidentally handles engraved
  text (letter outlines become regions).
- **Fillet/chamfer edge points** (`resolve_created_faces`): midpoint of each created
  face's **longest boundary edge** (a tangent line for straight edges, a boundary circle
  for circular ones). NOT the centroid — that's on the axis for surfaces of revolution.
- **Cross-rollback body-matcher** (`resolve_body_flow` + `emit._Bodies`): solid centroids
  per feature; track live bodies as [onshape_centroid, mcp_name]; cut applies to every
  body, pattern finds its source by rotation-matching new centroids, ADD merges into the
  touched body; re-key centroids after each feature.

Selection is emitted as `NearestToPointSelector` / `SumSelector` on those 3D points, so
it never depends on either engine's opaque topological ids.

## Coverage

| model | result |
|-------|--------|
| can jig | ✅ verified, volume rel 3.8e-16 |
| bearing_press (19+10-region shared sketch, engraved text, fillet) | ✅ verified, rel 5.8e-7 |
| bearing_presser | ❌ 1mm chamfer on a 1mm-tall feature (OCCT geometric limit) |
| backstop | ❌ non-planar up-to surface (6% flat approximation) |
| **washer (KPI)** | ❌ fully resolves, but OCCT **hangs** on a multi-body boolean |

## Next task: washer OCCT robustness

The washer's every feature resolves and the body-matcher engages correctly (ring +
ADD-tab → pattern1 ×3 → cut all 3 → pattern2 → union 5 → fillet → cut). But a multi-body
boolean **hangs in OCCT**. Hypothesis: patterning the **centered annulus** creates
coincident ring copies that CadQuery/OCCT can't union cleanly. Direction: detect
symmetric/coincident pattern copies (union with `glue`, or skip coincident copies), and
make booleans/fillets resilient (`clean`/tolerance, per-edge fallback). Iterate with
`verify.run_steps_guarded(steps[:k])` to find the hanging step.

Prereq to iterate offline: run the pipeline **once** so every rollback `/featurescript`
call is cached — until 2026-08-01 these were silently NOT cached (the cache key check
ignored the `?rollbackBarIndex=N` query string, now fixed in `client.py`), so each debug
run re-hit the API and could exhaust the ~daily quota. The washer's rollback data still
needs one clean fetch (key was rate-limited, `Retry-After` ~21h, when this was found).

## Notes / gotchas

- Onshape enforces a hard ~daily API cap; never burst (see the rate-limit memory). The
  disk cache + `resolve_*` design let you iterate reconstruction logic fully offline
  after one fetch.
- `verify` runs steps in a spawn subprocess because OCCT hangs are uninterruptible from
  Python (SIGALRM won't stop them).
- The error message step index in `verify` is 0-based; emit labels are 1-based.
