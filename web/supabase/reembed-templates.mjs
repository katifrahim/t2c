// Re-embed templates whose title/description you edited (e.g. in the Supabase
// dashboard). Embeddings are made at insert time and don't auto-update when you
// later change the text, so run this to resync. It only re-embeds rows whose text
// actually changed (content_hash mismatch) — up-to-date rows are skipped, so no
// Gemini quota is wasted. Run it whenever you're done editing.
//
// Re-embed changed rows:      node --env-file=.env.local supabase/reembed-templates.mjs
// Limit to specific rows:     node --env-file=.env.local supabase/reembed-templates.mjs <id> [<id> ...]
//
// Runs the same locally or against prod — needs only env:
//   NEXT_PUBLIC_SUPABASE_URL, SUPABASE_SECRET_KEY (bypasses RLS),
//   GOOGLE_GENERATIVE_AI_API_KEY (embeddings).

import { createClient } from "@supabase/supabase-js";
import { reembedStale } from "../lib/reembed.mjs";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SECRET_KEY;
if (!url || !key) {
  console.error("Missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SECRET_KEY.");
  process.exit(1);
}

const supabase = createClient(url, key, { auth: { persistSession: false } });
const ids = process.argv.slice(2); // optional: only these rows

const { reembedded, skipped } = await reembedStale(supabase, ids);
console.log(`Re-embedded ${reembedded} changed template(s); skipped ${skipped} already up to date.`);
