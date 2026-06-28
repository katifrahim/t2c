import Viewer from "../components/Viewer";
import Chat from "../components/Chat";

// Viewer left, chat right.
export default function Page() {
  return (
    <main style={{ display: "flex", height: "100vh" }}>
      <div style={{ flex: 1, minWidth: 0, borderRight: "1px solid #ddd" }}>
        <Viewer />
      </div>
      <div style={{ width: 360, flexShrink: 0 }}>
        <Chat />
      </div>
    </main>
  );
}
