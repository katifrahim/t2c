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
}));
