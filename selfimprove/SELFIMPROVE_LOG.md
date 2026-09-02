# Self-improvement log — branch `selfimprove/assy6`

Human-readable trail of each loop run on this worktree's drawing. At merge time, read
this to decide which server edits (commits on this branch) are worth keeping.

## Run 20260901-115742 — drawing: `Assy 6.pdf` — target 92%

### Iter 1 — mode=build — accuracy=**75%** — t2c calls=39
- summary: Test summary for schema validation.
  - [low/modeling] test

## Run 20260901-125540 — drawing: `Assy 6.pdf` — target 92%

### Iter 1 — mode=build — accuracy=**60%** — t2c calls=35
- summary: The build reproduces all 12 BOM item types (14 pieces) and gets most individual parts right at the major-dimension level: knurled nut (Ø40×16, Ø20 bore, Ø6 radial hole), bush (Ø24×54, Ø18 bore, Ø3 oil hole), bearing block (Ø48/Ø40/Ø24×57), lock nut (hex M10×5), discs (2.5 thick, Ø4.5/Ø6.5 holes), all three set screws (M6×9, M8×11, M10×33 with 60° cone point and slots), and the base envelope (134×72×~73 with the 102-span mounting slots and Ø46 boss). However, the fork (item 2) — the defining swivel component — is modeled as a plain Ø52/Ø41 ring plus a Ø18 cross-bar instead of the drawing's Ø36 
  - [high/modeling] Fork (2) is geometrically wrong: built as a Ø52 OD / Ø41 bore ring + Ø18 cross-bar with Ø10 axial hole (seq 23-25), but the drawing requires a Ø36 hub with stepped bore Ø32/Ø26/Ø20, two 8-mm-thick splayed arms with R8/R15 fillets rising 54 tall to M10-tapped bosses over an ~88 span. A correct hub (f
  - [high/modeling] The two M10 set screws (11) do not engage fork arms; because the fork has no arms they are placed floating at y=±19 threaded into the cross-bar, whereas the drawing shows them tapped M10 into the fork arm bosses and locked by the two lock nuts (12).
  - [medium/modeling] Spindle (3) Ø26 collar is 30 mm long (Ø20×70 + Ø26×30, seq 33); the drawing shows a ~6-mm collar on a ~94-mm M20 shaft (overall 100). The mid-shaft relief groove is also not modeled.
  - [medium/modeling] All threads are modeled as plain cylinders: M20 spindle and base bore, M10/M8/M6 set screws, M10 lock-nut bore and M10 fork holes. The drawing explicitly calls out these threads (M20, M10, M8, M6).
  - [medium/modeling] Casting fillets omitted: base R4 web fillets and the 6-mm ribbed web are replaced by a smooth Ø70→Ø46 loft (seq 28); fork R8 and R15 fillets absent entirely.
  - [medium/modeling] Assembly uses only Fixed placements at hand-set coordinates; the spindle spans x≈+20…−80 while the bush/bearing block begin at x≈−84, so the spindle never reaches the bush — the axle-in-bearing engagement that defines the swivel bearing is not physically represented.
  - [low/modeling] Bearing block (9): flange thickness modeled 7.5 mm each vs the drawing's 6 mm (6+42+6=54); the Ø3-CSK-to-Ø6 oil hole and the Ø4/Ø15/Ø10 set-screw counterbore are collapsed to a single plain Ø6 radial hole (seq 20-21); a Ø10 counterbore solid (bb_cb, seq 19) was built but never applied.
  - [low/modeling] Base (1) boss Ø42 face recess/counterbore is not modeled and the M20 through-bore is a plain Ø20 hole (seq 32).
  - [low/modeling] Cosmetic features omitted: knurling on the knurled nut (4) and the oil groove on the bush (10) are not represented.
editor: Documented that `revolve`'s `axisStart/axisEnd` are in **local** workplane coordinates (not world) and that a profile crossing the axis silently yields a degenerate zero-volume solid — the docstring omission (confirmed against CadQuery's `toWorldCoords` source) that caused the builder's silent zero-
- **server edit committed**: `b929c1a298f591f6bd4984184c31f1a99feb3281` (was `6c826bb45537569bb95a38ded813598c36425c57`)

### Iter 2 — mode=build — accuracy=**75%** — t2c calls=53
- summary: A complete, coherent build: all 14 BOM instances present with correct quantities (incl. 2 pins, 2 lock nuts), a solved collision-free assembly matching the sheet-1 isometric layout, and correct principal dimensions for the bush (Ø24/Ø18/54/Ø3 oil hole), knurled nut (Ø40/16/Ø20/M6), discs (Ø4.5, Ø6.5, 2.5), set screws (M6x9, M8x11, M10x33), lock nuts (M10 hex, 5 thk), bearing block (Ø48/Ø40/Ø24, 6/42/6), spindle (Ø20/Ø26/100/6) and base envelope (134x72 foot, Ø70 pad, Ø46 boss, 72 tall, M20 bore, R5 end slots at 102 centres). Points are lost for pervasive omission of thread form (all M20/M10/M8
  - [medium/modeling] Fork central stepped bore Ø36/Ø32/Ø26/Ø20 (sheet 4, item 2) is not modeled; the build only has a Ø20 through-bore plus one ~Ø27 recess (cr_cut), missing the Ø36 and Ø32 steps and their 6/18/1.5 depths.
  - [medium/modeling] Bearing-block radial locking feature — Ø10 hole with Ø3 CSK at 90° to Ø6, offset 9 (sheet 5, item 9) — is omitted, and item-7 disc (Ø6.5) + item-8 set screw (M8) are placed axially on the X centreline at x=-36/-50 instead of as a radial screw pressing the disc on the bush/spindle. Purpose/location w
  - [medium/modeling] No thread geometry anywhere: spindle M20, base M20 tapped boss, knurled-nut M20, fork/block M10 pins and lock-nut M10, and M8/M6 set-screw holes are all modeled as plain cylinders/holes at the correct major diameter. Faithful thread form per the drawing callouts is absent.
  - [medium/modeling] Base column is a smooth revolved frustum (loft Ø70->Ø46 over 25) rather than the ribbed/tapered cast bracket with R4 fillets and 6 mm ribs shown on sheet 3; also the Ø42 counterbore at the Ø46 boss mouth is omitted.
  - [low/modeling] Bearing block overall length is 54 (6+42+6) but the drawing dimensions it at 57 (sheet 5, item 9); the Ø15/Ø4/R3 counter-seat is omitted and an unexplained Ø16 boss (block_boss) is added.
  - [low/modeling] Fork hub is Ø36 vs the drawing's R15 (Ø30), R8 fillets are omitted, and the arm outer span (68) is slightly under the ~76-88 implied by 14/60/14.
  - [low/modeling] Bush oil groove (curved slot on sheet 5, item 10) and the knurl on the knurled nut are not modeled; only the Ø3 oil hole is represented.
  - [low/modeling] Base mounting slots are modeled as plain through-slots though the drawing labels them M8; block pin seats are Ø8 vs the M10 pin's Ø10 tip.
editor: Documented the two opposite slot conventions in `sketch_api`/`workplane_api` docstrings — `slot2D(length, diameter)` uses overall end-to-end length (straight = length−diameter, so length must exceed diameter, else the silent `BRep_API: command not done` degenerate failure) while `Sketch.slot(w, h)` 
- **server edit committed**: `ed943ec583dbec6fda8d31749f15dcdc585f2c57` (was `b929c1a298f591f6bd4984184c31f1a99feb3281`)

## Run 20260902-130319 — drawing: `Assy 6.pdf` — target 95%

### Iter 1 — accuracy=**78%** — t2c calls=75
- summary: Faithful and near-complete build of the 12-part swivel bearing: all 14 BOM instances are present with correct materials, the spindle (Ø26 collar + Ø20 shaft, real M20×2.5 thread, 100 long), bush (Ø24/Ø18/54, Ø3 oil hole) and bearing block (Ø48/Ø40/Ø48, 54 tall, Ø24 bore, oil hole, two Ø10 pivot seats) are essentially exact, the fork's stepped bore Ø20/26/32/36 and 14+60+14=88 lug layout are correct, fastener sizes (M6×9, M8×11, M10×33, thin M10 lock nuts) are right, and the assembly solves cleanly (residual 0, no collisions/floating) with the correct three perpendicular axes. Points are lost f
  - [medium/modeling] Base foot length: front views (sheets 2 & 3) dimension the overall foot at 134, but the model foot is only 102 long (base bbox x ±51); the foot is ~32 mm short and the M8 slot positions (±40) are not tied to a drawing dimension.
  - [medium/modeling] Fork overall height: drawing = 54 (sheet 4), model fork is ~40 tall; the clevis arms are ~14 mm short and the cast profile (R8/R15 fillets, curved 8-thick web) is reduced to plain boxes.
  - [medium/modeling] Discs (parts 5 & 7) modeled as Ø9-OD / Ø11-OD washers with Ø4.5 / Ø6.5 center holes, whereas the drawing shows small solid brass discs of Ø4.5 and Ø6.5 (2.5 thick) with no bore — OD roughly doubled and spurious holes added.
  - [low/modeling] Internal threads not modeled: the base M20 boss (should be M20 tapped, ~Ø20) is a plain Ø21 hole, and the knurled-nut bore (should be M20 tapped) is a plain Ø21 hole; a threadedHole/tapHole or internal IsoThread was available.
  - [low/modeling] Base cast transition simplified: the 6 mm curved web with R4 fillets and the Ø70 pad→Ø46 boss horn are replaced by a smooth cone loft with no ribs/fillets.
  - [low/modeling] Bearing block left ~3.4° off vertical (rotation_deg ry 86.6 vs 90) by an under-constrained solve; only 6 of 14 parts are constraint-solved, the other 8 (discs, set screws, lock nuts) are placed by literal Location rather than constrained.
  - [low/modeling] Bearing-block oil-hole Ø3 countersink (Ø3 CSK at 90° to Ø6) omitted; only the Ø6 + Ø10 counterbore was cut.
  - [low/server-limitation] Set screws (parts 6, 8, 11) are drawn slotted with 60° cone/dog points, but only hex-socket geometry could be produced: extension_api SetScrew exposes exactly one standard_type, 'iso4026' (verified from the tool's own options output). No slotted/cone-point set-screw is available.
  - [low/server-limitation] workplane_api cylinder() ignores the 'direct' orientation vector — a requested X-axis cylinder (direct=(1,0,0)) returns geometry whose height still lies on Z (reproduced: Ø10×40 came out bbox z ±20). Real bug; the builder had to rebuild all radial features via circle+extrude on YZ/XZ planes (did not
  - [low/server-limitation] workplane_api box(centered=[True,True,False]) and extrude(both=True) both ignore the Z flag through the JSON layer (reproduced: 2.5×0.9×16 box returned z −8…+8 still centered; circle.extrude(8, both=True) returned z 0…8 one-sided). Real bugs; the builder worked around them so final part sizes are co
editor 1.1: Committed cleanly. Here's my summary.

## FIXED
- **Local-frame confusion behind the three "server-limitation" bugs (cylinder `direct`, box `centered`, extrude `both`)** — Empirically, all three work correctly in CadQuery *and* through the server's JSON `_run` dispatch. The reviewer's exact symptom ("Ø10×40 came out on global Z") only reproduces when the primitive is built on a **side plane** (`ri
- editor 1.1: Committed cleanly. Here's my summary.

## FIXED
- **Local-frame confusion behind the three "server-limitation" bugs (cylinder `direct`, box `centered`, extrude `both`)** — Empirically, all three work correctly in CadQuery *and* through the server's JSON `_run` dispatch. The reviewer's exact symptom ("Ø10×40 came out on global Z") only reproduces when the primitive is built on a **side plane** (`ri
- commits so far this iter: 837b88b docs(t2c): document local-frame semantics of primitive direct/centered args [iter 1.1] 
- verifier 1.1: edits_work=True unresolved=1 declined=7
editor 1.2: Committed. Working tree now shows only the pre-existing `SELFIMPROVE_LOG.md` change, which I left untouched.

## Summary

**FIXED**
- **SetScrew capability gap** — documented in `extension_api` that `SetScrew` is `iso4026` **only** (hex-socket, flat-point grub screw): no slotted/hex/square drive and no cone/dog/cup point exists, and instructed the builder to model such set screws from primitives (
- editor 1.2: Committed. Working tree now shows only the pre-existing `SELFIMPROVE_LOG.md` change, which I left untouched.

## Summary

**FIXED**
- **SetScrew capability gap** — documented in `extension_api` that `SetScrew` is `iso4026` **only** (hex-socket, flat-point grub screw): no slotted/hex/square drive and no cone/dog/cup point exists, and instructed the builder to model such set screws from primitives (
- commits so far this iter: 94e0b75 docs(t2c): document SetScrew iso4026-only limit and primitive fallback for slotted/cone-point set screws [iter 1.2] 837b88b docs(t2c): document local-frame semantics of primitive direct/centered args [iter 1.1] 
- verifier 1.2: edits_work=True unresolved=0 declined=7
- iter 1 verified: edits work and all server-fixable issues resolved
