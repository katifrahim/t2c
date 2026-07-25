// Tool-capable OpenRouter models. The first four are paid, premium models
// (top reasoning / tool-calling / vision) and require credits on the
// OPENROUTER_API_KEY to run; the rest are free ($0) models. Models marked
// `vision: true` also accept image input. A `reasoning` field, when set,
// requests that reasoning effort from OpenRouter (applied in api/chat).
//
// Refresh the free list with:
//   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[]
//     | select(.pricing.prompt=="0" and .pricing.completion=="0")
//     | select(.supported_parameters|index("tools"))
//     | "\(.id)\t\((.architecture.input_modalities//[])|index("image")!=null)"'
//
// Note: "openrouter/free" is OpenRouter's OWN server-side auto-router — it picks
// from ALL free models on OpenRouter, not just this list. The chat route reports
// back which model it actually used (shown under each reply).
export const MODELS = [
  // Premium (paid — need OpenRouter credits). IDs/capabilities verified against
  // the live /api/v1/models endpoint: all support tools + reasoning + image.
  { id: "deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash (Best + Cheap)", context: 1048576 }, // Ctx window: 1m toks
  { id: "z-ai/glm-5.2", label: "GLM-5.2 (Best + Expensive)", context: 1048576 },
  { id: "openai/gpt-oss-120b", label: "GPT-OSS 120B (Good)", context: 131072 }, // Ctx window: 131k toks
  { id: "xiaomi/mimo-v2.5", label: "Xiaomi Mimo V2.5 (Decent)", context: 1048576 }, // Ctx window: 1m toks
];

// Default: DeepSeek V4 Flash — cheap and a 1M-token context so long/complex
// sessions don't hit a wall. Users can switch models from the dropdown.
export const DEFAULT_MODEL = MODELS[0].id;

// Per-token prices ($/token) from OpenRouter, used only as a cost fallback when
// OpenRouter doesn't return the real cost (we prefer usage.cost from the response).
export const MODEL_PRICING = {
  "openai/gpt-oss-120b": { input: 0.03e-6, output: 0.15e-6 },
  "deepseek/deepseek-v4-flash": { input: 0.09e-6, output: 0.18e-6 },
  "xiaomi/mimo-v2.5": { input: 0.105e-6, output: 0.28e-6 },
};

// 1 credit = $0.001, so $2.50 of usage → 2500 credits.
export const CREDITS_PER_USD = 1000;
