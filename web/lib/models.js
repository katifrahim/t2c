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
  { id: "anthropic/claude-sonnet-4.6", label: "Claude Sonnet 4.6 (High)", vision: true, reasoning: "high" },
  { id: "anthropic/claude-opus-4.8", label: "Claude Opus 4.8 (High)", vision: true, reasoning: "high" },
  { id: "openai/gpt-5.5", label: "GPT-5.5", vision: true },
  { id: "google/gemini-3.1-pro-preview", label: "Gemini 3.1 Pro", vision: true },

  // Free
  { id: "openai/gpt-oss-120b:free", label: "GPT-OSS 120B" },
  { id: "qwen/qwen3-coder:free", label: "Qwen3 Coder 480B" },
  { id: "meta-llama/llama-3.3-70b-instruct:free", label: "Llama 3.3 70B" },
  { id: "nvidia/nemotron-3-super-120b-a12b:free", label: "Nemotron 3 Super 120B" },
  { id: "nvidia/nemotron-3-ultra-550b-a55b:free", label: "Nemotron 3 Ultra 550B" },
  { id: "qwen/qwen3-next-80b-a3b-instruct:free", label: "Qwen3 Next 80B" },
  { id: "openai/gpt-oss-20b:free", label: "GPT-OSS 20B" },
  { id: "google/gemma-4-31b-it:free", label: "Gemma 4 31B", vision: true },
  { id: "google/gemma-4-26b-a4b-it:free", label: "Gemma 4 26B", vision: true },
  { id: "nvidia/nemotron-nano-12b-v2-vl:free", label: "Nemotron Nano 12B VL", vision: true },
  { id: "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free", label: "Nemotron 3 Nano Omni", vision: true },
  { id: "nvidia/nemotron-3-nano-30b-a3b:free", label: "Nemotron 3 Nano 30B" },
  { id: "nvidia/nemotron-nano-9b-v2:free", label: "Nemotron Nano 9B" },
  { id: "cohere/north-mini-code:free", label: "Cohere North Mini Code" },
  { id: "liquid/lfm-2.5-1.2b-thinking:free", label: "LFM 2.5 1.2B Thinking" },
  { id: "poolside/laguna-m.1:free", label: "Laguna M.1" },
  { id: "poolside/laguna-xs.2:free", label: "Laguna XS.2" },
  { id: "openrouter/free", label: "Auto (free router)" },
];

// Claude Sonnet 4.6: strong, reliable tool-calling for the CAD method chains.
export const DEFAULT_MODEL = MODELS[0].id;
