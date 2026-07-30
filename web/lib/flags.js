"use client";
// Feature flags + A/B experiments via PostHog. Reading a flag with this hook also
// records the exposure event PostHog needs to attribute experiment results, and
// bootstrapFlags (see app/layout.js) makes the value available on first render so
// there's no control→variant flash. No-ops to the fallback when PostHog is off or
// the flag doesn't exist yet.
import { useFeatureFlag } from "@posthog/next";

export { useFeatureFlag };

// The active variant key of an A/B experiment flag (e.g. "control" | "test"),
// or `fallback` until flags resolve / when the flag isn't defined.
export function useVariant(key, fallback = "control") {
  const result = useFeatureFlag(key);
  return result?.variant ?? fallback;
}
