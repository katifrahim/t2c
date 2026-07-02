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
import { useSessionStore } from "@/lib/session-store";

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
  const setResolvedModel = useModelStore((s) => s.setResolvedModel);

  const transport = useDynamicTransport(
    useMemo(() => new AssistantChatTransport({ api: "/api/chat", body: { model } }), [model]),
  );

  const id = useAuiState((s) => s.threadListItem.id);
  const aui = useAui();
  const chat = useChat({ id, transport });

  const runtime = useAISDKRuntime(chat, {
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithToolCalls,
    onFinish: ({ message }) => {
      const served = message?.metadata?.model;
      if (served) setResolvedModel(served);
    },
  });

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
    setSessionId(remoteId ?? localId);
    // Opening a chat with a persistent id → restore its saved CAD objects into the
    // backend session (once per id). The backend no-ops if they're already loaded.
    if (remoteId && !restored.current.has(remoteId)) {
      restored.current.add(remoteId);
      fetch(`/api/session/load?session=${remoteId}`, { method: "POST" }).catch(() => {});
    }
  }, [remoteId, localId, setSessionId]);
  return null;
}

export default function ChatProvider({ children }) {
  const adapter = useSupabaseThreadListAdapter();
  const runtime = useRemoteThreadListRuntime({ runtimeHook: useThreadRuntime, adapter });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <SessionSync />
      {children}
    </AssistantRuntimeProvider>
  );
}
