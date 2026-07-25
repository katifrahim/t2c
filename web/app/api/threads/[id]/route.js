import { createClient } from "@/lib/supabase/server";

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

// PATCH /api/threads/[id] — rename. Body: { title }. RLS scopes to the owner.
export async function PATCH(req, { params }) {
  if (!CONFIGURED) return Response.json({ ok: true });
  const { id } = await params;
  const supabase = await createClient();
  const { title } = await req.json().catch(() => ({}));
  const { error } = await supabase
    .from("chats")
    .update({ title, updated_at: new Date().toISOString() })
    .eq("id", id);
  if (error) return Response.json({ error: error.message }, { status: 500 });
  return Response.json({ ok: true });
}

// DELETE /api/threads/[id] — delete a chat (messages cascade).
export async function DELETE(_req, { params }) {
  if (!CONFIGURED) return Response.json({ ok: true });
  const { id } = await params;
  const supabase = await createClient();
  const { error } = await supabase.from("chats").delete().eq("id", id);
  if (error) return Response.json({ error: error.message }, { status: 500 });
  return Response.json({ ok: true });
}
