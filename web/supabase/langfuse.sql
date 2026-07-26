-- Link each usage row to its Langfuse trace, so you can jump from a DB usage/cost
-- row to the full trace (and back). Run in the Supabase SQL editor. Idempotent.

alter table public.usage_events add column if not exists trace_id text;

-- Drop the old 6-arg signature so the trace-aware one below isn't an overload.
drop function if exists public.charge_usage(uuid, text, int, int, numeric, int);

-- charge_usage + the Langfuse trace id (mirrors credits.sql, one extra column).
create or replace function public.charge_usage(
  p_chat_id  uuid,
  p_model    text,
  p_input    int,
  p_output   int,
  p_cost     numeric,
  p_credits  int,
  p_trace_id text default null
) returns int language plpgsql security definer as $$
declare
  v_uid     uuid := auth.uid();
  v_balance int;
begin
  if v_uid is null then
    raise exception 'not authenticated';
  end if;

  insert into public.usage_events (
    user_id, chat_id, model, input_tokens, output_tokens, total_tokens, cost_usd, credits_charged, trace_id
  ) values (
    v_uid, p_chat_id, p_model, p_input, p_output, p_input + p_output, p_cost, p_credits, p_trace_id
  );

  -- greatest(0, ...) floors the balance so a turn that overspends can never drive
  -- it negative (the app also stops turns before they exhaust the balance).
  update public.user_credits
     set credits_remaining = greatest(0, credits_remaining - p_credits),
         updated_at = now()
   where user_id = v_uid
   returning credits_remaining into v_balance;

  return v_balance;
end $$;
