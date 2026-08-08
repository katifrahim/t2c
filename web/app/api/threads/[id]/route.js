import { createClient } from "@/lib/supabase/server";
import { generateKeyBetween } from "fractional-indexing";

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

// PATCH /api/threads/[id] — rename ({ title }) or reorder ({ above, below }).
// For a reorder the body carries the dropped chat's new neighbour ids (either
// null at a list end); the new fractional key is derived from their positions.
// RLS scopes every read/write to the owner.
export async function PATCH(req, { params }) {
  if (!CONFIGURED) return Response.json({ ok: true });
  const { id } = await params;
  const supabase = await createClient();
  const body = await req.json().catch(() => ({}));

  if (body.above !== undefined || body.below !== undefined) {
    const ids = [body.above, body.below].filter(Boolean);
    const { data: neighbours } = ids.length
      ? await supabase.from("chats").select("id,position").in("id", ids)
      : { data: [] };
    const posOf = (nid) => neighbours?.find((n) => n.id === nid)?.position ?? null;
    let position;
    try {
      position = generateKeyBetween(posOf(body.above), posOf(body.below));
    } catch {
      return Response.json({ error: "bad order" }, { status: 400 });
    }
    const { error } = await supabase.from("chats").update({ position }).eq("id", id);
    if (error) return Response.json({ error: error.message }, { status: 500 });
    return Response.json({ ok: true });
  }

  const { error } = await supabase
    .from("chats")
    .update({ title: body.title, updated_at: new Date().toISOString() })
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
  // The chat's CAD snapshot lives in Storage at "<uid>/<chatId>". Storage has no FK
  // to cascade on chat delete (unlike the legacy session_snapshots row), so remove
  // it here or it's orphaned forever. Best-effort — a cleanup miss (incl. a chat
  // that never had a snapshot) must never fail the delete.
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub;
  if (uid) {
    await supabase.storage.from("cad-snapshots").remove([`${uid}/${id}`]).catch(() => {});
  }
  return Response.json({ ok: true });
}
