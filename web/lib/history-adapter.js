// assistant-ui ThreadHistoryAdapter backed by Supabase. Injected per-thread via
// the thread-list adapter's unstable_Provider, so it reads the active thread's
// remoteId (the Supabase chat id) from the aui store at load/append time.
// withFormat gives fmt.encode/decode for the AI-SDK-v6 UIMessage format.
export function makeHistoryAdapter(aui) {
  const remoteId = () =>
    aui.threadListItem.source ? aui.threadListItem().getState().remoteId : undefined;

  return {
    async load() {
      return { headId: null, messages: [] };
    },
    async append() {},

    withFormat: (fmt) => ({
      async load() {
        const id = remoteId();
        if (!id) return { messages: [] };
        const rows = await fetch(`/api/threads/${id}/messages`)
          .then((r) => r.json())
          .catch(() => []);
        return { messages: Array.isArray(rows) ? rows.map(fmt.decode) : [] };
      },
      async append(item) {
        const id = remoteId();
        if (!id) return;
        await fetch(`/api/threads/${id}/messages`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            id: fmt.getId(item.message),
            parent_id: item.parentId,
            format: fmt.format,
            content: fmt.encode(item),
          }),
        }).catch(() => {});
      },
    }),
  };
}
