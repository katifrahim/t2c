import { frontendTools } from "@assistant-ui/react-ai-sdk";
import { createMCPClient } from "@ai-sdk/mcp";
import { createOpenRouter } from "@openrouter/ai-sdk-provider";
import { streamText, convertToModelMessages, stepCountIs } from "ai";
import { MODELS, DEFAULT_MODEL } from "@/lib/models";

// Agentic tool loops can run several round-trips; give them room.
export const maxDuration = 120;

const SYSTEM_PROMPT = [
  "You are a CAD modeling assistant. You build and modify 3D parametric models",
  "by calling the available tools. Work step by step: set up workplanes and",
  "sketches, then extrude/cut, and combine parts into assemblies when needed.",
  "If you are unsure about a method, its parameters, or its behavior, call the",
  "documentation tool before using it rather than guessing. After building,",
  "briefly tell the user what you made — keep it short and let the 3D viewer",
  "show the result.",
].join(" ");

export async function POST(req) {
  const { messages, system, tools, model } = await req.json();

  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;

  // One MCP client per request, connected to the t2c FastMCP server over
  // streamable HTTP. Closed when the response finishes (see onFinish/onError).
  const mcpClient = await createMCPClient({
    transport: {
      type: "http",
      url: `${backendUrl}/mcp`,
      ...(token ? { headers: { Authorization: `Bearer ${token}` } } : {}),
    },
  });
  const mcpTools = await mcpClient.tools();

  const openrouter = createOpenRouter({
    apiKey: process.env.OPENROUTER_API_KEY,
  });

  const selectedModel = model || DEFAULT_MODEL;
  // Per-model reasoning effort (e.g. the premium "High" models). OpenRouter takes
  // this as a request parameter, not part of the model id.
  const reasoning = MODELS.find((m) => m.id === selectedModel)?.reasoning;

  const result = streamText({
    model: openrouter(selectedModel),
    ...(reasoning
      ? { providerOptions: { openrouter: { reasoning: { effort: reasoning } } } }
      : {}),
    system: [SYSTEM_PROMPT, system].filter(Boolean).join("\n\n"),
    messages: await convertToModelMessages(messages),
    tools: {
      ...mcpTools, // server-side t2c tools (executed here via the MCP client)
      ...frontendTools(tools ?? {}), // any client-side tools assistant-ui forwards
    },
    // Multi-step agentic loop: CAD tools naturally chain
    // (workplane -> sketch -> extrude -> assembly).
    stopWhen: stepCountIs(12),
    onFinish: () => mcpClient.close(),
    onError: () => mcpClient.close(),
  });

  return result.toUIMessageStreamResponse({
    // Surface the real error instead of AI SDK's generic "An error occurred".
    // Free OpenRouter models are rate-limited, so you want to see the 429.
    onError: (error) => (error instanceof Error ? error.message : String(error)),
    // Report which model actually answered. For "openrouter/free" (the auto
    // router) this is the resolved model OpenRouter picked, not the router id.
    messageMetadata: ({ part }) => {
      if (part.type === "finish") {
        return { model: part.response?.modelId };
      }
    },
  });
}
