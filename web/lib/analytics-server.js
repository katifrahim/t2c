// Server-side PostHog for authoritative product events (generation outcomes,
// cost/tokens, exports). Uses @posthog/next's request-scoped client, which
// resolves the distinct id from our Supabase session (not the spoofable client
// cookie) and keeps the browser session/device linkage so these events line up
// with the user's session replay. No-ops when PostHog isn't configured.
import { createPostHog } from "@posthog/next";
import { createClient } from "@/lib/supabase/server";

const ENABLED = !!process.env.NEXT_PUBLIC_POSTHOG_KEY;

export const { getPostHog } = createPostHog({
  options: { host: process.env.NEXT_PUBLIC_POSTHOG_HOST },
  // Attribute server events to the authenticated user; nullish → client identity.
  getDistinctId: async () => {
    if (!process.env.NEXT_PUBLIC_SUPABASE_URL) return undefined;
    try {
      const supabase = await createClient();
      const { data } = await supabase.auth.getClaims();
      return data?.claims?.sub ?? undefined;
    } catch {
      return undefined;
    }
  },
});

export async function captureServer(event, properties) {
  if (!ENABLED) return;
  try {
    const ph = await getPostHog();
    ph.capture({ event, properties });
  } catch (e) {
    console.error("posthog capture failed:", e);
  }
}

// Flush before the serverless instance can suspend — after() / waitUntil isn't
// guaranteed on every path (early returns, Hobby plan). Mirrors the Langfuse
// forceFlush already done at turn end.
export async function flushServerAnalytics() {
  if (!ENABLED) return;
  try {
    const ph = await getPostHog();
    await ph.flush();
  } catch {
    /* best-effort */
  }
}
