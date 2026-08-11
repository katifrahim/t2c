// Proxies a CAD file upload to the backend's token-gated /import endpoint, injecting the
// Bearer token server-side (the browser never sees it). The backend converts the file to
// AP242, makes it the active model, and returns a structured geometric description. The raw
// file bytes are the request body; ?session=<id>&filename=<name> select the chat and let the
// backend validate the format.
export async function POST(req) {
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  const params = new URL(req.url).searchParams;
  const session = params.get("session") || "";
  const filename = params.get("filename") || "";
  try {
    const body = await req.arrayBuffer();
    const qs = new URLSearchParams({ session, filename });
    const resp = await fetch(`${backendUrl}/import?${qs.toString()}`, {
      method: "POST",
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
        "Content-Type": "application/octet-stream",
      },
      body,
    });
    const data = await resp.json().catch(() => ({}));
    return Response.json(data, { status: resp.status });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
