// Text embeddings for the template RAG library, via Google Gemini (free tier).
// Used both to embed a template's title+description on save and to embed the user's
// request on each chat turn. Requires GOOGLE_GENERATIVE_AI_API_KEY.

import { google } from "@ai-sdk/google";
import { embed } from "ai";

// Must match the vector(768) column + HNSW index in supabase/templates.sql. 768 is
// one of Gemini's recommended Matryoshka sizes — plenty for a small library.
export const EMBED_DIM = 768;

const model = google.textEmbeddingModel("gemini-embedding-001");

// Cosine similarity needs unit-length vectors, but Gemini's truncated (sub-3072)
// Matryoshka embeddings aren't normalized — so we L2-normalize here.
function normalize(v) {
  let sum = 0;
  for (const x of v) sum += x * x;
  const norm = Math.sqrt(sum);
  return norm > 0 ? v.map((x) => x / norm) : v;
}

// Embed one string. taskType asymmetry matters: RETRIEVAL_DOCUMENT for stored
// templates, RETRIEVAL_QUERY for the user's request. Returns a normalized 768-dim
// array. Throws on an empty input or if the provider returns the wrong dimension
// (the AI SDK has silently ignored outputDimensionality before — fail loud instead).
export async function embedText(text, taskType) {
  const value = (text ?? "").trim();
  if (!value) throw new Error("embedText: empty input");
  const { embedding } = await embed({
    model,
    value,
    providerOptions: { google: { outputDimensionality: EMBED_DIM, taskType } },
  });
  if (embedding.length !== EMBED_DIM)
    throw new Error(`embedText: expected ${EMBED_DIM} dims, got ${embedding.length}`);
  return normalize(embedding);
}
