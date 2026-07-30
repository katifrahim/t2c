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
// mounted so switching never rebuilds the WebGL viewer or resets the chat.
//
// Layout is a layered overlay, not a show/hide of two equals:
//  - The Viewer is the bottom layer, ALWAYS kept laid out at full size so its
//    canvas builds at the real phone dimensions (never 0-wide). With display:none
//    its container measured 0, so it built at a desktop fallback size — expanded
//    toolbar, oversized model, mis-placed legend — and only snapped correct once
//    revealed. When Chat is active the Viewer is hidden with opacity:0 (not
//    display:none / visibility:hidden): opacity:0 makes the whole viewer a single
//    transparent stacking-context group, so even its high z-index (up to 1000)
//    Tools/Info dropdowns are composited into that group and can't paint over the
//    chat. Layout size is preserved, so there's nothing to re-fit (no flash).
//  - Chat is an opaque overlay on top: rendered when active, display:none when not.
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
        "flex-1 cursor-pointer py-2 text-sm font-medium transition-colors",
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
        {/* Viewer: bottom layer, always laid out at full size so its canvas builds
            at real phone dimensions. Hidden via opacity:0 (a transparent stacking
            group) when Chat is active so its high z-index Tools/Info dropdowns
            can't bleed over the chat, while its size is preserved (no re-fit/flash).
            Chat: opaque overlay on top, display:none when inactive. */}
        <div className={cn("absolute inset-0", pane !== "viewer" && "opacity-0 pointer-events-none")}>
          <Viewer />
        </div>
        <div className={pane === "chat" ? "absolute inset-0" : "hidden"}>
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
      <ResizablePanel defaultSize="65%" minSize="350px"> 
        <Viewer />
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize="35%" minSize="350px">
        <Chat />
      </ResizablePanel>
    </ResizablePanelGroup>
  );
  // sum of minSize of both panels must be <= 768px (mobile breakpoint width)
}
