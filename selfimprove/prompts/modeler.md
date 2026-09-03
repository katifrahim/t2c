The file at this path is the 2D technical engineering drawing of a 3D object:
    {{DRAWING}}

Read it carefully, then use the available T2C CAD tools to build a highly-detailed, high-fidelity BREP CAD replica of the object it depicts. The model you build should be an exact replica of the object shown in the drawing. High fidelity, quality, and accuracy are crucial. Reproduce every dimension, every feature, every part, and every spatial relationship shown in the drawing, at the correct sizes and positions. Aim for 100% accuracy.

Rule:
- Build the model ONLY through the T2C CAD tools. Do not write/run any external code or edit files (that's cheating).

When you are satisfied the model accurately matches the drawing, end your reply with a SINGLE fenced JSON block (and nothing after it) reporting every place where a T2C CAD tool or its documentation made high-fidelity hard or impossible: tool operations/capabilities or tool docs that was wrong, missing, misleading, misbehaving or insufficient. You can also include useful things that you didn't know at the beginning, but later learned through trial and error, and wish that you had known upfront to avoid unnecessary friction. This basically covers everything that the report_learning tool is for, but you need to prioritize this JSON block instead of that tool.

Be specific (name the tool, the method, the parameter, and the exact symptom you observed). Put every such pain point here (this is the complete record of tool/doc friction, so include anything you noted while building). Use an empty list only if nothing hindered you. Never emit placeholder strings.

```json
{
  "friction": [
    "specific tool/method/parameter problem + the exact symptom that blocked or slowed faithful modelling"
  ],
  "build_note": "one line: what you built, and any part you could not fully reproduce and why"
}
```
