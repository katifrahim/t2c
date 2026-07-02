import { createBrowserClient } from "@supabase/ssr";

// Browser-side Supabase client (client components / event handlers).
export function createClient() {
  return createBrowserClient(
    process.env.NEXT_PUBLIC_SUPABASE_URL,
    process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY,
  );
}

// True once the project keys are configured; auth is a no-op until then so the
// app still runs during setup (see middleware + login page guards).
export const SUPABASE_CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
