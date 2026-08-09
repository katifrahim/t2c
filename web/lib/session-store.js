import { create } from "zustand";

// The active chat's id — used as (a) the backend session id (X-Session-Id →
// per-chat CAD state), (b) the Viewer poll key, and (c) the Supabase chat row id
// so a reopened chat restores its own history + (Phase 5) saved models.
export function newChatId() {
  return crypto?.randomUUID?.() ?? `s-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

export const useSessionStore = create((set) => ({
  sessionId: newChatId(),
  // Open an existing chat (persisted id) or set the active session explicitly.
  setSessionId: (id) => set({ sessionId: id }),
  // Start a brand-new chat (fresh backend session; row is created lazily on the
  // first saved message).
  newSession: () => set({ sessionId: newChatId() }),

  // When set, the Viewer polls this backend session instead of sessionId — used by
  // the Template Library to preview a template's stored 3D model without disturbing
  // the live chat. Cleared to snap back to the active chat's model.
  previewSessionId: null,
  setPreview: (id) => set({ previewSessionId: id }),
  clearPreview: () => set({ previewSessionId: null }),
}));
