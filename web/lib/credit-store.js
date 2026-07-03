import { create } from "zustand";

// Shared credit balance shown in the top bar. Refreshed on mount and after each
// turn finishes (see ChatProvider). Users only ever see the credit number.
export const useCreditStore = create((set) => ({
  credits: null,
  refresh: async () => {
    try {
      const r = await fetch("/api/credits");
      const { credits } = await r.json();
      set({ credits });
    } catch {
      /* ignore — the chip just keeps its last value */
    }
  },
}));
