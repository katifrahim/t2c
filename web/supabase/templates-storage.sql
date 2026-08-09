-- Per-template 3D preview models live in Supabase STORAGE, one pickled-snapshot blob
-- per template at "<templateId>" in a private bucket. Same blob format as
-- cad-snapshots (pickled CadQuery objects) — it contains the GEOMETRY only, never the
-- template's `steps`, so previewing a template can't leak its build recipe.
--
-- Unlike cad-snapshots, access is NOT keyed to the caller's uid: a public template's
-- model must be viewable by everyone, and a private one only by its owner. That rule
-- lives in the templates table, not the path, so we don't express it as storage RLS.
-- Instead every blob read/write goes through a server route that authorizes via
-- can_view_template()/ownership and uses the service-role key. Hence: private bucket,
-- no public policies. Run once in the SQL editor.
insert into storage.buckets (id, name, public)
values ('cad-templates', 'cad-templates', false)
on conflict (id) do nothing;
