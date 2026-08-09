import { createClient } from "@/lib/supabase/server";
import { createServiceClient, TEMPLATE_BUCKET } from "@/lib/supabase/service";

// POST /api/templates/[id]/preview — load a template's stored 3D model into a
// throwaway backend session ("tpl_<id>") so the viewer can show it without touching
// the user's live chat session. Mirrors app/api/session/load: download the blob and
// POST it to the backend's /session/import. Returns { available, sessionId }.
// Only the geometry blob is used here — the template's `steps` never leave the server.

const CONFIGURED = !!process.env.NEXT_PUBLIC_SUPABASE_URL;

export async function POST(_req, { params }) {
  if (!CONFIGURED) return Response.json({ available: false });
  const { id } = await params;

  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  if (!claims?.claims?.sub) return Response.json({ error: "not authenticated" }, { status: 401 });

  // Authorize: caller must own it or it must be an approved public template.
  const { data: allowed, error: authErr } = await supabase.rpc("can_view_template", { p_id: id });
  if (authErr) return Response.json({ error: "auth check failed" }, { status: 500 });
  if (!allowed) return Response.json({ error: "not found" }, { status: 404 });

  const service = createServiceClient();
  if (!service) return Response.json({ available: false });
  const { data: blob } = await service.storage.from(TEMPLATE_BUCKET).download(id);
  if (!blob) return Response.json({ available: false }); // legacy template, no model stored

  const session = `tpl_${id}`;
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  try {
    const bytes = Buffer.from(await blob.arrayBuffer());
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
    // "ok" = just imported; "already-loaded" = a previous preview left the model in
    // this session (the backend won't clobber it). Both mean the model is on screen.
    const available = r.status === "ok" || r.status === "already-loaded";
    return Response.json({ available, sessionId: session });
  } catch (e) {
    return Response.json({ available: false, error: String(e) }, { status: 502 });
  }
}
