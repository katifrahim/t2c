"use client";
import { useState, useEffect } from "react";
import { LoaderIcon } from "lucide-react";
import {
  ResizablePanelGroup,
  ResizablePanel,
  ResizableHandle,
} from "@/components/ui/resizable";
import Viewer from "@/components/Viewer";
import Chat from "@/components/Chat";
import { cn } from "@/lib/utils";
import { useIsMobile } from "@/lib/use-is-mobile";

// Below `md` (768px) a side-by-side split leaves both panes too thin to use, so
// mobile shows one pane full-screen with a [3D | Chat] toggle. Both panes stay
// mounted (inactive one hidden with display:none) so switching never rebuilds the
// WebGL viewer or resets the chat; the Viewer re-fits via its own ResizeObserver.
function MobileWorkspace() {
  const [pane, setPane] = useState("chat"); // start on Chat: first action is to type a prompt
  // Full-bleed segmented toggle: the active pane is a solid block matching its
  // content (white for chat, muted for the 3D viewer chrome), the inactive one is
  // muted — no rounding, gaps, or padding, so it reads as one left/right toggle.
  const tab = (id, label) => (
    <button
      type="button"
      onClick={() => setPane(id)}
      aria-pressed={pane === id}
      className={cn(
        "flex-1 py-2 text-sm font-medium transition-colors",
        id === "viewer" && "border-r", // thin divider between the two segments
        pane === id
          ? "bg-background text-foreground"
          : "bg-muted text-muted-foreground"
      )}
    >
      {label}
    </button>
  );
  return (
    <div className="flex h-dvh w-full flex-col">
      <div className="flex shrink-0 border-b">
        {tab("viewer", "View")}
        {tab("chat", "Chat")}
      </div>
      <div className="relative min-h-0 flex-1">
        <div className={pane === "viewer" ? "h-full" : "hidden"}>
          <Viewer />
        </div>
        <div className={pane === "chat" ? "h-full" : "hidden"}>
          <Chat />
        </div>
      </div>
    </div>
  );
}

// Resizable split: 3D viewer on the left, chat on the right. Panels use
// percentage sizes so both reflow on window resize; the Viewer re-fits its
// canvas via its own ResizeObserver (covers both window resize and drags).
export default function Workspace() {
  // react-resizable-panels only computes its 65/35 layout in a post-mount
  // effect; the SSR/first-paint HTML would otherwise show mis-sized panels
  // until the (heavy) client bundle hydrates. Render a spinner until mounted
  // so the browser never paints the wrong layout.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isMobile = useIsMobile();

  if (!mounted) {
    return (
      <div className="flex h-dvh w-screen items-center justify-center">
        <LoaderIcon className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (isMobile) return <MobileWorkspace />;

  return (
    <ResizablePanelGroup direction="horizontal" className="h-dvh w-screen">
      <ResizablePanel defaultSize="65%" minSize="275px">
        <Viewer />
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize="35%" minSize="250px">
        <Chat />
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
