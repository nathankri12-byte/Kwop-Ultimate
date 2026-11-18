
create or replace function public.kwop_record_public_replay(
  p_launch_id uuid,
  p_session_key text,
  p_event_type text,
  p_page_path text default '/',
  p_label text default '',
  p_event_value numeric default null,
  p_metadata jsonb default '{}'::jsonb
)
returns table(session_id uuid)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  owner_id uuid;
  visitor_row_id uuid;
  session_row_id uuid;
  safe_page text := left(split_part(coalesce(p_page_path,'/'), '?', 1), 200);
  safe_label text := left(regexp_replace(coalesce(p_label,''), '[[:cntrl:]]', '', 'g'), 100);
  safe_event jsonb;
  safe_metadata jsonb;
  scroll_value integer;
  viewport_value integer;
  element_value text;
begin
  if p_event_type not in ('page_view','click','cta_click','scroll') then
    raise exception 'Unsupported replay event.';
  end if;
  if p_session_key !~ '^[A-Za-z0-9-]{8,80}$' then
    raise exception 'Invalid session key.';
  end if;
  select user_id into owner_id
  from public.kwop_launches
  where id = p_launch_id and published = true;
  if owner_id is null then raise exception 'Launch page is not available.'; end if;
  scroll_value := case when coalesce(p_metadata->>'scroll_y','') ~ '^[0-9]{1,6}$' then least(100000,(p_metadata->>'scroll_y')::integer) else null end;
  viewport_value := case when coalesce(p_metadata->>'viewport_width','') ~ '^[0-9]{1,5}$' then least(10000,(p_metadata->>'viewport_width')::integer) else null end;
  element_value := case when p_metadata->>'element_tag' in ('a','button','div','span') then p_metadata->>'element_tag' else null end;
  safe_metadata := jsonb_build_object('scroll_y',scroll_value,'viewport_width',viewport_value,'element_tag',element_value);
  safe_event := jsonb_build_object('type',p_event_type,'path',safe_page,'label',safe_label,'value',p_event_value,'metadata',safe_metadata,'at',now());
  insert into public.kwop_visitors(user_id,visitor_key,source,device,browser,landing_page,page_views,session_count,consented)
  values(owner_id,p_session_key,'direct','unknown','unknown',safe_page,case when p_event_type='page_view' then 1 else 0 end,1,true)
  on conflict(user_id,visitor_key) do update
    set last_seen=now(),page_views=public.kwop_visitors.page_views + case when p_event_type='page_view' then 1 else 0 end
  returning id into visitor_row_id;
  insert into public.kwop_sessions(user_id,visitor_id,session_key,started_at,last_seen_at,duration_seconds,page_views,entry_page,exit_page,referrer,device,replay,consented)
  values(owner_id,visitor_row_id,p_session_key,now(),now(),0,case when p_event_type='page_view' then 1 else 0 end,safe_page,safe_page,'','unknown',jsonb_build_array(safe_event),true)
  on conflict(user_id,session_key) do update
    set last_seen_at=now(),
        duration_seconds=greatest(0,extract(epoch from (now()-public.kwop_sessions.started_at))::integer),
        page_views=public.kwop_sessions.page_views + case when p_event_type='page_view' then 1 else 0 end,
        exit_page=safe_page,
        replay=case when jsonb_array_length(public.kwop_sessions.replay)<500 then public.kwop_sessions.replay || jsonb_build_array(safe_event) else public.kwop_sessions.replay end
  returning id into session_row_id;
  insert into public.kwop_events(user_id,session_id,visitor_id,event_type,page_path,label,event_value,metadata)
  values(owner_id,session_row_id,visitor_row_id,p_event_type,safe_page,safe_label,p_event_value,safe_metadata);
  return query select session_row_id;
end;
$$;
revoke all on function public.kwop_record_public_replay(uuid,text,text,text,text,numeric,jsonb) from public;
grant execute on function public.kwop_record_public_replay(uuid,text,text,text,text,numeric,jsonb) to anon;
