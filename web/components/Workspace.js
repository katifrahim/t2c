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

  if (!mounted) {
    return (
      <div className="flex h-screen w-screen items-center justify-center">
        <LoaderIcon className="animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <ResizablePanelGroup direction="horizontal" className="h-screen w-screen">
      <ResizablePanel defaultSize={65} minSize={25}>
        <Viewer />
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize={35} minSize={25}>
        <Chat />
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
