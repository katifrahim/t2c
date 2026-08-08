import { createClient } from "@/lib/supabase/server";

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;
const SNAPSHOT_BUCKET = "cad-snapshots";

// POST /api/session/load?session=<chatId> — restore a chat's saved CAD objects
// into the backend session so a reopened chat's models come back after a restart.
// The backend skips the import if the session already has objects (no clobbering).
export async function POST(req) {
  if (!CONFIGURED) return Response.json({ restored: false });
  const session = new URL(req.url).searchParams.get("session");
  if (!session || session.startsWith("__LOCALID")) return Response.json({ restored: false });

  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  const uid = claims?.claims?.sub ?? null;

  // Preferred source: the blob in Storage (off Postgres).
  let bytes = null;
  if (uid) {
    const { data: blob } = await supabase.storage
      .from(SNAPSHOT_BUCKET)
      .download(`${uid}/${session}`);
    if (blob) bytes = Buffer.from(await blob.arrayBuffer());
  }
  // Legacy fallback: base64 blob in the session_snapshots table (pre-Storage chats).
  // TODO(2026-09-07): remove this fallback + `drop table public.session_snapshots`
  // in the SQL editor. By then every active chat has re-saved to the Storage bucket,
  // so the table holds nothing of value and is dead weight.
  if (!bytes) {
    const { data } = await supabase
      .from("session_snapshots")
      .select("data")
      .eq("chat_id", session)
      .maybeSingle();
    if (data?.data) bytes = Buffer.from(data.data, "base64");
  }
  if (!bytes) return Response.json({ restored: false });

  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  try {
    const resp = await fetch(
      `${backendUrl}/session/import?session=${encodeURIComponent(session)}`,
      {
        method: "POST",
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          "Content-Type": "application/octet-stream",
        },
        body: bytes,
      },
    );
    const r = await resp.json().catch(() => ({}));
    return Response.json({ restored: r.status === "ok", ...r });
  } catch (e) {
    return Response.json({ restored: false, error: String(e) }, { status: 502 });
  }
}
