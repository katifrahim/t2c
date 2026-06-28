"use client";
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
