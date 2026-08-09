import { embedText } from "@/lib/embeddings";
import { contentHash } from "@/lib/reembed";
import { createClient } from "@/lib/supabase/server";
import { createServiceClient, TEMPLATE_BUCKET } from "@/lib/supabase/service";

// /api/templates
//   GET  — list the templates the caller may see (their own + approved public), for
//          the Template Library panel. Returns everything EXCEPT `steps` (via the
//          list_templates RPC), so the build recipe never reaches the browser.
//   POST — store a reviewed template in the shared RAG library. Called from the
//          save-as-template popup after the user confirms the (editable) title and
//          description that capture-template drafted. Embeds title+description,
//          inserts one row, and (best-effort) uploads the captured 3D preview model
//          to the cad-templates bucket. See supabase/templates.sql + templates-library.sql.

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

export async function GET() {
  if (!CONFIGURED) return Response.json({ templates: [] });
  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  if (!claims?.claims?.sub) return Response.json({ error: "not authenticated" }, { status: 401 });

  const { data, error } = await supabase.rpc("list_templates");
  if (error) {
    console.error("list_templates failed:", error);
    return Response.json({ error: "list failed" }, { status: 500 });
  }
  return Response.json({ templates: data ?? [] });
}

export async function POST(req) {
  if (!CONFIGURED) return Response.json({ error: "not configured" }, { status: 400 });

  const { title, description, steps, verified, visibility, model } = await req.json().catch(() => ({}));
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

  // Generate the id up front so we can name the model blob before the row exists.
  const id = crypto.randomUUID();

  // Best-effort: upload the captured 3D preview model (base64 pickle snapshot, no
  // steps) to cad-templates/<id> via the service role. On any failure we just store
  // the template without a preview (has_model stays false).
  let hasModel = false;
  const service = createServiceClient();
  if (model && service) {
    try {
      const bytes = Buffer.from(model, "base64");
      const { error: upErr } = await service.storage
        .from(TEMPLATE_BUCKET)
        .upload(id, bytes, { upsert: true, contentType: "application/octet-stream" });
      if (upErr) console.error("template model upload failed:", upErr);
      else hasModel = true;
    } catch (e) {
      console.error("template model upload threw:", e);
    }
  }

  // Insert only (no .select()): the templates table has no RLS select policy, so
  // reads go solely through list_templates()/match_templates(). RLS enforces
  // user_id = auth.uid(). Only public templates enter the review queue.
  const { error } = await supabase.from("templates").insert({
    id,
    user_id: uid,
    title: t,
    description: d,
    steps,
    verified: verified === true,
    visibility: vis,
    review_status: vis === "public" ? "pending" : null,
    embedding,
    // Record the text this embedding was made from, so a later re-embed pass skips
    // this row until its title/description is actually edited.
    content_hash: contentHash(t, d),
    has_model: hasModel,
  });
  if (error) {
    console.error("template insert failed:", error);
    // Don't leave an orphaned model blob behind if the row didn't land.
    if (hasModel && service) service.storage.from(TEMPLATE_BUCKET).remove([id]).catch(() => {});
    return Response.json({ error: "store failed" }, { status: 500 });
  }

  return Response.json({ ok: true, id });
}
