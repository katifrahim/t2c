"use client";
import { useEffect, useRef, useState } from "react";
import {
  ThreadListPrimitive,
  ThreadListItemPrimitive,
  useAssistantRuntime,
  useAui,
  useAuiState,
  useThreadListItem,
  useThreadListItemRuntime,
} from "@assistant-ui/react";
import { MenuIcon, PlusIcon, DownloadIcon, LogOutIcon, Trash2Icon, PencilIcon, MoonIcon, SunIcon } from "lucide-react";
import {
  TooltipProvider,
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from "@/components/ui/alert-dialog";
import { Thread } from "@/components/assistant-ui/thread";
import ChatProvider from "@/components/ChatProvider";
import { MODELS } from "@/lib/models";
import { useModelStore } from "@/lib/model-store";
import { useSessionStore } from "@/lib/session-store";
import { useViewerThemeStore } from "@/lib/viewer-theme-store";
import { createClient, SUPABASE_CONFIGURED } from "@/lib/supabase/client";

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

// Icon button for the top bar: instant tooltip (same primitive as the composer's
// + button) plus a grey hover, or a red hover for the `danger` (sign-out) button.
function TopBarButton({ tooltip, onClick, active, danger, children }) {
  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <button
            type="button"
            onClick={onClick}
            aria-label={tooltip}
            style={{ ...iconBtnStyle, ...(active ? { background: "#f1f1f1" } : {}) }}
            onMouseEnter={(e) => {
              const el = e.currentTarget;
              if (danger) {
                el.style.background = "#fef2f2";
                el.style.borderColor = "#fca5a5";
                el.style.color = "#dc2626";
              } else {
                el.style.background = "#f1f1f1";
              }
            }}
            onMouseLeave={(e) => {
              const el = e.currentTarget;
              el.style.background = active ? "#f1f1f1" : "#fff";
              el.style.borderColor = "#e5e5e5";
              el.style.color = "#333";
            }}
          />
        }
      >
        {children}
      </TooltipTrigger>
      <TooltipContent side="bottom" sideOffset={10}>{tooltip}</TooltipContent>
    </Tooltip>
  );
}

function TopBar({ onToggleHistory, historyOpen }) {
  const model = useModelStore((s) => s.model);
  const setModel = useModelStore((s) => s.setModel);
  const sessionId = useSessionStore((s) => s.sessionId);
  const viewerTheme = useViewerThemeStore((s) => s.theme);
  const toggleViewerTheme = useViewerThemeStore((s) => s.toggleTheme);
  const runtime = useAssistantRuntime();
  const aui = useAui();

  const [menuOpen, setMenuOpen] = useState(false);
  const [isSketch, setIsSketch] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);

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
    window.location.href = "/";
  }

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 12px", borderBottom: "1px solid #eee", background: "#fff" }}>
      <TopBarButton tooltip="Chat history" onClick={onToggleHistory} active={historyOpen}>
        <MenuIcon size={16} />
      </TopBarButton>

      <Tooltip>
        <TooltipTrigger
          render={
            <select
              value={model}
              onChange={(e) => setModel(e.target.value)}
              aria-label="Model"
              style={{ flex: 1, minWidth: 0, fontSize: 13, padding: "5px 8px", border: "1px solid #e0e0e0", borderRadius: 6, background: "#fff", color: "#333", cursor: "pointer" }}
              onMouseEnter={(e) => (e.currentTarget.style.borderColor = "#a3a3a3")}
              onMouseLeave={(e) => (e.currentTarget.style.borderColor = "#e0e0e0")}
            >
              {MODELS.map((m) => (
                <option key={m.id} value={m.id}>{m.vision ? `${m.label} 👁` : m.label}</option>
              ))}
            </select>
          }
        />
        <TooltipContent side="bottom" sideOffset={10}>Select LLM</TooltipContent>
      </Tooltip>

      <TopBarButton tooltip="New chat" onClick={newChat}>
        <PlusIcon size={16} />
      </TopBarButton>

      <TopBarButton tooltip="Viewer theme" onClick={toggleViewerTheme}>
        {viewerTheme === "dark" ? <SunIcon size={16} /> : <MoonIcon size={16} />}
      </TopBarButton>

      <div style={{ position: "relative" }}>
        <TopBarButton tooltip="Download model" onClick={openDownloadMenu} active={menuOpen}>
          <DownloadIcon size={16} />
        </TopBarButton>
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
        <TopBarButton tooltip="Sign out" onClick={() => setConfirmSignOut(true)} danger>
          <LogOutIcon size={16} />
        </TopBarButton>
      )}

      <AlertDialog open={confirmSignOut} onOpenChange={setConfirmSignOut}>
        <AlertDialogContent size="sm" className="gap-3">
          <AlertDialogHeader className="gap-3">
            <AlertDialogTitle>Sign out?</AlertDialogTitle>
            <AlertDialogDescription>
              You&apos;ll need to sign in again to access your chats and models.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel className="h-10">Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={signOut} className="h-10">
              Sign out
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

function ThreadListItem() {
  const runtime = useThreadListItemRuntime();
  const currentTitle = useThreadListItem((s) => s.title);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const startEditing = () => {
    setDraft(currentTitle ?? "");
    setEditing(true);
  };

  const save = () => {
    const next = draft.trim();
    if (next && next !== currentTitle) runtime.rename(next);
    setEditing(false);
  };

  return (
    <ThreadListItemPrimitive.Root
      style={{ display: "flex", alignItems: "center", gap: 6, padding: "2px 8px", borderBottom: "1px solid #f2f2f2", background: "transparent" }}
      onMouseEnter={(e) => (e.currentTarget.style.background = "#f5f5f5")}
      onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
    >
      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onFocus={(e) => e.currentTarget.select()}
          onBlur={save}
          onKeyDown={(e) => {
            if (e.key === "Enter") save();
            else if (e.key === "Escape") setEditing(false);
          }}
          style={{ flex: 1, minWidth: 0, padding: "7px 6px", fontSize: 13, color: "#222", border: "1px solid #d4d4d4", borderRadius: 6, outline: "none" }}
        />
      ) : (
        <>
          <ThreadListItemPrimitive.Trigger
            style={{ flex: 1, minWidth: 0, textAlign: "left", padding: "8px 6px", border: "none", background: "transparent", cursor: "pointer", fontSize: 13, color: "#222", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
          >
            <ThreadListItemPrimitive.Title fallback="New chat" />
          </ThreadListItemPrimitive.Trigger>
          <button
            type="button"
            title="Rename chat"
            aria-label="Rename chat"
            onClick={startEditing}
            style={{ border: "none", background: "none", cursor: "pointer", color: "#bbb", padding: 4, borderRadius: 6, display: "inline-flex" }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "#2563eb"; e.currentTarget.style.background = "#eff6ff"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "#bbb"; e.currentTarget.style.background = "none"; }}
          >
            <PencilIcon size={15} />
          </button>
          <ThreadListItemPrimitive.Delete
            title="Delete chat"
            aria-label="Delete chat"
            style={{ border: "none", background: "none", cursor: "pointer", color: "#bbb", padding: 4, borderRadius: 6, display: "inline-flex" }}
            onMouseEnter={(e) => { e.currentTarget.style.color = "#dc2626"; e.currentTarget.style.background = "#fef2f2"; }}
            onMouseLeave={(e) => { e.currentTarget.style.color = "#bbb"; e.currentTarget.style.background = "none"; }}
          >
            <Trash2Icon size={15} />
          </ThreadListItemPrimitive.Delete>
        </>
      )}
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
