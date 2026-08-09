-- Template Library UI additions. Run in the Supabase SQL editor (or `supabase db
-- push`) AFTER supabase/templates.sql. Adds:
--   1. Per-user on/off preferences (so a user can exclude a template from THEIR RAG
--      retrieval without affecting anyone else — needed because public templates are
--      shared across all users, so a boolean on the row wouldn't be per-user).
--   2. updated_at + has_model columns on templates.
--   3. Owner-scoped UPDATE/DELETE policies (edit/delete own private templates).
--   4. list_templates() — the read path for the library panel: returns everything
--      EXCEPT steps, for the caller's own templates + everyone's approved public ones.
--   5. can_view_template() — authorization helper for the model-preview route.
--   6. match_templates() recreated with an extra filter that drops templates the
--      caller has toggled off.
-- All statements are idempotent so this can be re-run safely.

-- 1. Per-user template preferences. Absence of a row = enabled (the default), so we
-- only ever store a row when a user turns a template OFF (or back on). Cascades with
-- the template and the user so stale prefs can't linger.
create table if not exists public.template_preferences (
  user_id     uuid not null references auth.users (id) on delete cascade,
  template_id uuid not null references public.templates (id) on delete cascade,
  enabled     boolean not null default true,
  updated_at  timestamptz not null default now(),
  primary key (user_id, template_id)
);

alter table public.template_preferences enable row level security;

-- A user manages only their own preference rows.
drop policy if exists "own template prefs" on public.template_preferences;
create policy "own template prefs" on public.template_preferences
  for all to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

-- 2. Freshness/ordering + whether a 3D preview blob was stored in the cad-templates
-- bucket (set true by the store route after a successful upload).
alter table public.templates add column if not exists updated_at timestamptz not null default now();
alter table public.templates add column if not exists has_model boolean not null default false;

-- 3. Owner-scoped write policies. The library only ever edits/deletes PRIVATE
-- templates (enforced in the route); RLS just guarantees you can only touch your own
-- rows. There is still deliberately NO select policy — reads go through the RPCs.
drop policy if exists "update own template" on public.templates;
create policy "update own template" on public.templates
  for update to authenticated
  using (auth.uid() = user_id)
  with check (auth.uid() = user_id);

drop policy if exists "delete own template" on public.templates;
create policy "delete own template" on public.templates
  for delete to authenticated
  using (auth.uid() = user_id);

-- 4. Library listing. SECURITY DEFINER so it can enforce visibility itself and NEVER
-- returns `steps` (the raw table has no select policy, so steps stay server-only).
-- Same visibility rule as match_templates: your own templates (any visibility) plus
-- everyone's approved public ones. `enabled` is the caller's effective toggle state
-- (no pref row → true). `is_owner` drives which rows expose edit/delete in the UI.
drop function if exists public.list_templates();
create or replace function public.list_templates()
  returns table (
    id            uuid,
    title         text,
    description   text,
    visibility    text,
    review_status text,
    verified      boolean,
    has_model     boolean,
    created_at    timestamptz,
    updated_at    timestamptz,
    is_owner      boolean,
    enabled       boolean
  )
  language sql stable security definer set search_path = public as $$
  select t.id, t.title, t.description, t.visibility, t.review_status, t.verified,
         t.has_model, t.created_at, t.updated_at,
         (t.user_id = auth.uid()) as is_owner,
         coalesce(p.enabled, true) as enabled
  from public.templates t
  left join public.template_preferences p
    on p.template_id = t.id and p.user_id = auth.uid()
  where t.user_id = auth.uid()
     or (t.visibility = 'public' and t.review_status = 'approved')
  order by t.updated_at desc;
$$;

-- 5. May the caller view this template's model? Owner (any visibility) or an approved
-- public one. SECURITY DEFINER so the preview route can authorize without a select
-- policy on the table.
drop function if exists public.can_view_template(uuid);
create or replace function public.can_view_template(p_id uuid)
  returns boolean
  language sql stable security definer set search_path = public as $$
  select exists (
    select 1 from public.templates t
    where t.id = p_id
      and (t.user_id = auth.uid()
           or (t.visibility = 'public' and t.review_status = 'approved'))
  );
$$;

-- 6. Recreate match_templates with the per-user toggle filter. Same signature and
-- return type as supabase/templates.sql; the only change is the extra NOT EXISTS that
-- excludes templates the caller has turned off for their own retrieval.
drop function if exists public.match_templates(vector(768), float, int);
create or replace function public.match_templates(
  query_embedding vector(768),
  match_threshold float,
  match_count     int
) returns table (id uuid, title text, description text, steps jsonb, similarity float)
  language sql stable security definer set search_path = public as $$
  select t.id, t.title, t.description, t.steps, 1 - (t.embedding <=> query_embedding) as similarity
  from public.templates t
  where 1 - (t.embedding <=> query_embedding) > match_threshold
    and (
      t.user_id = auth.uid()
      or (t.visibility = 'public' and t.review_status = 'approved')
    )
    and not exists (
      select 1 from public.template_preferences p
      where p.user_id = auth.uid() and p.template_id = t.id and p.enabled = false
    )
  order by t.embedding <=> query_embedding
  limit match_count;
$$;
