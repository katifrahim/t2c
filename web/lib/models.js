// The two workspace "modes", each backed by one OpenRouter model. The user never
// picks a model — api/chat routes by the CURRENT prompt: an image in the latest
// user message → Image-to-CAD (Opus, best at image→CAD); otherwise → Text-to-CAD
// (GPT, the cheaper default). The `mode` field is what api/chat routes on.
//
// BOTH models are vision-capable on OpenRouter (verified: tools + image input). GPT's
// vision matters even though it's the "text" mode: after an image turn, a follow-up
// text prompt routes back to GPT while the earlier image still sits in history — GPT
// must accept it or OpenRouter rejects the whole request. A `reasoning` field, when
// set, requests that reasoning effort from OpenRouter (applied in api/chat).
//
// Check a model's image support against the live endpoint with:
//   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[]
//     | select(.supported_parameters|index("tools"))
//     | "\(.id)\t\((.architecture.input_modalities//[])|index("image")!=null)"'
export const MODELS = [
  { id: "openai/gpt-5.6-sol", label: "Text-to-CAD", mode: "text", context: 1000000 }, // Ctx window: 1m toks
  { id: "anthropic/claude-opus-5", label: "Image-to-CAD", mode: "image", context: 1000000 }, // Ctx window: 1m toks
];

// Model per mode — the routing targets used by api/chat.
export const TEXT_TO_CAD_MODEL = MODELS.find((m) => m.mode === "text").id;
export const IMAGE_TO_CAD_MODEL = MODELS.find((m) => m.mode === "image").id;

// Default for background/non-chat rebuilds (e.g. template capture): the cheap text
// mode. Interactive chat turns are routed per-prompt in api/chat, not via this.
export const DEFAULT_MODEL = TEXT_TO_CAD_MODEL;

// Per-token prices ($/token) from OpenRouter, used only as a cost fallback when
// OpenRouter doesn't return the real cost (we prefer usage.cost from the response).
// Values match the live /api/v1/models endpoint; both models are covered.
export const MODEL_PRICING = {
  "openai/gpt-5.6-sol": { input: 4e-6, output: 20e-6 }, // Input: $4/m toks, Output: $20/m toks
  "anthropic/claude-opus-5": { input: 5e-6, output: 25e-6 }, // Input: $5/m toks, Output: $25/m toks
};

// 1 credit = $0.001, so $2.50 of usage → 2500 credits.
export const CREDITS_PER_USD = 1000;
