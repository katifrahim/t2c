import { createClient } from "@/lib/supabase/server";
import { generateKeyBetween } from "fractional-indexing";

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

// GET /api/threads/[id]/messages — the chat's messages in order (for load()).
// RLS scopes to the owner.
export async function GET(_req, { params }) {
  if (!CONFIGURED) return Response.json([]);
  const { id } = await params;
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("messages")
    .select("id,parent_id,format,content")
    .eq("chat_id", id)
    .order("created_at", { ascending: true });
  if (error) return Response.json([]);
  return Response.json(data ?? []);
}

// POST /api/threads/[id]/messages — append/upsert one message (from append()).
export async function POST(req, { params }) {
  if (!CONFIGURED) return Response.json({ ok: true });
  const { id: chatId } = await params;
  const supabase = await createClient();
  const { id, parent_id, format, content, role } = await req.json();

  // Safety net: ensure the chat row exists (normally created by initialize()).
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub;
  if (uid) {
    await supabase
      .from("chats")
      .upsert({ id: chatId, user_id: uid }, { onConflict: "id", ignoreDuplicates: true });
  }

  const { error } = await supabase
    .from("messages")
    .upsert({ id, chat_id: chatId, parent_id: parent_id ?? null, format, content });
  if (error) return Response.json({ error: error.message }, { status: 500 });

  // A new user prompt is a significant interaction → float the chat to the top of
  // the sidebar (recency wins over the manual order), by regenerating its position
  // key to sort before every other chat. Assistant/tool messages and renames don't.
  const patch = { updated_at: new Date().toISOString() };
  if (role === "user") {
    const { data: top } = await supabase
      .from("chats")
      .select("position")
      .neq("id", chatId)
      .order("position", { ascending: true })
      .limit(1)
      .maybeSingle();
    patch.position = generateKeyBetween(null, top?.position ?? null);
  }
  await supabase.from("chats").update(patch).eq("id", chatId);
  return Response.json({ ok: true });
}
