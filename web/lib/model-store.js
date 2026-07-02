import { create } from "zustand";
import { DEFAULT_MODEL } from "@/lib/models";

// Selected LLM + the model the free auto-router actually resolved to. Shared by
// the top bar (select) and the per-thread runtime hook (transport body + onFinish).
export const useModelStore = create((set) => ({
  model: DEFAULT_MODEL,
  resolvedModel: null,
  setModel: (model) => set({ model, resolvedModel: null }),
  setResolvedModel: (resolvedModel) => set({ resolvedModel }),
}));
