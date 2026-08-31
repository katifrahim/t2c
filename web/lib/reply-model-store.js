import { create } from "zustand";

// Maps an assistant message id -> the model id that actually produced it. Captured in
// useChat.onFinish from the reply's raw AI-SDK metadata (api/chat stamps the resolved
// model there). assistant-ui drops unknown top-level metadata keys during its message
// conversion, so we stash the model here instead and read it back by the same message
// id. Powers the per-reply mode label in the thread (shows Text-to-CAD vs Image-to-CAD).
export const useReplyModelStore = create((set) => ({
  byId: {},
  set: (id, model) => set((s) => (s.byId[id] === model ? s : { byId: { ...s.byId, [id]: model } })),
}));
