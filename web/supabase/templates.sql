-- t2c template library (RAG). Run in the Supabase SQL editor (or `supabase db push`).
-- A shared, global library of reusable CAD build recipes. Each row is one captured
-- model: its exact tool-call sequence (`steps`) plus a short title/description that
-- gets embedded for semantic retrieval. On a chat turn we embed the user's request,
-- find the closest template, and inject its `steps` into the prompt as a worked
-- example (see app/api/chat/route.js and app/api/templates/route.js).

create extension if not exists vector;

create table if not exists public.templates (
  id          uuid primary key default gen_random_uuid(),
  -- Who captured it (attribution / future moderation). Kept even if the user is gone.
  user_id     uuid references auth.users (id) on delete set null,
  title       text not null,
  description text not null,
  -- The template itself: the verbatim array of MCP tool-call payloads to replay.
  steps       jsonb not null,
  -- Whether the capture's rebuild matched the live model (see template-extract.js).
  -- This is an AUTOMATIC fidelity check, NOT dev approval (that's review_status).
  verified    boolean not null default false,
  -- Owner's choice: 'private' is retrievable only by its owner; 'public' is offered
  -- to everyone, but only once a developer approves it (review_status below).
  visibility    text not null default 'private' check (visibility in ('private', 'public')),
  -- Developer moderation, and ONLY for public templates: NULL when it doesn't apply
  -- (private), 'pending' once submitted as public, then 'approved'/'rejected'. Keeping
  -- private rows NULL means `where review_status = 'pending'` is exactly the review
  -- queue. Reviewed by flipping this column in the Supabase dashboard.
  review_status text check (review_status in ('pending', 'approved', 'rejected')),
  -- Gemini gemini-embedding-001 output, requested at 768 dims and L2-normalized so
  -- cosine distance is meaningful (see lib/embeddings.js). Column dim is fixed:
  -- changing the embedding model/size later means re-embedding every row.
  embedding   vector(768) not null,
  created_at  timestamptz not null default now()
);

-- Add the visibility/moderation columns to tables created before they existed.
alter table public.templates
  add column if not exists visibility text not null default 'private'
    check (visibility in ('private', 'public'));
alter table public.templates
  add column if not exists review_status text
    check (review_status in ('pending', 'approved', 'rejected'));
-- Moderation applies to public templates only: keep private rows NULL so the review
-- queue (review_status = 'pending') never contains private templates. Idempotent.
alter table public.templates alter column review_status drop not null;
alter table public.templates alter column review_status set default null;
update public.templates set review_status = null where visibility = 'private';

-- Approximate nearest-neighbour index for cosine similarity (recommended for RAG).
create index if not exists templates_embedding_hnsw
  on public.templates using hnsw (embedding vector_cosine_ops);

alter table public.templates enable row level security;

-- Any signed-in user may contribute a template attributed to themselves. There is
-- deliberately NO select policy: the raw table can't be dumped over PostgREST —
-- reads happen only through match_templates() below, which is SECURITY DEFINER and
-- returns just the top matches. (So the store route inserts WITHOUT .select().)
drop policy if exists "insert own template" on public.templates;
create policy "insert own template" on public.templates
  for insert to authenticated
  with check (auth.uid() = user_id);

-- Semantic search entry point. PostgREST can't use pgvector operators directly, so
-- retrieval calls this via supabase.rpc('match_templates', ...). SECURITY DEFINER +
-- an internal auth.uid() so it can enforce visibility itself: a user matches against
-- their OWN templates (any visibility) plus everyone's approved public ones — nobody
-- else's private or unreviewed work is ever retrievable.
create or replace function public.match_templates(
  query_embedding vector(768),
  match_threshold float,
  match_count     int
) returns table (id uuid, title text, steps jsonb, similarity float)
  language sql stable security definer set search_path = public as $$
  select t.id, t.title, t.steps, 1 - (t.embedding <=> query_embedding) as similarity
  from public.templates t
  where 1 - (t.embedding <=> query_embedding) > match_threshold
    and (
      t.user_id = auth.uid()
      or (t.visibility = 'public' and t.review_status = 'approved')
    )
  order by t.embedding <=> query_embedding
  limit match_count;
$$;
