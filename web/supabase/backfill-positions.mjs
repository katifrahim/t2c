// One-time backfill for chats.position (fractional-indexing keys).
// SQL can't generate valid fractional keys, so we assign them here, per user,
// preserving the previous "newest first" order (newest chat gets the smallest
// key → top). Safe to re-run: only rows with a null position are touched.
//
// Run once after applying the schema change:
//   node --env-file=.env.local supabase/backfill-positions.mjs
//
// Requires NEXT_PUBLIC_SUPABASE_URL and SUPABASE_SECRET_KEY (bypasses RLS).

import { createClient } from "@supabase/supabase-js";
import { generateNKeysBetween } from "fractional-indexing";

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SECRET_KEY;
if (!url || !key) {
  console.error("Missing NEXT_PUBLIC_SUPABASE_URL or SUPABASE_SECRET_KEY.");
  process.exit(1);
}

const supabase = createClient(url, key, { auth: { persistSession: false } });

const { data: rows, error } = await supabase
  .from("chats")
  .select("id,user_id,position,updated_at")
  .is("position", null)
  .order("updated_at", { ascending: false });
if (error) throw error;

// Group the position-less rows by user.
const byUser = new Map();
for (const r of rows) (byUser.get(r.user_id) ?? byUser.set(r.user_id, []).get(r.user_id)).push(r);

let updated = 0;
for (const [uid, userRows] of byUser) {
  // Anchor after any keys the user already has, so new keys sort below existing ones.
  const { data: existing } = await supabase
    .from("chats")
    .select("position")
    .eq("user_id", uid)
    .not("position", "is", null)
    .order("position", { ascending: false })
    .limit(1)
    .maybeSingle();
  const keys = generateNKeysBetween(existing?.position ?? null, null, userRows.length);
  // userRows is newest→oldest; keys ascend, so newest gets the smallest key (top).
  for (let i = 0; i < userRows.length; i++) {
    const { error: upErr } = await supabase
      .from("chats")
      .update({ position: keys[i] })
      .eq("id", userRows[i].id);
    if (upErr) throw upErr;
    updated++;
  }
}

console.log(`Backfilled position for ${updated} chat(s) across ${byUser.size} user(s).`);
