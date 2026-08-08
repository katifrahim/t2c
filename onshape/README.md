# onshape → verified text2cad templates

Mines **natively-built** Onshape Part Studios and reconstructs their parametric design
history into MCP tool-call sequences (`templates.steps`) that the text2cad agent can
replay/adapt. Every reconstruction is **geometrically verified against Onshape's own
geometry at every feature** — nothing is kept that doesn't provably match.

Branch: `feat/onshape`. Runs in the `mcp_server` venv (CadQuery 2.7.0). Onshape API keys
from `ONSHAPE_ACCESS_KEY/SECRET_KEY`, falling back to `../../onshape-exp/.env`.

```bash
# reconstruct a model, verify end-to-end, write the steps:
mcp_server/.venv/bin/python -m onshape.recon "<part-studio-url>" --name NAME --out onshape/out/NAME_recon.json
mcp_server/.venv/bin/python -m pytest onshape/tests
```

> **New here / resuming? Read [`HANDOFF.md`](HANDOFF.md) first.** It has the full
> architecture, how to add feature types, the model URLs, and what's next.

## Architecture: closed-loop, oracle-driven reconstruction

The old approach (still in `normalize.py`/`emit.py`/`pipeline.py`, now retiring) hand-coded
every feature's conventions and verified once at the end — so every wrong convention was a
manual debugging session. It could not scale to L4 (169 features). The **closed loop**
(`oracle.py` + `recon.py`) replaces it:

For each feature, in tree order:
1. **Generate candidates** — a small set of op-sequences enumerating the *genuinely
   ambiguous* choices (extrude direction ±, which loops form a region, which body an op
   targets, which sketch plane, which edges a fillet hits, the exact amount).
2. **Execute each** through the real `t2c_mcp` engine in a crash/hang-isolated worker.
3. **The oracle picks** — keep the candidate whose resulting solids match Onshape's own
   geometry at that feature (per-body volume / area / bbox / centroid).

Conventions are **discovered per feature, not encoded**. An error is caught at the feature
that causes it. Coverage gaps fail loudly (flagged, never a silent wrong answer).

```
oracle.py    Onshape's exact geometry at every rollback: per-body {volume, area,
             centroid, bbox}. Body.matches / State.matches are the accept test.
recon.py     the engine + translators + driver:
               Engine / WorkerEngine  — run candidates; WorkerEngine isolates each
                                        trial in a persistent child (kill+respawn+replay
                                        committed steps on OCCT hang)
               *_candidates(...)       — per-feature translators (extrude, pattern,
                                        boolean, fillet/chamfer); each yields Candidates
               reconstruct(api, ps)    — the feature loop + pick()  (CLI: __main__)
extract.py   rollback FeatureScript: resolve_extrude_caps (per-extrude cap faces),
             resolve_modifier_faces (fillet/chamfer created faces at THEIR rollback),
             resolve_body_flow (legacy)
normalize.py sketch geometry + caps_to_profiles / caps_to_regions (loop grouping)
verify.py    run steps through real t2c_mcp in a spawn subprocess; compare to Onshape
client.py    Onshape REST: auth, 429 backoff (fails fast on daily cap), throttle, disk
             cache of GETs AND /featurescript POSTs (incl. ?rollbackBarIndex=N)
```

## Key techniques

- **Rollback evaluation** — `/featurescript?rollbackBarIndex=N` evaluates the model at
  feature N's checkpoint, before later features fragment its geometry. Dissolves the
  opaque-`qCompressed` wall. Used for the oracle, cap-face regions, and modifier edges.
- **Per-region extrudes** (`caps_to_regions` + `region_extrude_candidates`) — one planar
  cap face = one region (outer + holes); extrude each region on its own workplane and
  union. Robust where a flat even-odd over several regions' loops corrupts (nested rings).
- **Robust edge selection** (`round_candidates` + `_edge_selector`) — `NearestToPoint`
  picks by centroid, so concentric circles tie. Query the live bodies' real edges, map
  fingerprint points to nearby edges by *true* distance, and isolate a circle with a
  `SubtractSelector` of two bbox `BoxSelector`s (radius band at its plane).
- **Candidate sketch planes** — when an extrude's profile is a `qCompressed` query, the
  plane link is unreliable; offer any sketch whose plane is parallel to and a cap-or-
  cap±depth offset from the caps, and let the oracle pick the plane+direction that abuts.
- **Amount perturbation** — offer a chamfer/fillet's exact value AND ×0.9999; OCCT rejects
  an op that exactly consumes a feature (1mm on a 1mm wall) where Onshape's kernel does not.

## Coverage (all verify end-to-end)

| model | features | steps | note |
|-------|----------|-------|------|
| washer | 12/12 | 22 | centered-annulus base, ×3 patterns, boolean, fillet, cut |
| can_jig | 2/2 | 1 | sketch + extrude |
| bearing_press | 6/6 | 6 | multi-region shared sketch |
| backstop | 8/8 | 7 | |
| bearing_presser | 8/8 | 11 | concentric-circle chamfers + 1mm-on-1mm (perturbed) |

**KPI target: L4 "Speed_toolhead"** (169 features: 59 extrude, 42 sketch, 18 fillet, 14
chamfer, 13 moveFace, 7 mirror, 5 cPlane, 3 sweep, 2 revolve/replaceFace, shell, hole,
splitPart, lighten). Adding a feature type = a new `*_candidates` translator; the oracle
keeps it honest. See `HANDOFF.md` → "What's next".

## Gotchas

- Onshape enforces a hard ~daily API cap; **never burst**. Cache + `resolve_*` let you
  iterate reconstruction fully offline after one fetch. The cap is per-ACCOUNT (a second
  key does not dodge it). Client now fails fast on a multi-hour `Retry-After`.
- The reconstruction runs candidates through `workplane_api`; the CLI entry MUST be under
  `if __name__ == "__main__"` (the worker uses spawn). Viewer tessellation is disabled in
  the engine (it OOMs during search).
- `verify`/worker run steps in a spawn subprocess because OCCT hangs are uninterruptible.
- Do NOT touch production `mcp_server/src/t2c_mcp.py` for reconstruction robustness — it
  belongs in the recon candidate search. (An in-engine fillet fallback was reverted.)
