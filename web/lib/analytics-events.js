// Single source of truth for PostHog event names, shared by the client
// (lib/analytics.js) and server (lib/analytics-server.js) so neither drifts.
// Convention: category:object_action. No "use client" — safe to import anywhere.
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
  MODEL_IMPORTED: "model:imported",
  TEMPLATE_SAVED: "template:saved",
  // Emitted server-side (see lib/analytics-server.js):
  GENERATION_SUCCEEDED: "chat:generation_succeeded",
  GENERATION_FAILED: "chat:generation_failed",
  CREDITS_EXHAUSTED: "credits:exhausted",
  MODEL_EXPORTED: "model:exported",
};
