import { embedText } from "@/lib/embeddings";
import { createClient } from "@/lib/supabase/server";

// POST /api/templates — store a reviewed template in the shared RAG library. Called
// from the save-as-template popup after the user confirms the (editable) title and
// description that capture-template drafted. Embeds title+description and inserts one
// row; retrieval (api/chat) matches against it. See supabase/templates.sql.

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

export async function POST(req) {
  if (!CONFIGURED) return Response.json({ error: "not configured" }, { status: 400 });

  const { title, description, steps, verified, visibility } = await req.json().catch(() => ({}));
  const t = (title ?? "").trim();
  const d = (description ?? "").trim();
  if (!t || !d) return Response.json({ error: "title and description required" }, { status: 400 });
  if (!Array.isArray(steps) || steps.length === 0)
    return Response.json({ error: "steps required" }, { status: 400 });
  // Default to private; public templates start as 'pending' (DB default) until a dev
  // approves them. Anything but an explicit "public" is treated as private.
  const vis = visibility === "public" ? "public" : "private";

  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub ?? null;
  if (!uid) return Response.json({ error: "not authenticated" }, { status: 401 });

  // Embed what users search on (the prose), not the tool JSON. RETRIEVAL_DOCUMENT is
  // the storage-side task type; the query side uses RETRIEVAL_QUERY.
  let embedding;
  try {
    embedding = await embedText(`${t}. ${d}`, "RETRIEVAL_DOCUMENT");
  } catch (e) {
    console.error("template embed failed:", e);
    return Response.json({ error: "embed failed" }, { status: 502 });
  }

  // Insert only (no .select()): the templates table has no RLS select policy, so
  // reads go solely through match_templates(). RLS enforces user_id = auth.uid().
  // Only public templates enter the review queue; private ones leave review_status
  // NULL so `review_status = 'pending'` is a clean queue of public submissions.
  const { error } = await supabase.from("templates").insert({
    user_id: uid,
    title: t,
    description: d,
    steps,
    verified: verified === true,
    visibility: vis,
    review_status: vis === "public" ? "pending" : null,
    embedding,
  });
  if (error) {
    console.error("template insert failed:", error);
    return Response.json({ error: "store failed" }, { status: 500 });
  }

  return Response.json({ ok: true });
}
