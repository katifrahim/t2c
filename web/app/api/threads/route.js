import { createClient } from "@/lib/supabase/server";
import { generateKeyBetween } from "fractional-indexing";

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

// GET /api/threads — the signed-in user's chats in their saved manual order.
// RLS scopes the query to the user, so no explicit auth round-trip is needed.
export async function GET() {
  if (!CONFIGURED) return Response.json([]);
  const supabase = await createClient();
  const { data, error } = await supabase
    .from("chats")
    .select("id,title,position")
    .order("position", { ascending: true });
  if (error) return Response.json([]); // e.g. no session / table missing → empty
  return Response.json(data ?? []);
}

// POST /api/threads — create a chat row (called by the thread-list adapter's
// initialize). Its uuid becomes the thread's remoteId. New chats go to the top,
// so their fractional key sorts before the current smallest one.
export async function POST() {
  if (!CONFIGURED) return Response.json({ id: null });
  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub;
  if (!uid) return Response.json({ error: "unauthorized" }, { status: 401 });

  const { data: top } = await supabase
    .from("chats")
    .select("position")
    .order("position", { ascending: true })
    .limit(1)
    .maybeSingle();
  const position = generateKeyBetween(null, top?.position ?? null);

  const { data, error } = await supabase
    .from("chats")
    .insert({ user_id: uid, position })
    .select("id")
    .single();
  if (error) return Response.json({ error: error.message }, { status: 500 });
  return Response.json({ id: data.id });
}
