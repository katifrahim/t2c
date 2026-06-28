// Free, tool-capable OpenRouter models (every $0 model whose
// supported_parameters include "tools"). Ordered best-first for reliable tool
// calling; gpt-oss-120b is the verified default. Models marked `vision: true`
// also accept image input. Refresh with:
//   curl -s https://openrouter.ai/api/v1/models | jq -r '.data[]
//     | select(.pricing.prompt=="0" and .pricing.completion=="0")
//     | select(.supported_parameters|index("tools"))
//     | "\(.id)\t\((.architecture.input_modalities//[])|index("image")!=null)"'
//
// Note: "openrouter/free" is OpenRouter's OWN server-side auto-router — it picks
// from ALL free models on OpenRouter, not just this list. The chat route reports
// back which model it actually used (shown under each reply).
export const MODELS = [
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

// gpt-oss-120b: best at the precise JSON / method-chaining the CAD tools expect.
export const DEFAULT_MODEL = MODELS[0].id;
