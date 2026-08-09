import { createClient } from "@/lib/supabase/server";

// POST /api/templates/[id]/toggle { enabled } — set whether THIS template is
// retrievable by the caller's RAG (per-user, so it never affects anyone else). We
// only store a row when the state differs from the default; upserting enabled=true is
// harmless. match_templates() excludes any template the caller has toggled off.

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

export async function POST(req, { params }) {
  if (!CONFIGURED) return Response.json({ error: "not configured" }, { status: 400 });
  const { id } = await params;
  const { enabled } = await req.json().catch(() => ({}));

  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub ?? null;
  if (!uid) return Response.json({ error: "not authenticated" }, { status: 401 });

  // RLS on template_preferences enforces user_id = auth.uid().
  const { error } = await supabase
    .from("template_preferences")
    .upsert(
      { user_id: uid, template_id: id, enabled: enabled !== false, updated_at: new Date().toISOString() },
      { onConflict: "user_id,template_id" },
    );
  if (error) {
    console.error("template toggle failed:", error);
    return Response.json({ error: "toggle failed" }, { status: 500 });
  }
  return Response.json({ ok: true });
}
