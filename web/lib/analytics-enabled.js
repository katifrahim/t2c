// Single switch for whether analytics actually capture. Events/replays run ONLY
// in real production (actual users), so local dev and preview deploys don't burn
// PostHog's per-organization free-tier quota. Set NEXT_PUBLIC_ANALYTICS_DEV=true
// in .env.local to force-enable capture locally while testing analytics.
// Plain module (no "use client") so it's importable on client and server.
//
// Best practice: run two PostHog projects in one org — a prod project (this key on
// Vercel, always capturing) and a separate dev/testing project (its key in
// .env.local, off by default, enabled only when NEXT_PUBLIC_ANALYTICS_DEV=true) —
// so dev noise never pollutes production data.
export const ANALYTICS_ENABLED =
  !!process.env.NEXT_PUBLIC_POSTHOG_KEY &&
  (process.env.NEXT_PUBLIC_VERCEL_ENV === "production" ||
    process.env.NEXT_PUBLIC_ANALYTICS_DEV === "true");
