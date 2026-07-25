-- t2c chat history schema. Run in the Supabase SQL editor (or `supabase db push`).
-- Row-level security scopes every row to the signed-in user (auth.uid()), so the
-- API routes use the ordinary publishable key and can't leak across users.

-- Chats ----------------------------------------------------------------------
create table if not exists public.chats (
  id         uuid primary key default gen_random_uuid(),
  user_id    uuid not null references auth.users (id) on delete cascade,
  title      text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists chats_user_updated
  on public.chats (user_id, updated_at desc);

alter table public.chats enable row level security;

drop policy if exists "own chats" on public.chats;
create policy "own chats" on public.chats
  for all
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- Messages -------------------------------------------------------------------
-- id is the assistant-ui message id (text). content is the encoded UIMessage.
create table if not exists public.messages (
  id         text primary key,
  chat_id    uuid not null references public.chats (id) on delete cascade,
  parent_id  text,
  format     text,
  content    jsonb not null,
  created_at timestamptz not null default now()
);

create index if not exists messages_chat_created
  on public.messages (chat_id, created_at);

alter table public.messages enable row level security;

drop policy if exists "own messages" on public.messages;
create policy "own messages" on public.messages
  for all
  using (exists (
    select 1 from public.chats c
    where c.id = messages.chat_id and c.user_id = auth.uid()
  ))
  with check (exists (
    select 1 from public.chats c
    where c.id = messages.chat_id and c.user_id = auth.uid()
  ));
