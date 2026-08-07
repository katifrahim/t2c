-- CAD session snapshots now live in Supabase STORAGE (object storage), off the
-- Postgres disk + disk-IO budget. Run once in the SQL editor.
--
-- Objects are stored at "<uid>/<chatId>" in a private bucket. RLS keys access to
-- the first path segment (the owner's auth uid). New saves go here; the legacy
-- public.session_snapshots table is kept read-only as a fallback so pre-existing
-- chats still restore (it can be dropped once all active chats have re-saved).

-- Private bucket (idempotent).
insert into storage.buckets (id, name, public)
values ('cad-snapshots', 'cad-snapshots', false)
on conflict (id) do nothing;

-- RLS: a user may read/write/update/delete only their own snapshots.
drop policy if exists "cad snapshots read"   on storage.objects;
drop policy if exists "cad snapshots insert" on storage.objects;
drop policy if exists "cad snapshots update" on storage.objects;
drop policy if exists "cad snapshots delete" on storage.objects;

create policy "cad snapshots read" on storage.objects for select
  using (bucket_id = 'cad-snapshots' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "cad snapshots insert" on storage.objects for insert
  with check (bucket_id = 'cad-snapshots' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "cad snapshots update" on storage.objects for update
  using (bucket_id = 'cad-snapshots' and (storage.foldername(name))[1] = auth.uid()::text);

create policy "cad snapshots delete" on storage.objects for delete
  using (bucket_id = 'cad-snapshots' and (storage.foldername(name))[1] = auth.uid()::text);
