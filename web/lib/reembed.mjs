// Incremental re-embed for the template library, used by the terminal script
// supabase/reembed-templates.mjs. Embeddings are generated from title+description;
// when you edit that text later (e.g. in the Supabase dashboard) the vector goes
// stale. This re-embeds ONLY rows whose text changed since their last embed —
// detected by comparing a content hash — so Gemini is never called for rows that
// are already up to date.

import { createHash } from "node:crypto";
import { embedText } from "./embeddings.mjs";

// Hash of the exact string we embed. Stored on the row as content_hash; a mismatch
// means the text was edited and the embedding needs regenerating.
export function contentHash(title, description) {
  return createHash("sha256").update(`${title}. ${description}`).digest("hex");
}

// Re-embed rows whose content_hash is stale/missing. `ids` optional to scope to
// specific rows. Returns { reembedded, skipped }. Caller supplies a Supabase client
// with write access to templates (service role).
export async function reembedStale(supabase, ids) {
  let q = supabase.from("templates").select("id,title,description,content_hash");
  if (ids?.length) q = q.in("id", ids);
  const { data: rows, error } = await q;
  if (error) throw error;

  let reembedded = 0;
  let skipped = 0;
  for (const t of rows ?? []) {
    const hash = contentHash(t.title, t.description);
    if (hash === t.content_hash) { skipped++; continue; } // up to date → no API call
    const embedding = await embedText(`${t.title}. ${t.description}`, "RETRIEVAL_DOCUMENT");
    const { error: upErr } = await supabase
      .from("templates")
      .update({ embedding, content_hash: hash })
      .eq("id", t.id);
    if (upErr) throw upErr;
    reembedded++;
  }
  return { reembedded, skipped };
}
