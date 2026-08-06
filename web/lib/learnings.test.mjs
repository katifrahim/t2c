// Run with: node --test lib/learnings.test.mjs   (no test framework needed)
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  extractLearnings, learningTags, normalizeSeverity,
  LEARNING_TYPES, LEARNING_SEVERITIES,
} from "./learnings.mjs";

// A finished-turn `steps` array with the given tool calls (AI SDK v6 shape).
const stepWith = (...toolCalls) => [{ toolCalls }];

test("extracts a single well-formed report_learning call", () => {
  const steps = stepWith(
    { toolName: "workplane_api", input: { operations: [] } },
    { toolName: "report_learning", input: { type: "tool_bug", title: "t", detail: "d", severity: "high" } },
  );
  const out = extractLearnings(steps);
  assert.equal(out.length, 1);
  assert.equal(out[0].type, "tool_bug");
  assert.equal(out[0].severity, "high");
});

test("extracts multiple learnings across multiple steps", () => {
  const steps = [
    { toolCalls: [{ toolName: "report_learning", input: { type: "technique", title: "a", detail: "d" } }] },
    { toolCalls: [{ toolName: "report_learning", input: { type: "painpoint", title: "b", detail: "d" } }] },
  ];
  assert.equal(extractLearnings(steps).length, 2);
});

test("parses args passed as a JSON string", () => {
  const steps = stepWith({
    toolName: "report_learning",
    input: JSON.stringify({ type: "context_gap", title: "t", detail: "d" }),
  });
  assert.equal(extractLearnings(steps)[0].type, "context_gap");
});

test("falls back to .args when .input is absent", () => {
  const steps = stepWith({ toolName: "report_learning", args: { type: "technique", title: "t", detail: "d" } });
  assert.equal(extractLearnings(steps).length, 1);
});

test("drops malformed reports (unknown type, missing title/detail, bad JSON)", () => {
  const steps = stepWith(
    { toolName: "report_learning", input: { type: "nonsense", title: "t", detail: "d" } },
    { toolName: "report_learning", input: { type: "tool_bug", detail: "d" } },      // no title
    { toolName: "report_learning", input: { type: "tool_bug", title: "t" } },        // no detail
    { toolName: "report_learning", input: "{not json" },
  );
  assert.equal(extractLearnings(steps).length, 0);
});

test("returns empty for no steps / no learning calls", () => {
  assert.deepEqual(extractLearnings(undefined), []);
  assert.deepEqual(extractLearnings([]), []);
  assert.deepEqual(extractLearnings(stepWith({ toolName: "sketch_api", input: {} })), []);
});

test("learningTags emits distinct base + per-type tags", () => {
  const tags = learningTags([{ type: "tool_bug" }, { type: "tool_bug" }, { type: "technique" }]);
  assert.deepEqual(new Set(tags), new Set(["learning", "learning:tool_bug", "learning:technique"]));
});

test("learningTags is empty when there are no learnings", () => {
  assert.deepEqual(learningTags([]), []);
  assert.deepEqual(learningTags(undefined), []);
});

test("normalizeSeverity clamps unknown values to medium", () => {
  assert.equal(normalizeSeverity("high"), "high");
  assert.equal(normalizeSeverity("urgent"), "medium");
  assert.equal(normalizeSeverity(undefined), "medium");
});

test("taxonomy sets match the documented values", () => {
  assert.deepEqual(
    [...LEARNING_TYPES].sort(),
    ["context_gap", "missing_capability", "painpoint", "stale_context", "technique", "tool_bug", "tool_doc_error"],
  );
  assert.deepEqual(LEARNING_SEVERITIES, ["low", "medium", "high"]);
});
