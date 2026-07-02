"use client";
import { useEffect, useRef, useState } from "react";
import {
  ThreadListPrimitive,
  ThreadListItemPrimitive,
  useAssistantRuntime,
  useAui,
  useAuiState,
} from "@assistant-ui/react";
import { MenuIcon, PlusIcon, DownloadIcon, LogOutIcon, Trash2Icon } from "lucide-react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Thread } from "@/components/assistant-ui/thread";
import ChatProvider from "@/components/ChatProvider";
import { MODELS } from "@/lib/models";
import { useModelStore } from "@/lib/model-store";
import { useSessionStore } from "@/lib/session-store";
import { createClient, SUPABASE_CONFIGURED } from "@/lib/supabase/client";

const AUTO_MODEL = "openrouter/free";

const EXPORT_FORMATS = [
  { fmt: "stl", label: "STL" },
  { fmt: "3mf", label: "3MF" },
  { fmt: "step", label: "STEP" },
  { fmt: "amf", label: "AMF" },
  { fmt: "brep", label: "BREP" },
];
const SKETCH_FORMATS = [
  { fmt: "dxf", label: "DXF" },
  { fmt: "svg", label: "SVG" },
  { fmt: "step", label: "STEP" },
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

function TopBar({ onToggleHistory, historyOpen }) {
  const model = useModelStore((s) => s.model);
  const setModel = useModelStore((s) => s.setModel);
  const resolvedModel = useModelStore((s) => s.resolvedModel);
  const sessionId = useSessionStore((s) => s.sessionId);
  const runtime = useAssistantRuntime();
  const aui = useAui();

  const [menuOpen, setMenuOpen] = useState(false);
  const [isSketch, setIsSketch] = useState(false);

  function newChat() {
    try {
      runtime.switchToNewThread();
    } catch {
      try { aui.threads().switchToNewThread(); } catch { /* ignore */ }
    }
  }

  async function openDownloadMenu() {
    try {
      const { obj_type } = await (await fetch(`/api/version?session=${sessionId}`)).json();
      setIsSketch(obj_type === "Sketch");
    } catch {
      setIsSketch(false);
    }
    setMenuOpen((o) => !o);
  }

  function download(fmt) {
    setMenuOpen(false);
    const a = document.createElement("a");
    a.href = `/api/export?fmt=${fmt}&session=${sessionId}`;
    a.download = `model.${fmt}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }

  async function signOut() {
    await createClient().auth.signOut();
    window.location.href = "/login";
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid #eee", background: "#fff" }}>
      <button
        type="button"
        onClick={onToggleHistory}
        title="Chat history"
        aria-label="Chat history"
        style={{ ...iconBtnStyle, ...(historyOpen ? { background: "#f1f1f1" } : {}) }}
      >
        <MenuIcon size={16} />
      </button>

      <select
        value={model}
        onChange={(e) => setModel(e.target.value)}
        style={{ flex: 1, minWidth: 0, fontSize: 13, padding: "5px 8px", border: "1px solid #e0e0e0", borderRadius: 6, background: "#fff", color: "#333", cursor: "pointer" }}
      >
        {MODELS.map((m) => {
          const label = m.id === AUTO_MODEL && resolvedModel ? `${m.label} → ${resolvedModel}` : m.label;
          return <option key={m.id} value={m.id}>{label}</option>;
        })}
      </select>

      <button type="button" onClick={newChat} title="New chat" aria-label="New chat" style={iconBtnStyle}>
        <PlusIcon size={16} />
      </button>

      <div style={{ position: "relative" }}>
        <button type="button" onClick={openDownloadMenu} title="Download model" aria-label="Download model" style={iconBtnStyle}>
          <DownloadIcon size={16} />
        </button>
        {menuOpen && (
          <>
            <div onClick={() => setMenuOpen(false)} style={{ position: "fixed", inset: 0, zIndex: 10 }} />
            <div style={{ position: "absolute", right: 0, top: 36, zIndex: 20, background: "#fff", border: "1px solid #e5e5e5", borderRadius: 8, boxShadow: "0 6px 24px -8px rgba(0,0,0,0.18)", padding: 4, minWidth: 120 }}>
              {(isSketch ? SKETCH_FORMATS : EXPORT_FORMATS).map((f) => (
                <button
                  key={f.fmt}
                  type="button"
                  onClick={() => download(f.fmt)}
                  style={{ display: "block", width: "100%", textAlign: "left", padding: "6px 10px", fontSize: 13, border: "none", background: "transparent", cursor: "pointer", borderRadius: 6 }}
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

      {SUPABASE_CONFIGURED && (
        <button type="button" onClick={signOut} title="Sign out" aria-label="Sign out" style={iconBtnStyle}>
          <LogOutIcon size={16} />
        </button>
      )}
    </div>
  );
}

function ThreadListItem() {
  return (
    <ThreadListItemPrimitive.Root
      style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 8px", borderBottom: "1px solid #f2f2f2" }}
    >
      <ThreadListItemPrimitive.Trigger
        style={{ flex: 1, minWidth: 0, textAlign: "left", padding: "8px 6px", border: "none", background: "transparent", cursor: "pointer", fontSize: 13, color: "#222", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
      >
        <ThreadListItemPrimitive.Title fallback="New chat" />
      </ThreadListItemPrimitive.Trigger>
      <ThreadListItemPrimitive.Delete
        title="Delete chat"
        aria-label="Delete chat"
        style={{ border: "none", background: "none", cursor: "pointer", color: "#bbb", padding: 4, display: "inline-flex" }}
      >
        <Trash2Icon size={15} />
      </ThreadListItemPrimitive.Delete>
    </ThreadListItemPrimitive.Root>
  );
}

function ThreadListPanel() {
  return (
    <ThreadListPrimitive.Root style={{ flex: 1, minHeight: 0, overflowY: "auto", padding: "4px 0" }}>
      <ThreadListPrimitive.Items components={{ ThreadListItem }} />
    </ThreadListPrimitive.Root>
  );
}

function ChatInner() {
  const [showHistory, setShowHistory] = useState(false);

  // Close the history panel whenever the active thread changes (selecting a past
  // chat or starting a new one), so the conversation comes to the front.
  const activeId = useAuiState((s) => s.threadListItem.id);
  const mounted = useRef(false);
  useEffect(() => {
    if (mounted.current) setShowHistory(false);
    else mounted.current = true;
  }, [activeId]);

  // Nudge react-textarea-autosize so the empty-state composer measures to one line.
  useEffect(() => {
    const fire = () => window.dispatchEvent(new Event("resize"));
    const r = requestAnimationFrame(fire);
    const t = setTimeout(fire, 250);
    return () => { cancelAnimationFrame(r); clearTimeout(t); };
  }, [showHistory]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: "#fff" }}>
      <TopBar historyOpen={showHistory} onToggleHistory={() => setShowHistory((v) => !v)} />
      <div style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
        {showHistory ? <ThreadListPanel /> : <div style={{ height: "100%" }}><Thread /></div>}
      </div>
    </div>
  );
}

export default function Chat() {
  return (
    <TooltipProvider>
      <ChatProvider>
        <ChatInner />
      </ChatProvider>
    </TooltipProvider>
  );
}
