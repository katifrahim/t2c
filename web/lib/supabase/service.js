import { createClient as createSupabaseClient } from "@supabase/supabase-js";

// Service-role Supabase client for server routes that need to bypass RLS — used only
// for the cad-templates Storage bucket, whose blobs (per-template 3D preview models)
// aren't owner-path-scoped: a public template's model must be readable by everyone.
// The routes authorize the caller themselves (can_view_template / ownership) before
// touching a blob, so this key never reaches the browser. Same env the reembed script
// uses. Returns null when unconfigured so callers can degrade gracefully.
export function createServiceClient() {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SECRET_KEY;
  if (!url || !key) return null;
  return createSupabaseClient(url, key, { auth: { persistSession: false } });
}

export const TEMPLATE_BUCKET = "cad-templates";
