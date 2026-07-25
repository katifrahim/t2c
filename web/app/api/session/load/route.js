import { createClient } from "@/lib/supabase/server";

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

// POST /api/session/load?session=<chatId> — restore a chat's saved CAD objects
// into the backend session so a reopened chat's models come back after a restart.
// The backend skips the import if the session already has objects (no clobbering).
export async function POST(req) {
  if (!CONFIGURED) return Response.json({ restored: false });
  const session = new URL(req.url).searchParams.get("session");
  if (!session || session.startsWith("__LOCALID")) return Response.json({ restored: false });

  const supabase = await createClient();
  const { data } = await supabase
    .from("session_snapshots")
    .select("data")
    .eq("chat_id", session)
    .maybeSingle();
  if (!data?.data) return Response.json({ restored: false });

  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  try {
    const bytes = Buffer.from(data.data, "base64");
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
