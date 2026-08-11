import { create } from "zustand";

// A CAD file the user just imported (STEP), described by the backend parser. The composer
// reads `pending` and prefixes the next prompt with the model's structured description — so
// the AI knows the imported model's exact geometry (a far richer replacement for a 2D
// drawing) and can edit it. Cleared once injected. Mirrors the selection-store pattern.
// pending: { name, filename, converted, description } | null
export const useImportStore = create((set) => ({
  pending: null,
  setPending: (pending) => set({ pending }),
  clear: () => set({ pending: null }),
}));
