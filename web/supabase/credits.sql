-- t2c credits & usage metering. Run in the Supabase SQL editor (or `supabase db push`).
-- Beta model: every user gets $2.50 of real usage, shown as 2,500 credits.
-- 1 credit = $0.001. The ONLY limit is this balance — there is no cap on the number
-- of messages per chat or the number of chats per user.

-- Per-user credit balance ----------------------------------------------------
create table if not exists public.user_credits (
  user_id           uuid primary key references auth.users (id) on delete cascade,
  credits_remaining int not null default 2500,
  credits_granted   int not null default 2500,
  updated_at        timestamptz not null default now()
);

alter table public.user_credits enable row level security;

-- Users may READ their own balance but never mutate it (no insert/update policy).
drop policy if exists "read own credits" on public.user_credits;
create policy "read own credits" on public.user_credits
  for select using (auth.uid() = user_id);

-- Usage log: one row per completed turn (streamText call) --------------------
create table if not exists public.usage_events (
  id              uuid primary key default gen_random_uuid(),
  user_id         uuid not null references auth.users (id) on delete cascade,
  chat_id         uuid references public.chats (id) on delete set null,
  model           text not null,
  input_tokens    int not null default 0,
  output_tokens   int not null default 0,
  total_tokens    int not null default 0,
  cost_usd        numeric(12,6) not null default 0,
  credits_charged int not null default 0,
  created_at      timestamptz not null default now()
);

create index if not exists usage_events_user_created
  on public.usage_events (user_id, created_at desc);

alter table public.usage_events enable row level security;

drop policy if exists "read own usage" on public.usage_events;
create policy "read own usage" on public.usage_events
  for select using (auth.uid() = user_id);

-- Grant 2500 credits to every new user via a trigger on signup ---------------
create or replace function public.grant_initial_credits()
returns trigger language plpgsql security definer as $$
begin
  insert into public.user_credits (user_id) values (new.id)
    on conflict (user_id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
  after insert on auth.users
  for each row execute function public.grant_initial_credits();

-- Backfill existing users (idempotent)
insert into public.user_credits (user_id)
  select id from auth.users on conflict (user_id) do nothing;

-- Atomic charge: log the usage event AND decrement the balance in one call.
-- SECURITY DEFINER lets it write user_credits despite RLS; it uses auth.uid()
-- internally so a caller can only ever charge their own account. Returns the
-- new remaining balance.
create or replace function public.charge_usage(
  p_chat_id uuid,
  p_model   text,
  p_input   int,
  p_output  int,
  p_cost    numeric,
  p_credits int
) returns int language plpgsql security definer as $$
declare
  v_uid     uuid := auth.uid();
  v_balance int;
begin
  if v_uid is null then
    raise exception 'not authenticated';
  end if;

  insert into public.usage_events (
    user_id, chat_id, model, input_tokens, output_tokens, total_tokens, cost_usd, credits_charged
  ) values (
    v_uid, p_chat_id, p_model, p_input, p_output, p_input + p_output, p_cost, p_credits
  );

  update public.user_credits
     set credits_remaining = credits_remaining - p_credits,
         updated_at = now()
   where user_id = v_uid
   returning credits_remaining into v_balance;

  return v_balance;
end $$;
