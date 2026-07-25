import { create } from "zustand";
import { DEFAULT_MODEL } from "@/lib/models";

// Selected LLM. Shared by the top bar (select) and the per-thread runtime hook
// (transport body).
export const useModelStore = create((set) => ({
  model: DEFAULT_MODEL,
  setModel: (model) => set({ model }),
}));
