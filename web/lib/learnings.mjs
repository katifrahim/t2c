// Pure, dependency-free helpers for the agent self-improvement channel. Kept out
// of route.js (and free of any Next/Langfuse imports) so they're trivially unit-
// testable with `node --test` and reused by the Langfuse write in the chat route.
//
// The MCP `report_learning` tool lets the agent privately flag things the devs
// should act on. It only acks; the chat route reads these calls off the finished
// turn's steps and writes them to Langfuse.

export const LEARNING_DATASET = "agent-learnings";

// The taxonomy the agent reports under (mirrors the MCP tool's _LEARNING_TYPES).
export const LEARNING_TYPES = new Set([
  "missing_capability", // a capability no tool offers
  "tool_doc_error",     // a tool's doc/schema is wrong/misleading/incomplete
  "tool_bug",           // a tool errors or misbehaves when used correctly
  "context_gap",        // a fact the agent wished it had up front (add to context)
  "stale_context",      // something in the agent's context is wrong (remove it)
  "technique",          // a reusable trick/recipe found by trial-and-error
  "painpoint",          // recurring friction / repeated mistake / user frustration
]);

export const LEARNING_SEVERITIES = ["low", "medium", "high"];

// Pull the report_learning calls (with their args) out of a finished turn's steps.
// Defensive: tolerates missing steps/toolCalls, args as object or JSON string, and
// silently drops malformed reports (unknown type or missing title/detail).
export function extractLearnings(steps) {
  const out = [];
  for (const step of steps ?? []) {
    for (const tc of step?.toolCalls ?? []) {
      if (tc?.toolName !== "report_learning") continue;
      let a = tc.input ?? tc.args ?? {};
      if (typeof a === "string") {
        try { a = JSON.parse(a); } catch { a = null; }
      }
      if (a && LEARNING_TYPES.has(a.type) && a.title && a.detail) out.push(a);
    }
  }
  return out;
}

// Distinct trace tags for a turn's learnings: "learning" + "learning:<type>", so
// the Langfuse Traces view filters straight to turns that produced a learning.
export function learningTags(learnings) {
  return [...new Set((learnings ?? []).flatMap((l) => ["learning", `learning:${l.type}`]))];
}

// Normalize a soft severity field to a known value (defaults to "medium").
export function normalizeSeverity(severity) {
  return LEARNING_SEVERITIES.includes(severity) ? severity : "medium";
}
