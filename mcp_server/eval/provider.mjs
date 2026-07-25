// This uses the same tech-stack + architecture as production (web/app/api/chat/route.js):
//
// It returns a transcript that our tests grade: the agent's REASONING (for reasoning models), 
// its prose, and every tool CALL + result)
//
// Run the server first (HTTP transport):
//   MCP_TRANSPORT=http PORT=9000 ../.venv/bin/python ../src/t2c_mcp.py

import { createMCPClient } from "@ai-sdk/mcp";
import { createOpenRouter } from "@openrouter/ai-sdk-provider";
import { generateText, stepCountIs } from "ai";

const URL = process.env.T2C_URL ?? "http://localhost:9000";

// MCP tool results arrive wrapped as { content: [{ type: "text", text: "<json>" }] }.
// Pull out the actual t2c JSON string the server returned.
function toolText(output) {
  if (typeof output === "string") return output;
  if (output && Array.isArray(output.content)) {
    return output.content.map((c) => (typeof c?.text === "string" ? c.text : "")).join("");
  }
  return output == null ? "" : JSON.stringify(output);
}

export default class T2CProvider {
  constructor(options = {}) {
    this.providerId = options.id ?? "t2c";
    this.config = options.config ?? {};
  }
  id() {
    return this.providerId;
  }

  async callApi(prompt, context) {
    const { model, system, maxSteps = 12, reasoning, timeoutMs = 600000 } = this.config;
    const token = process.env.MCP_TOKEN;

    // Fresh server state per task (server keeps one shared object store).
    await fetch(`${URL}/clear`, { method: "POST" });

    const mcpClient = await createMCPClient({
      transport: {
        type: "http",
        url: `${URL}/mcp`,
        ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
      },
    });

    try {
      const openrouter = createOpenRouter({ apiKey: process.env.OPENROUTER_API_KEY });
      const result = await generateText({
        model: openrouter(model, { usage: { include: true } }), // ask OpenRouter for cost

        ...(reasoning ? { providerOptions: { openrouter: { reasoning: { effort: reasoning } } } } : {}),
        ...(system ? { system } : {}),
        messages: [{ role: "user", content: prompt }],
        tools: await mcpClient.tools(), // the t2c MCP tools, exactly as in route.js
        stopWhen: stepCountIs(maxSteps),
        maxRetries: 2,
        // Fail fast instead of hanging forever if the model stalls (common with
        // rate-limited :free models). Aborts the whole task after timeoutMs.
        abortSignal: AbortSignal.timeout(timeoutMs),
      });

      const log = [];
      let anyToolError = false;
      // Deterministic counters read straight from the tool-call JSON.
      const BUILD = new Set(["workplane_api", "sketch_api", "assembly_api"]);
      // Deterministic counters (select_model is intentionally excluded from all of these).
      let cadToolCalls = 0; // # of workplane/sketch/assembly invocations
      let docToolCalls = 0; // # of query_docs invocations
      let cadMethods = 0; // # of operations (method entries) across build calls
      let cadParams = 0; // # of params supplied to those operations
      let docMethodCount = 0; // # of methods looked up across query_docs calls
      let assemblyParts = 0; // parts in the final assembly (from its result)
      let sketchObjs = 0, workplaneObjs = 0, assyObjs = 0; // objects saved, by obj_type
      for (const step of result.steps) {
        if (step.reasoningText) log.push(`REASONING: ${step.reasoningText}`);
        if (step.text) log.push(`ASSISTANT: ${step.text}`);
        for (const call of step.toolCalls ?? []) {
          const res = (step.toolResults ?? []).find((r) => r.toolCallId === call.toolCallId);
          const out = res ? toolText(res.output) : "(no result)";
          let parsed = null;
          try { parsed = JSON.parse(out); } catch {}
          if (parsed?.status === "error" || out.includes('"status":"error"') || out.includes('"status": "error"')) anyToolError = true;

          const inp = call.input ?? {};
          if (BUILD.has(call.toolName)) {
            cadToolCalls += 1;
            for (const op of Array.isArray(inp.operations) ? inp.operations : []) {
              cadMethods += 1;
              cadParams += (Array.isArray(op.args) ? op.args.length : 0)
                + (op.params && typeof op.params === "object" ? Object.keys(op.params).length : 0)
                + (op.kwargs && typeof op.kwargs === "object" ? Object.keys(op.kwargs).length : 0);
            }
            // Count each saved object by the obj_type in its response.
            if (parsed?.status === "success") {
              const t = parsed.obj_type;
              if (t === "Sketch") sketchObjs += 1;
              else if (t === "Assembly") assyObjs += 1;
              else workplaneObjs += 1;
            }
          }
          if (call.toolName === "query_docs") {
            docToolCalls += 1;
            docMethodCount += Array.isArray(inp.methods) ? inp.methods.length : 0;
          }
          if (call.toolName === "assembly_api" && Number.isFinite(parsed?.properties?.object_count)) {
            assemblyParts = parsed.properties.object_count; // last successful assembly wins
          }

          log.push(`CALL ${call.toolName} ${JSON.stringify(call.input)}\n  -> ${out}`);
        }
      }
      // Agent-only cost + tokens (the LLM-judge's usage is billed separately, not here).
      // Returned at the TOP LEVEL below so promptfoo shows them in its native cost/token
      // columns (real numbers), not as score/1.0 percentage metrics.
      const u = result.totalUsage ?? {};
      const tokenUsage = { total: u.totalTokens ?? 0, prompt: u.inputTokens ?? 0, completion: u.outputTokens ?? 0 };
      let cost = 0;
      for (const step of result.steps) cost += step.providerMetadata?.openrouter?.usage?.cost ?? 0;
      if (!cost) cost = result.providerMetadata?.openrouter?.usage?.cost ?? 0;
      // Save a STEP of the final model: artifacts/<model>/<task id>.step
      let artifact = null;
      try {
        const exp = await fetch(`${URL}/export?fmt=step`);
        if (exp.ok) {
          const id = String(context?.vars?.id ?? "task").replace(/[^a-z0-9._-]+/gi, "_");
          const dir = `artifacts/${model.replace(/[^a-z0-9._-]+/gi, "_")}`;
          const fs = await import("node:fs/promises");
          await fs.mkdir(dir, { recursive: true });
          artifact = `${dir}/${id}.step`;
          await fs.writeFile(artifact, Buffer.from(await exp.arrayBuffer()));
        }
      } catch {}

      return {
        output: log.join("\n\n"),
        cost, // native promptfoo cost column (real $), agent only
        tokenUsage, // native promptfoo token column (real count), agent only
        metadata: {
          anyToolError, artifact, cadToolCalls, docToolCalls, cadMethods, cadParams,
          docMethodCount, assemblyParts, sketchObjs, workplaneObjs, assyObjs,
        },
      };
    } catch (e) {
      return { error: String(e?.message ?? e) };
    } finally {
      await mcpClient.close();
    }
  }
}
