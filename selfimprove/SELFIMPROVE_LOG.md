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
