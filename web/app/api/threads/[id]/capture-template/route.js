import { createMCPClient } from "@ai-sdk/mcp";
import { createOpenRouter } from "@openrouter/ai-sdk-provider";
import { generateObject, generateText, stepCountIs } from "ai";
import { z } from "zod";
import { DEFAULT_MODEL, MODEL_PRICING, CREDITS_PER_USD } from "@/lib/models";
import { createClient } from "@/lib/supabase/server";
import {
  buildReferenceTranscript,
  templateFromSteps,
  finalSignatureFromSteps,
  finalSignatureFromMessages,
  signaturesMatch,
} from "@/lib/template-extract";

// One capture runs a full agent turn (rebuilds the model from scratch), so give it
// the same headroom as a chat turn.
export const maxDuration = 300;

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

const CAPTURE_SYSTEM = `You reproduce a finished CAD model as a clean, reusable template.
You are given a REFERENCE of an earlier conversation in which the model was built with some trial-and-error. Your scratch workspace is EMPTY — nothing has been built yet.
Rebuild ONLY the final model from scratch by actually calling the CAD build tools now, in order, until it is complete and active. Use the reference's successful calls for exact dimensions and operations; skip abandoned experiments and iterations. Do NOT call the docs tool. Do NOT write explanations — just make the tool calls.`;

// Real $ cost of one rebuild turn — prefer OpenRouter's cache-aware per-step cost,
// fall back to token math. Same basis the chat route bills on.
function turnCost({ steps, totalUsage }) {
  let cost = 0;
  let hasReal = false;
  for (const s of steps ?? []) {
    const c = s.providerMetadata?.openrouter?.usage?.cost ?? s.providerMetadata?.openrouter?.cost;
    if (typeof c === "number") { cost += c; hasReal = true; }
  }
  if (hasReal) return cost;
  const p = MODEL_PRICING[DEFAULT_MODEL];
  if (p && totalUsage) return (totalUsage.inputTokens ?? 0) * p.input + (totalUsage.outputTokens ?? 0) * p.output;
  return 0;
}

// Same $ basis for a single (non-agentic) call like the metadata draft below.
function callCost({ usage, providerMetadata }) {
  const c = providerMetadata?.openrouter?.usage?.cost ?? providerMetadata?.openrouter?.cost;
  if (typeof c === "number") return c;
  const p = MODEL_PRICING[DEFAULT_MODEL];
  if (p && usage) return (usage.inputTokens ?? 0) * p.input + (usage.outputTokens ?? 0) * p.output;
  return 0;
}

const META_SYSTEM = `You write a short catalog entry for a reusable CAD template so a user can later find it by describing what they want to build.
Given a reference of how a model was built, output:
- title: 1-3 words naming the object (e.g. "Hex Bolt", "Wall Bracket").
- description: one plain-language sentence describing what the object is and its notable features — the kind of phrasing a user would type when searching. Do NOT mention tools, code, or implementation.`;

const MetaSchema = z.object({
  title: z.string().max(40),
  description: z.string().max(300),
});

// Draft a title + description for the captured template from the build reference.
// Best-effort: on any failure return blanks so the review popup still opens and the
// user can fill them in. Returns { title, description, cost, inputTokens, outputTokens }.
async function draftMetadata({ transcript, openrouter }) {
  try {
    const { object, usage, providerMetadata } = await generateObject({
      model: openrouter(DEFAULT_MODEL, {
        provider: { ignore: ["Groq", "groq"], require_parameters: true },
      }),
      providerOptions: { openrouter: { usage: { include: true } } },
      schema: MetaSchema,
      system: META_SYSTEM,
      prompt: `REFERENCE (how the model was built):\n\n${transcript}`,
    });
    return {
      title: object.title ?? "",
      description: object.description ?? "",
      cost: callCost({ usage, providerMetadata }),
      inputTokens: usage?.inputTokens ?? 0,
      outputTokens: usage?.outputTokens ?? 0,
    };
  } catch (e) {
    console.error("capture-template metadata draft failed:", e);
    return { title: "", description: "", cost: 0, inputTokens: 0, outputTokens: 0 };
  }
}

// Export the freshly-built scratch session as a snapshot blob (base64), so we can
// store it as the template's 3D preview model. Same format as a chat snapshot —
// pickled geometry, NOT the steps. Best-effort: returns null on any failure.
async function exportModel(backendUrl, token, scratch) {
  try {
    const resp = await fetch(`${backendUrl}/session/export?session=${encodeURIComponent(scratch)}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (resp.status !== 200) return null; // 404 = empty session
    return Buffer.from(await resp.arrayBuffer()).toString("base64");
  } catch (e) {
    console.error("capture-template model export failed:", e);
    return null;
  }
}

// Rebuild the model once in a fresh scratch session; return steps + usage + model.
async function runRebuild({ backendUrl, token, scratch, transcript, openrouter }) {
  const mcpClient = await createMCPClient({
    transport: {
      type: "http",
      url: `${backendUrl}/mcp`,
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        "X-Session-Id": scratch,
      },
    },
  });
  try {
    const { steps, totalUsage } = await generateText({
      model: openrouter(DEFAULT_MODEL, {
        provider: { ignore: ["Groq", "groq"], require_parameters: true },
      }),
      providerOptions: { openrouter: { usage: { include: true } } }, // real cost per step
      system: CAPTURE_SYSTEM,
      messages: [
        {
          role: "user",
          content: `REFERENCE (earlier build — for dimensions/operations only):\n\n${transcript}\n\nNow rebuild the final model from scratch by calling the tools.`,
        },
      ],
      tools: await mcpClient.tools(),
      stopWhen: stepCountIs(50),
    });
    // Snapshot the built model before the finally clears the scratch session.
    const model = await exportModel(backendUrl, token, scratch);
    return { steps, totalUsage, model };
  } finally {
    try { await mcpClient.close(); } catch { /* already closed */ }
    // Free the scratch session's objects on the backend.
    fetch(`${backendUrl}/clear?session=${encodeURIComponent(scratch)}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    }).catch(() => {});
  }
}

// POST /api/threads/[id]/capture-template — rebuild the chat's current model from
// scratch in an isolated session and return its tool-call sequence as a template.
export async function POST(_req, { params }) {
  if (!CONFIGURED) return Response.json({ error: "not configured" }, { status: 400 });
  const { id } = await params;

  const supabase = await createClient();
  const { data: rows, error } = await supabase
    .from("messages")
    .select("id,content")
    .eq("chat_id", id)
    .order("created_at", { ascending: true });
  if (error) return Response.json({ error: "load failed" }, { status: 500 });

  const transcript = buildReferenceTranscript(rows ?? []);
  if (!transcript) return Response.json({ error: "empty chat" }, { status: 400 });

  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  const openrouter = createOpenRouter({ apiKey: process.env.OPENROUTER_API_KEY });

  const liveSig = finalSignatureFromMessages(rows ?? []);

  // Rebuild; if we can positively tell it doesn't match the live model, retry once.
  // A capture is a full agent turn, so meter every attempt's real cost.
  let template = [];
  let verified = null;
  let model = null; // base64 snapshot of the accepted attempt's built model
  let cost = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    const scratch = `__TEMPLATE__${id}__${Date.now()}`;
    let steps, totalUsage, attemptModel;
    try {
      ({ steps, totalUsage, model: attemptModel } = await runRebuild({ backendUrl, token, scratch, transcript, openrouter }));
    } catch (e) {
      console.error("capture-template rebuild failed:", e);
      return Response.json({ error: "rebuild failed" }, { status: 502 });
    }
    cost += turnCost({ steps, totalUsage });
    inputTokens += totalUsage?.inputTokens ?? 0;
    outputTokens += totalUsage?.outputTokens ?? 0;
    template = templateFromSteps(steps);
    verified = signaturesMatch(finalSignatureFromSteps(steps), liveSig);
    model = attemptModel; // keep the latest (= accepted, since we break on accept)
    if (verified !== false) break; // matched, or nothing to compare against
  }

  if (!template.length) return Response.json({ error: "no model built" }, { status: 422 });

  // Draft a title + description for the review popup, and fold its cost into the bill.
  const meta = await draftMetadata({ transcript, openrouter });
  cost += meta.cost;
  inputTokens += meta.inputTokens;
  outputTokens += meta.outputTokens;

  // Bill the capture to the user's credits (only a productive capture is charged).
  // charge_usage is a SECURITY DEFINER RPC keyed off auth.uid() that floors the
  // balance at 0, so the authenticated client can only charge its own account.
  const credits = cost > 0 ? Math.ceil(cost * CREDITS_PER_USD) : 0;
  if (credits > 0) {
    try {
      await supabase.rpc("charge_usage", {
        p_chat_id: id,
        p_model: DEFAULT_MODEL,
        p_input: inputTokens,
        p_output: outputTokens,
        p_cost: cost,
        p_credits: credits,
        p_trace_id: null,
      });
    } catch (e) {
      console.error("capture-template charge failed:", e);
    }
  }

  return Response.json({
    template,
    verified,
    model, // base64 3D preview snapshot (may be null), stored on save
    count: template.length,
    credits,
    title: meta.title,
    description: meta.description,
  });
}
