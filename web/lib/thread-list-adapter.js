"use client";
import { useCallback, useMemo } from "react";
import { RuntimeAdapterProvider, useAui } from "@assistant-ui/react";
import { createAssistantStream } from "assistant-stream";
import { makeHistoryAdapter } from "@/lib/history-adapter";

// RemoteThreadListAdapter backed by Supabase (via /api/threads*). This is what
// gives each thread a persistent remoteId (the Supabase chat id) so the history
// adapter loads its messages on reopen — and it lets the thread list survive
// reloads. Modeled on assistant-ui's useCloudThreadListAdapter.
export function useSupabaseThreadListAdapter() {
  // Injects a Supabase-backed history adapter around each active thread.
  const unstable_Provider = useCallback(function Provider({ children }) {
    const aui = useAui();
    const history = useMemo(() => makeHistoryAdapter(aui), [aui]);
    return <RuntimeAdapterProvider adapters={{ history }}>{children}</RuntimeAdapterProvider>;
  }, []);

  return useMemo(
    () => ({
      async list() {
        const rows = await fetch("/api/threads").then((r) => r.json()).catch(() => []);
        return {
          threads: (Array.isArray(rows) ? rows : []).map((t) => ({
            status: "regular",
            remoteId: t.id,
            title: t.title ?? undefined,
          })),
        };
      },
      // Create the chat row; its DB-generated uuid becomes the thread's remoteId
      // (== backend session id == viewer poll key == Phase 5 snapshot key).
      async initialize() {
        const res = await fetch("/api/threads", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: "{}",
        })
          .then((r) => r.json())
          .catch(() => ({}));
        return { remoteId: res.id };
      },
      async rename(remoteId, title) {
        await fetch(`/api/threads/${remoteId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title }),
        }).catch(() => {});
      },
      async archive() {},
      async unarchive() {},
      async delete(remoteId) {
        await fetch(`/api/threads/${remoteId}`, { method: "DELETE" }).catch(() => {});
      },
      // Title = first user message. Persist it (so the sidebar shows it after a
      // reload) and stream it back for immediate display.
      async generateTitle(remoteId, messages) {
        const title = firstUserText(messages).slice(0, 80);
        if (title) {
          fetch(`/api/threads/${remoteId}`, {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ title }),
          }).catch(() => {});
        }
        return createAssistantStream(async (controller) => {
          if (title) controller.appendText(title);
        });
      },
      async fetch(remoteId) {
        const rows = await fetch("/api/threads").then((r) => r.json()).catch(() => []);
        const t = (Array.isArray(rows) ? rows : []).find((x) => x.id === remoteId);
        return { status: "regular", remoteId, title: t?.title ?? undefined };
      },
      unstable_Provider,
    }),
    [unstable_Provider],
  );
}

function firstUserText(messages) {
  const u = messages.find((m) => m.role === "user");
  if (!u) return "";
  const parts = u.content || [];
  return parts
    .filter((p) => p.type === "text" && p.text)
    .map((p) => p.text)
    .join(" ")
    .trim();
}
