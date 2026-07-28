import { createMCPClient } from "@ai-sdk/mcp";
import { createOpenRouter } from "@openrouter/ai-sdk-provider";
import { generateText, stepCountIs } from "ai";
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

// Rebuild the model once in a fresh scratch session; return steps + usage.
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
    return { steps, totalUsage };
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
  let cost = 0;
  let inputTokens = 0;
  let outputTokens = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    const scratch = `__TEMPLATE__${id}__${Date.now()}`;
    let steps, totalUsage;
    try {
      ({ steps, totalUsage } = await runRebuild({ backendUrl, token, scratch, transcript, openrouter }));
    } catch (e) {
      console.error("capture-template rebuild failed:", e);
      return Response.json({ error: "rebuild failed" }, { status: 502 });
    }
    cost += turnCost({ steps, totalUsage });
    inputTokens += totalUsage?.inputTokens ?? 0;
    outputTokens += totalUsage?.outputTokens ?? 0;
    template = templateFromSteps(steps);
    verified = signaturesMatch(finalSignatureFromSteps(steps), liveSig);
    if (verified !== false) break; // matched, or nothing to compare against
  }

  if (!template.length) return Response.json({ error: "no model built" }, { status: 422 });

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

  return Response.json({ template, verified, count: template.length, credits });
}
