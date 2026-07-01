// Proxies model downloads to the backend's token-gated /export endpoint,
// injecting the Bearer token server-side and streaming the file back to the
// browser with a download disposition.
const EXT = {
  stl: "stl",
  "3mf": "3mf",
  step: "step",
  amf: "amf",
  brep: "brep",
  dxf: "dxf",
  svg: "svg",
};
const MIME = {
  stl: "model/stl",
  "3mf": "model/3mf",
  step: "application/step",
  amf: "application/octet-stream",
  brep: "application/octet-stream",
  dxf: "application/dxf",
  svg: "image/svg+xml",
};

export async function GET(req) {
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  const fmt = (new URL(req.url).searchParams.get("fmt") || "step").toLowerCase();
  const ext = EXT[fmt] || "step";

  try {
    const resp = await fetch(
      `${backendUrl}/export?fmt=${encodeURIComponent(fmt)}`,
      { headers: token ? { Authorization: `Bearer ${token}` } : {} },
    );
    if (!resp.ok) {
      const err = await resp.text().catch(() => "");
      return Response.json(
        { error: err || `export failed (${resp.status})` },
        { status: resp.status },
      );
    }
    return new Response(resp.body, {
      headers: {
        "Content-Type": MIME[ext] || "application/octet-stream",
        "Content-Disposition": `attachment; filename="model.${ext}"`,
      },
    });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
