"use client";
// Thin wrapper over posthog-js for product analytics. All app code should call
// these helpers (not posthog directly) so event names stay centralized and
// tracking no-ops safely when PostHog isn't configured (e.g. local dev).
import posthog from "posthog-js";
import { ANALYTICS_ENABLED } from "./analytics-enabled";

// Event names live in a framework-agnostic module shared with the server.
export { EVENTS } from "./analytics-events";

export function track(event, props) {
  if (!ANALYTICS_ENABLED) return;
  posthog?.capture?.(event, props);
}

// Tie the anonymous session to the authenticated user so their journey is
// stitched across devices. Safe to call repeatedly with the same id.
export function identify(userId, email) {
  if (!ANALYTICS_ENABLED || !userId) return;
  posthog?.identify?.(userId, email ? { email } : undefined);
}

// Clear identity on sign-out so the next user isn't merged into this profile.
export function resetIdentity() {
  posthog?.reset?.();
}
