import { create } from "zustand";

// Light/dark background of the 3D viewer scene (three-cad-viewer's own theme).
// Shared by the chat top bar (toggle button) and the Viewer, which live in
// separate trees.
export const useViewerThemeStore = create((set) => ({
  theme: "light",
  // Init-only (restore from storage / OS default); does NOT persist. Use
  // toggleTheme for user actions so the choice is written to localStorage.
  setTheme: (theme) => set({ theme }),
  toggleTheme: () =>
    set((s) => {
      const theme = s.theme === "dark" ? "light" : "dark";
      try { localStorage.setItem("tcv-theme", theme); } catch {}
      return { theme };
    }),
}));
