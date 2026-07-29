"use client";
// Thin wrapper over posthog-js for product analytics. All app code should call
// these helpers (not posthog directly) so event names stay centralized and
// tracking no-ops safely when PostHog isn't configured (e.g. local dev).
import posthog from "posthog-js";

// Single source of truth for event names. Convention: category:object_action.
export const EVENTS = {
  GET_STARTED_CLICK: "landing:get_started_click",
  SIGN_UP_SUBMITTED: "auth:sign_up_submitted",
  SIGN_UP_SUCCEEDED: "auth:sign_up_succeeded",
  SIGN_IN_SUCCEEDED: "auth:sign_in_succeeded",
  OAUTH_CLICK: "auth:oauth_click",
  PROMPT_SUBMITTED: "chat:prompt_submitted",
  MODEL_SWITCHED: "chat:model_switched",
  NEW_CHAT: "chat:new_chat",
  EXPORT_CLICKED: "model:export_clicked",
  TEMPLATE_SAVED: "template:saved",
  // Emitted server-side (see lib/analytics-server.js):
  GENERATION_SUCCEEDED: "chat:generation_succeeded",
  GENERATION_FAILED: "chat:generation_failed",
  CREDITS_EXHAUSTED: "credits:exhausted",
  MODEL_EXPORTED: "model:exported",
};

export function track(event, props) {
  posthog?.capture?.(event, props);
}

// Tie the anonymous session to the authenticated user so their journey is
// stitched across devices. Safe to call repeatedly with the same id.
export function identify(userId, email) {
  if (!userId) return;
  posthog?.identify?.(userId, email ? { email } : undefined);
}

// Clear identity on sign-out so the next user isn't merged into this profile.
export function resetIdentity() {
  posthog?.reset?.();
}
