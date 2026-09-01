The file at this path is a technical engineering drawing (2D orthographic views, dimensions, and a title block) of a single 3D object or assembly:

    {{DRAWING}}

Read it carefully and use ONLY the T2C MCP server tools to build a high-fidelity BREP CAD replica of the object it depicts. High fidelity, quality, and dimensional accuracy are crucial — reproduce every dimension, feature (holes, fillets, chamfers, threads, pockets), spatial relationship, and part shown in the drawing, at the correct sizes and positions.

Rules you MUST follow:
- Use ONLY the T2C MCP tools: workplane_api, sketch_api, assembly_api, extension_api, query_docs, select_model. Do NOT write raw Python, shell, or CLI code, and do NOT edit any files — that is cheating and is disallowed. Build the model purely through T2C tool calls.
- Work in millimetres unless the drawing states otherwise.
- Be critical and careful. Read the structured report each build/assembly call returns (collisions, floating/unconstrained parts, validity, dimensions). Call query_docs whenever a method or parameter is unclear. Inspect what you build, find your own mistakes, and fix them before you finish.
- Prefer constraints (assembly_api) over hard-coded locations for multi-part models.

{{FEEDBACK_BLOCK}}

When you have finished and are satisfied the model matches the drawing, end your reply with a section titled exactly:

## Friction

List, as a few short bullets, anything where a T2C tool limitation, a missing capability, a bug, or unclear / insufficient / incorrect tool documentation made it hard or impossible to reach higher fidelity. Be specific (name the tool, the method, the parameter, the doc line). If nothing blocked you, write exactly "None". This note is used to improve the T2C server, so it matters.
