// Human-judge mode. Run with:  npx promptfoo@latest eval --grader file://humanjudge.mjs
// npx promptfoo@latest view
//
// This replaces the LLM judge for every llm-rubric assertion (--grader overrides the
// judge globally). It makes no API call — it just defaults each capability cell to
// FAIL / score 0 and shows that task's success criteria, so you can read the transcript
// in `promptfoo view` and manually flip each cell's pass/fail + score yourself.
export default class {
  id() {
    return "human-judge";
  }
  async callApi(prompt) {
    // `prompt` is the rendered rubricPrompt; pull out the criteria block it contains.
    const m = String(prompt).match(/SUCCESS CRITERIA:\s*([\s\S]*?)\s*TRANSCRIPT:/);
    const criteria = m ? m[1].trim() : "Grade against the task's success criteria.";
    return {
      output: JSON.stringify({
        pass: false,
        score: 0,
        reason: "MANUAL REVIEW — override in the viewer. Grade against:\n" + criteria,
      }),
    };
  }
}
