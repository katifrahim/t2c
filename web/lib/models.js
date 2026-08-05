// Tool-capable OpenRouter models. Only models marked `vision: true` accept image
// input; the others are text-only and OpenRouter rejects any image sent to them
// (api/chat guards against this). A `reasoning` field, when set, requests that
// reasoning effort from OpenRouter (applied in api/chat).
//
// Check a model's image support against the live endpoint with:
//   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[]
//     | select(.supported_parameters|index("tools"))
//     | "\(.id)\t\((.architecture.input_modalities//[])|index("image")!=null)"'
export const MODELS = [
  { id: "deepseek/deepseek-v4-flash", label: "DeepSeek V4 Flash (Best)", context: 1000000 }, // Ctx window: 1m toks
  { id: "z-ai/glm-5.2", label: "GLM 5.2 (Best)", context: 1000000 }, // Ctx window: 1m toks
  { id: "openai/gpt-oss-120b", label: "GPT OSS 120B (Good)", context: 131000 }, // Ctx window: 131k toks
  { id: "xiaomi/mimo-v2.5", label: "Xiaomi Mimo V2.5 (Vision)", context: 1000000, vision: true }, // Ctx window: 1m toks
  { id: "openai/gpt-5.6-luna-pro", label: "GPT 5.6 Luna (Vision)", context: 1000000, vision: true }, // Ctx window: 1m toks
  { id: "qwen/qwen3.8-max", label: "Qwen 3.8 Max (Vision)", reasoning: "high", context: 1000000, vision: true }, // Ctx window: 1m toks
  { id: "x-ai/grok-4.5", label: "Grok 4.5 (Vision)", context: 500000, vision: true }, // Ctx window: 500k toks
];

// Default: DeepSeek V4 Flash — cheap and a 1M-token context so long/complex
// sessions don't hit a wall. Users can switch models from the dropdown.
export const DEFAULT_MODEL = MODELS[0].id;

// Per-token prices ($/token) from OpenRouter, used only as a cost fallback when
// OpenRouter doesn't return the real cost (we prefer usage.cost from the response).
// Values match the live /api/v1/models endpoint; all four models are covered.
export const MODEL_PRICING = {
  "openai/gpt-oss-120b": { input: 0.04e-6, output: 0.17e-6 },
  "deepseek/deepseek-v4-flash": { input: 0.14e-6, output: 0.28e-6 },
  "z-ai/glm-5.2": { input: 0.67e-6, output: 2.1e-6 },
  "xiaomi/mimo-v2.5": { input: 0.14e-6, output: 0.28e-6 },
  "openai/gpt-5.6-luna-pro": { input: 1e-6, output: 6e-6 },
  "qwen/qwen3.8-max": { input: 2e-6, output: 6e-6 },
  "x-ai/grok-4.5": { input: 2e-6, output: 6e-6 },
};

// 1 credit = $0.001, so $2.50 of usage → 2500 credits.
export const CREDITS_PER_USD = 1000;
