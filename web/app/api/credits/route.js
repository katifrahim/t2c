import { createClient } from "@/lib/supabase/server";

// Current user's remaining credit balance (RLS scopes to the signed-in user).
// Returns { credits: number | null }; null when signed out / not configured.
export async function GET() {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL) return Response.json({ credits: null });
  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub;
  if (!uid) return Response.json({ credits: null });
  const { data } = await supabase
    .from("user_credits")
    .select("credits_remaining")
    .eq("user_id", uid)
    .maybeSingle();
  return Response.json({ credits: data?.credits_remaining ?? null });
}
