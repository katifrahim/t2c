import { create } from "zustand";

// Client-side chat-sidebar order, mirroring the server's persisted `position`.
// It lives outside the history panel so it survives the panel opening/closing
// (no reset → no refetch flash). Optimistic: a new prompt floats its chat to the
// top immediately, matching the server-side position bump, so reopening the panel
// shows the correct order with no fetch round-trip.
export const useThreadOrderStore = create((set) => ({
  order: null, // array of remoteIds, or null before the first sync

  // Reconcile against the runtime's thread list: seed on first run, add new chats
  // at the top, drop deleted ones, and preserve the current order (bumps/drags)
  // for everything else.
  sync: (threadIds) =>
    set((s) => {
      if (!s.order) return { order: threadIds };
      const known = new Set(s.order);
      const additions = threadIds.filter((id) => !known.has(id));
      const runtime = new Set(threadIds);
      const survivors = s.order.filter((id) => runtime.has(id));
      return { order: [...additions, ...survivors] };
    }),

  setOrder: (order) => set({ order }),

  // Float a chat to the top — a new user prompt wins recency over the manual
  // order. No-op until the store is seeded (the server bump + reload cover that
  // edge); otherwise a lone id here would be re-sorted to the bottom by sync().
  bumpToTop: (id) =>
    set((s) => (s.order ? { order: [id, ...s.order.filter((x) => x !== id)] } : {})),
}));
