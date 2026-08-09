import { embedText } from "@/lib/embeddings";
import { contentHash } from "@/lib/reembed";
import { createClient } from "@/lib/supabase/server";
import { createServiceClient, TEMPLATE_BUCKET } from "@/lib/supabase/service";

// /api/templates/[id]
//   PATCH  — edit the title/description of your OWN PRIVATE template. Re-embeds
//            automatically (either field changing alters the embedded text), so the
//            RAG vector stays in sync with no terminal command.
//   DELETE — delete your OWN PRIVATE template (row + its 3D preview blob).
// Public templates are view-only for everyone (managed directly in Supabase), so both
// routes reject anything that isn't the caller's own private template.

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

// Resolve the caller + the target row (via service role, since the table has no RLS
// select policy) and enforce "own private template". Returns { error } on any failure.
async function guardOwnPrivate(id) {
  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub ?? null;
  if (!uid) return { error: Response.json({ error: "not authenticated" }, { status: 401 }) };

  const service = createServiceClient();
  if (!service) return { error: Response.json({ error: "not configured" }, { status: 503 }) };

  const { data: row, error } = await service
    .from("templates")
    .select("user_id, visibility")
    .eq("id", id)
    .maybeSingle();
  if (error) return { error: Response.json({ error: "load failed" }, { status: 500 }) };
  if (!row || row.user_id !== uid)
    return { error: Response.json({ error: "not found" }, { status: 404 }) };
  if (row.visibility !== "private")
    return { error: Response.json({ error: "public templates are view-only" }, { status: 403 }) };

  return { service };
}

export async function PATCH(req, { params }) {
  if (!CONFIGURED) return Response.json({ error: "not configured" }, { status: 400 });
  const { id } = await params;

  const { title, description } = await req.json().catch(() => ({}));
  const t = (title ?? "").trim();
  const d = (description ?? "").trim();
  if (!t || !d) return Response.json({ error: "title and description required" }, { status: 400 });

  const { error: guard, service } = await guardOwnPrivate(id);
  if (guard) return guard;

  // Re-embed the new prose. content_hash changes whenever EITHER field changes, so
  // this keeps retrieval in sync with the edit.
  let embedding;
  try {
    embedding = await embedText(`${t}. ${d}`, "RETRIEVAL_DOCUMENT");
  } catch (e) {
    console.error("template re-embed failed:", e);
    return Response.json({ error: "embed failed" }, { status: 502 });
  }

  const { error } = await service
    .from("templates")
    .update({
      title: t,
      description: d,
      embedding,
      content_hash: contentHash(t, d),
      updated_at: new Date().toISOString(),
    })
    .eq("id", id);
  if (error) {
    console.error("template update failed:", error);
    return Response.json({ error: "update failed" }, { status: 500 });
  }
  return Response.json({ ok: true });
}

export async function DELETE(_req, { params }) {
  if (!CONFIGURED) return Response.json({ error: "not configured" }, { status: 400 });
  const { id } = await params;

  const { error: guard, service } = await guardOwnPrivate(id);
  if (guard) return guard;

  // Remove the preview blob first (best-effort); the row delete cascades preferences.
  service.storage.from(TEMPLATE_BUCKET).remove([id]).catch(() => {});

  const { error } = await service.from("templates").delete().eq("id", id);
  if (error) {
    console.error("template delete failed:", error);
    return Response.json({ error: "delete failed" }, { status: 500 });
  }
  return Response.json({ ok: true });
}
