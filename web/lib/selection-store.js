import { create } from "zustand";

// Features the user picked in the 3D viewer's "select" tool, resolved to
// geometric descriptions by the backend (/api/selection). The composer reads
// these and prefixes the next prompt with a "Selected geometry" block so the AI
// knows the exact vertices/edges/faces/solids the user is referring to.
// Each feature: { id, label, text }. Shared across the Viewer (writes) and the
// Chat composer (reads/clears), which live in separate React trees.
export const useSelectionStore = create((set) => ({
  features: [],
  setFeatures: (features) => set({ features: features || [] }),
  remove: (id) => set((s) => ({ features: s.features.filter((f) => f.id !== id) })),
  clear: () => set({ features: [] }),
}));
