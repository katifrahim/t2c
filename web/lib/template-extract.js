// Turn an agent's CAD build into a reusable template + verify it matches the model.
//
// The capture flow (see app/api/threads/[id]/capture-template) runs one isolated
// agent turn that rebuilds ONLY the final model from an empty session, so its tool
// calls ARE the clean template — no history pruning, no trimming. These helpers
// shape those calls and check the rebuild reproduced the model shown in the viewer.

const CAD_TOOLS = new Set([
  "workplane_api",
  "sketch_api",
  "assembly_api",
  "select_model",
]);

// Pull toolName out of a call/part, or null if it isn't a CAD tool call.
function cadToolName(x) {
  const name =
    x?.toolName ??
    (typeof x?.type === "string" && x.type.startsWith("tool-")
      ? x.type.slice(5)
      : null);
  return name && CAD_TOOLS.has(name) ? name : null;
}

// AI-SDK generateText steps -> the template array: one entry per CAD tool call, in
// order, with the input copied verbatim (already the exact MCP payload). query_docs
// and any non-CAD calls are dropped. `step` is just the 1-based sequence number
// ("Step N") — the model's prose is noise, the ordering is the only useful signal.
export function templateFromSteps(steps) {
  const out = [];
  for (const step of steps ?? []) {
    for (const call of step.toolCalls ?? []) {
      const name = cadToolName(call);
      if (!name) continue;
      out.push({ step: `Step ${out.length + 1}`, toolName: name, input: call.input });
    }
  }
  return out;
}

// A plain-text reference of the conversation for the capture turn: the intent
// (user/assistant prose) plus every prior CAD call's exact input as JSON. Passed as
// REFERENCE (not as executed tool messages) so the agent re-issues the calls into
// the empty scratch session instead of assuming the model already exists.
export function buildReferenceTranscript(rows) {
  const lines = [];
  for (const row of rows ?? []) {
    const msg = row?.content ?? row;
    if (!Array.isArray(msg?.parts)) continue;
    const text = msg.parts.filter((p) => p.type === "text").map((p) => p.text).join("\n").trim();
    if (msg.role === "user") {
      if (text) lines.push(`USER: ${text}`);
    } else if (msg.role === "assistant") {
      if (text) lines.push(`ASSISTANT: ${text}`);
      for (const part of msg.parts) {
        if (cadToolName(part) && part.state === "output-available")
          lines.push(`CALL ${cadToolName(part)} ${JSON.stringify(part.input)}`);
      }
    }
  }
  return lines.join("\n\n");
}

// MCP tool output arrives as a JSON string, an object, or an MCP content array;
// return the parsed { status, obj_type, properties } object, or null.
function parseOutput(output) {
  let o = output;
  if (Array.isArray(o?.content)) {
    const t = o.content.find((c) => c?.type === "text")?.text;
    o = t ?? o;
  }
  if (typeof o === "string") {
    try { o = JSON.parse(o); } catch { return null; }
  }
  return o && typeof o === "object" ? o : null;
}

// Geometry signature (obj_type + volume + assembly part count) of the LAST CAD call
// — for a build turn this describes the final, active model. Works on generateText
// steps (toolResults) or on persisted messages (assistant parts).
function signatureFrom(results) {
  for (let i = results.length - 1; i >= 0; i--) {
    const p = parseOutput(results[i]);
    if (!p || p.status === "error") continue;
    const props = p.properties ?? {};
    return {
      objType: p.obj_type ?? null,
      volume: typeof props.volume === "number" ? props.volume : null,
      objectCount: typeof props.object_count === "number" ? props.object_count : null,
    };
  }
  return null;
}

export function finalSignatureFromSteps(steps) {
  const results = [];
  for (const step of steps ?? [])
    for (const r of step.toolResults ?? [])
      if (cadToolName(r)) results.push(r.output);
  return signatureFrom(results);
}

export function finalSignatureFromMessages(rows) {
  const results = [];
  for (const row of rows ?? []) {
    const msg = row?.content ?? row;
    if (msg?.role !== "assistant" || !Array.isArray(msg.parts)) continue;
    for (const part of msg.parts)
      if (cadToolName(part) && part.state === "output-available") results.push(part.output);
  }
  return signatureFrom(results);
}

// Do the rebuilt model and the live model match? Loose on floats, strict on kind.
// Returns null when we lack the data to judge (caller treats null as "unverified").
export function signaturesMatch(a, b) {
  if (!a || !b) return null;
  if (a.objType !== b.objType) return false;
  if (a.objectCount != null && b.objectCount != null && a.objectCount !== b.objectCount)
    return false;
  if (a.volume != null && b.volume != null) {
    const denom = Math.max(Math.abs(a.volume), Math.abs(b.volume), 1e-9);
    return Math.abs(a.volume - b.volume) / denom < 0.01; // within 1%
  }
  return null; // no volume to compare → inconclusive
}
