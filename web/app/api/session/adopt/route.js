// POST /api/session/adopt?from=<localId>&to=<chatId> — re-home a new chat's live CAD state
// (a model imported or built before the first message, stored under the temporary local id)
// onto the persistent chat id, injecting the Bearer token server-side. The backend no-ops if
// there is nothing to move or the target already has work.
export async function POST(req) {
  const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8080";
  const token = process.env.MCP_TOKEN;
  const params = new URL(req.url).searchParams;
  const from = params.get("from") || "";
  const to = params.get("to") || "";
  if (!from || !to || from === to) return Response.json({ status: "noop" });
  try {
    const qs = new URLSearchParams({ from, to });
    const resp = await fetch(`${backendUrl}/session/adopt?${qs.toString()}`, {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    const data = await resp.json().catch(() => ({}));
    return Response.json(data, { status: resp.status });
  } catch (e) {
    return Response.json({ status: "error", error: String(e) }, { status: 502 });
  }
}
