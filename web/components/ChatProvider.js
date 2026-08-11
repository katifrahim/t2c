"use client";
import { useEffect, useMemo, useRef } from "react";
import {
  AssistantRuntimeProvider,
  useRemoteThreadListRuntime,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { useAISDKRuntime, AssistantChatTransport } from "@assistant-ui/react-ai-sdk";
import { useChat } from "@ai-sdk/react";
import { lastAssistantMessageIsCompleteWithToolCalls } from "ai";
import { useSupabaseThreadListAdapter } from "@/lib/thread-list-adapter";
import { useModelStore } from "@/lib/model-store";
import { useCreditStore } from "@/lib/credit-store";
import { useSessionStore } from "@/lib/session-store";
import { useThreadOrderStore } from "@/lib/thread-order-store";

// Keep a stable transport reference while its config (model) changes underneath,
// mirroring assistant-ui's internal useChatThreadRuntime.
function useDynamicTransport(transport) {
  const ref = useRef(transport);
  useEffect(() => {
    ref.current = transport;
  });
  return useMemo(
    () =>
      new Proxy(ref.current, {
        get(_t, prop) {
          const res = ref.current[prop];
          return typeof res === "function" ? res.bind(ref.current) : res;
        },
      }),
    [],
  );
}

// Per-thread runtime: an AI SDK chat wired to /api/chat, with the transport glue
// that auto-injects the thread's remoteId as `id` in the request body.
function useThreadRuntime() {
  const model = useModelStore((s) => s.model);

  const transport = useDynamicTransport(
    useMemo(() => new AssistantChatTransport({ api: "/api/chat", body: { model } }), [model]),
  );

  const id = useAuiState((s) => s.threadListItem.id);
  const aui = useAui();

  const refreshCredits = () => {
    // A turn just settled (success or stop/error) — the server bills as the stream
    // closes, so refresh shortly after to catch the new balance; the 5s poll backs it up.
    setTimeout(() => useCreditStore.getState().refresh(), 1500);
  };
  // These callbacks MUST live on useChat: useAISDKRuntime silently drops
  // sendAutomaticallyWhen / onFinish / onError (they aren't in its option set), which
  // is why auto-continue never fired and post-turn credit refresh never ran.
  const chat = useChat({
    id,
    transport,
    // Auto-continue when a turn ends mid-task (tool calls resolved, no final answer
    // yet) so long builds finish without the user typing "continue".
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithToolCalls,
    onFinish: refreshCredits,
    onError: refreshCredits,
  });

  const runtime = useAISDKRuntime(chat);

  if (transport instanceof AssistantChatTransport) {
    transport.setRuntime(runtime);
    transport.__internal_setGetThreadListItem(() =>
      aui.threadListItem.source ? aui.threadListItem() : undefined,
    );
  }
  return runtime;
}

// Mirror the active thread's session id (remoteId once it exists, else the local
// thread id) into the shared store so the 3D Viewer polls the matching backend
// session. A brand-new chat has no remoteId yet → its local id → empty session →
// blank viewer, which is what we want.
function SessionSync() {
  const remoteId = useAuiState((s) => s.threadListItem.remoteId);
  const localId = useAuiState((s) => s.threadListItem.id);
  const setSessionId = useSessionStore((s) => s.setSessionId);
  const restored = useRef(new Set());
  useEffect(() => {
    // The id the backend has been using so far (what an import/build before the first
    // message wrote to) — capture it BEFORE we switch to the new chat id.
    const prev = useSessionStore.getState().sessionId;
    setSessionId(remoteId ?? localId);
    if (remoteId && !restored.current.has(remoteId)) {
      restored.current.add(remoteId);
      // First message just promoted this chat: move any CAD state built/imported under the
      // local id onto the persistent chat id (so an imported model isn't orphaned), THEN
      // restore any saved snapshot (reopened chats). The backend no-ops when nothing applies.
      const adopt =
        prev && prev !== remoteId
          ? fetch(`/api/session/adopt?from=${encodeURIComponent(prev)}&to=${encodeURIComponent(remoteId)}`, {
              method: "POST",
            }).catch(() => {})
          : Promise.resolve();
      adopt.finally(() =>
        fetch(`/api/session/load?session=${remoteId}`, { method: "POST" }).catch(() => {}),
      );
    }
  }, [remoteId, localId, setSessionId]);
  return null;
}

// Keep the sidebar order store reconciled with the runtime's thread list. Lives
// here (always mounted) rather than in the panel, so optimistic prompt-bumps land
// even while the panel is closed and survive it being reopened.
function ThreadOrderSync() {
  const threadIds = useAuiState((s) => s.threads.threadIds);
  const sync = useThreadOrderStore((s) => s.sync);
  useEffect(() => {
    sync(threadIds);
  }, [threadIds, sync]);
  return null;
}

export default function ChatProvider({ children }) {
  const adapter = useSupabaseThreadListAdapter();
  const runtime = useRemoteThreadListRuntime({ runtimeHook: useThreadRuntime, adapter });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <SessionSync />
      <ThreadOrderSync />
      {children}
    </AssistantRuntimeProvider>
  );
}
