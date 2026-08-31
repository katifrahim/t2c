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
  { id: "openai/gpt-5.6-sol", label: "Text-to-CAD", context: 1000000, vision: false }, // Ctx window: 1m toks
  { id: "anthropic/claude-opus-5", label: "Image-to-CAD", context: 1000000, vision: true }, // Ctx window: 1m toks
];

// Default: Text-to-CAD
export const DEFAULT_MODEL = MODELS[0].id;

// Per-token prices ($/token) from OpenRouter, used only as a cost fallback when
// OpenRouter doesn't return the real cost (we prefer usage.cost from the response).
// Values match the live /api/v1/models endpoint; both models are covered.
export const MODEL_PRICING = {
  "openai/gpt-5.6-sol": { input: 4e-6, output: 20e-6 }, // Input: $4/m toks, Output: $20/m toks
  "anthropic/claude-opus-5": { input: 5e-6, output: 25e-6 }, // Input: $5/m toks, Output: $25/m toks
};

// 1 credit = $0.001, so $2.50 of usage → 2500 credits.
export const CREDITS_PER_USD = 1000;
