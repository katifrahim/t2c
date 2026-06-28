"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  AssistantRuntimeProvider,
  useAssistantRuntime,
  useAui,
} from "@assistant-ui/react";
import {
  useChatRuntime,
  AssistantChatTransport,
} from "@assistant-ui/react-ai-sdk";
import { lastAssistantMessageIsCompleteWithToolCalls } from "ai";
import { RefreshCwIcon, DownloadIcon } from "lucide-react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Thread } from "@/components/assistant-ui/thread";
import { MODELS, DEFAULT_MODEL } from "@/lib/models";

const AUTO_MODEL = "openrouter/free";

const EXPORT_FORMATS = [
  { fmt: "stl", label: "STL" },
  { fmt: "3mf", label: "3MF" },
  { fmt: "step", label: "STEP" },
  { fmt: "amf", label: "AMF" },
  { fmt: "brep", label: "BREP" },
];

const iconBtnStyle = {
  display: "inline-flex",
  alignItems: "center",
  justifyContent: "center",
  width: 30,
  height: 30,
  borderRadius: 6,
  border: "1px solid #e5e5e5",
  background: "#fff",
  cursor: "pointer",
  color: "#333",
  flexShrink: 0,
};

// Toolbar lives inside AssistantRuntimeProvider so it can start a new thread.
function Toolbar({ model, setModel, resolvedModel }) {
  const runtime = useAssistantRuntime();
  const aui = useAui();
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  // New session: clear backend CadQuery objects (viewer resets to the
  // placeholder via its poll) AND start a fresh chat thread.
  async function handleReset() {
    setBusy(true);
    try {
      await fetch("/api/reset", { method: "POST" });
      try {
        runtime.switchToNewThread();
      } catch {
        try { aui.threads().switchToNewThread(); } catch { /* ignore */ }
      }
    } finally {
      setBusy(false);
    }
  }

  function download(fmt) {
    setMenuOpen(false);
    const a = document.createElement("a");
    a.href = `/api/export?fmt=${fmt}`;
    a.download = `model.${fmt}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        padding: "8px 12px",
        borderBottom: "1px solid #eee",
        background: "#fff",
      }}
    >
      <span style={{ fontSize: 13, color: "#666" }}>Model</span>
      <select
        value={model}
        onChange={(e) => setModel(e.target.value)}
        style={{
          flex: 1,
          minWidth: 0,
          fontSize: 13,
          padding: "5px 8px",
          border: "1px solid #e0e0e0",
          borderRadius: 6,
          background: "#fff",
          color: "#333",
          cursor: "pointer",
        }}
      >
        {MODELS.map((m) => {
          // For the auto router, once a turn finishes, reveal the model it
          // actually picked right in the dropdown label.
          const label =
            m.id === AUTO_MODEL && resolvedModel
              ? `${m.label} → ${resolvedModel}`
              : m.label;
          return (
            <option key={m.id} value={m.id}>
              {label}
            </option>
          );
        })}
      </select>

      <button
        type="button"
        onClick={handleReset}
        disabled={busy}
        title="New session (clear model & chat)"
        aria-label="New session"
        style={iconBtnStyle}
      >
        <RefreshCwIcon size={16} className={busy ? "animate-spin" : undefined} />
      </button>

      <div style={{ position: "relative" }}>
        <button
          type="button"
          onClick={() => setMenuOpen((o) => !o)}
          title="Download model"
          aria-label="Download model"
          style={iconBtnStyle}
        >
          <DownloadIcon size={16} />
        </button>
        {menuOpen && (
          <>
            <div
              onClick={() => setMenuOpen(false)}
              style={{ position: "fixed", inset: 0, zIndex: 10 }}
            />
            <div
              style={{
                position: "absolute",
                right: 0,
                top: 36,
                zIndex: 20,
                background: "#fff",
                border: "1px solid #e5e5e5",
                borderRadius: 8,
                boxShadow: "0 6px 24px -8px rgba(0,0,0,0.18)",
                padding: 4,
                minWidth: 120,
              }}
            >
              {EXPORT_FORMATS.map((f) => (
                <button
                  key={f.fmt}
                  type="button"
                  onClick={() => download(f.fmt)}
                  style={{
                    display: "block",
                    width: "100%",
                    textAlign: "left",
                    padding: "6px 10px",
                    fontSize: 13,
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    borderRadius: 6,
                  }}
                  onMouseEnter={(e) => (e.currentTarget.style.background = "#f3f3f3")}
                  onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                >
                  {f.label}
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function Chat() {
  const [model, setModel] = useState(DEFAULT_MODEL);
  // The model the auto free-router actually picked on the last turn.
  const [resolvedModel, setResolvedModel] = useState(null);

  // Latest selected model, readable inside the onFinish closure.
  const modelRef = useRef(model);
  modelRef.current = model;

  function selectModel(id) {
    setModel(id);
    setResolvedModel(null); // stale resolved label shouldn't carry across switches
  }

  // Recreate the transport when the model changes so the next request uses it;
  // the runtime keeps the existing thread (messages live in the runtime store).
  const transport = useMemo(
    () => new AssistantChatTransport({ api: "/api/chat", body: { model } }),
    [model],
  );

  const runtime = useChatRuntime({
    transport,
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithToolCalls,
    // The server attaches the model that answered (messageMetadata.model). When
    // the auto router is active, surface which model it resolved to.
    onFinish: ({ message }) => {
      if (modelRef.current !== AUTO_MODEL) return;
      const served = message?.metadata?.model;
      if (served) setResolvedModel(served);
    },
  });

  // The composer's react-textarea-autosize mis-measures its height on mount in
  // the centered empty state (renders tall, then snaps to one line on first
  // keystroke). It recomputes on window resize, so nudge one after layout
  // settles. Cross-browser (unlike CSS field-sizing).
  useEffect(() => {
    const fire = () => window.dispatchEvent(new Event("resize"));
    const r = requestAnimationFrame(fire);
    const t = setTimeout(fire, 250);
    return () => {
      cancelAnimationFrame(r);
      clearTimeout(t);
    };
  }, []);

  return (
    <TooltipProvider>
      <AssistantRuntimeProvider runtime={runtime}>
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            height: "100%",
            background: "#fff",
          }}
        >
          <Toolbar model={model} setModel={selectModel} resolvedModel={resolvedModel} />
          <div style={{ flex: 1, minHeight: 0 }}>
            <Thread />
          </div>
        </div>
      </AssistantRuntimeProvider>
    </TooltipProvider>
  );
}
