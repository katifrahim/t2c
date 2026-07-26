// Tool-capable OpenRouter models. All are paid and require credits on the
// OPENROUTER_API_KEY to run. Only models marked `vision: true` accept image
// input — the others are text-only and OpenRouter rejects any image sent to
// them (api/chat guards against this). A `reasoning` field, when set, requests
// that reasoning effort from OpenRouter (applied in api/chat).
//
// Check a model's image support against the live endpoint with:
//   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[]
//     | select(.supported_parameters|index("tools"))
//     | "\(.id)\t\((.architecture.input_modalities//[])|index("image")!=null)"'
//
// Note: "openrouter/free" is OpenRouter's OWN server-side auto-router — it picks
// from ALL free models on OpenRouter, not just this list. The chat route reports
// back which model it actually used (shown under each reply).
export const MODELS = [
  // Text-only (no image input) — verified against live /api/v1/models.
  { id: "deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash (Best + Cheap)", context: 1048576 }, // Ctx window: 1m toks
  { id: "z-ai/glm-5.2", label: "GLM-5.2 (Best + Expensive)", context: 1048576 },
  { id: "openai/gpt-oss-120b", label: "GPT-OSS 120B (Good)", context: 131072 }, // Ctx window: 131k toks
  // Vision-capable (accept image input) — verified against live /api/v1/models.
  { id: "xiaomi/mimo-v2.5", label: "Xiaomi Mimo V2.5 (Decent)", context: 1048576, vision: true }, // Ctx window: 1m toks
  { id: "qwen/qwen3.5-flash-02-23", label: "Qwen3.5 Flash (Fast + Vision)", context: 1000000, vision: true }, // Ctx window: 1m toks
  { id: "google/gemini-3.1-flash-lite", label: "Gemini 3.1 Flash (Strong + Vision)", context: 1048576, vision: true }, // Ctx window: 1m toks
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
  "qwen/qwen3.5-flash-02-23": { input: 0.07e-6, output: 0.26e-6 },
  "google/gemini-3.1-flash-lite": { input: 0.25e-6, output: 1.5e-6 },
};

// 1 credit = $0.001, so $2.50 of usage → 2500 credits.
export const CREDITS_PER_USD = 1000;
