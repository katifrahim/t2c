// Proxies a "new session" reset to the backend's token-gated /clear endpoint,
// injecting the Bearer token server-side (the browser never sees it). The
// ?session=<id> selects which chat's backend state to clear.
export async function POST(req) {
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  const session = new URL(req.url).searchParams.get("session") || "";
  try {
    const resp = await fetch(`${backendUrl}/clear?session=${encodeURIComponent(session)}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    const data = await resp.json().catch(() => ({}));
    return Response.json(data, { status: resp.status });
  } catch (e) {
    return Response.json({ error: String(e) }, { status: 502 });
  }
}
