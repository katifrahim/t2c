-- Phase 5: durable CAD session snapshots. Run in the Supabase SQL editor.
-- One row per chat holds the pickled backend session (base64) so the models a
-- chat built survive MCP/web restarts and reload when the chat is reopened.

create table if not exists public.session_snapshots (
  chat_id    uuid primary key references public.chats (id) on delete cascade,
  user_id    uuid not null references auth.users (id) on delete cascade,
  data       text not null,            -- base64-encoded pickle snapshot
  updated_at timestamptz not null default now()
);

alter table public.session_snapshots enable row level security;

drop policy if exists "own snapshots" on public.session_snapshots;
create policy "own snapshots" on public.session_snapshots
  for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);
