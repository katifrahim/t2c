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

// Session replay is disabled at init (see app/layout.js) so anonymous landing
// traffic never records and burns the free-tier quota. We start it explicitly
// only for authenticated users. Safe to call repeatedly — PostHog no-ops if a
// recording is already running.
export function startReplay() {
  if (!ANALYTICS_ENABLED) return;
  posthog?.startSessionRecording?.();
}

// Stop the current recording (called on sign-out via resetIdentity).
export function stopReplay() {
  posthog?.stopSessionRecording?.();
}

// Clear identity on sign-out so the next user isn't merged into this profile,
// and stop recording so a subsequent anonymous session isn't captured.
export function resetIdentity() {
  stopReplay();
  posthog?.reset?.();
}
