The file at this path is a technical engineering drawing — 2D orthographic views, dimensions, and a title block — of a single 3D object or assembly:

    {{DRAWING}}

Read it carefully, then build a high-fidelity BREP CAD replica of the object it depicts, using the T2C CAD tools. High fidelity, quality, and dimensional accuracy are crucial — reproduce every dimension, every feature (holes, fillets, chamfers, threads, pockets, counterbores), every part, and every spatial relationship shown in the drawing, at the correct sizes and positions.

Rules:
- Build the model ONLY through the T2C CAD tools (workplane, sketch, assembly, ready-made parts, etc.). Do not write or run code, and do not edit files — build purely through the tools.
- Work in millimetres unless the drawing states otherwise.
- Be critical and careful. Read the structured report each build/assembly call returns (collisions, floating/unconstrained parts, validity, dimensions) and act on it. When a method or parameter is unclear, look up its documentation before guessing. Inspect what you build, find your own mistakes, and fix them before you finish.
- Prefer constraints over hard-coded locations for multi-part models.

When you are satisfied the model faithfully matches the drawing, end your reply with a SINGLE fenced JSON block — and nothing after it — reporting every place where a T2C CAD tool or its documentation made high fidelity hard or impossible: a wrong, missing, or misleading parameter/behaviour doc; a tool that misbehaved or errored where it should have worked; or a capability that was simply absent (so you had to approximate or work around it). Be specific — name the tool, the method, the parameter, and the exact symptom you observed. Put every such point here (this is the complete record of tool/doc friction, so include anything you noted while building). Use an empty list only if nothing hindered you.

```json
{
  "friction": [
    "specific tool/method/parameter problem + the exact symptom that blocked or slowed faithful modelling"
  ],
  "build_note": "one line: what you built, and any part you could not fully reproduce and why"
}
```
