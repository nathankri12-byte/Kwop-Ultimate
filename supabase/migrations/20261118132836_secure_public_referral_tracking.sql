-- Keep this migration in sync with the live Supabase migration history.
-- Public referral visitors may increment a click count for a known opaque code,
-- but cannot list referral destinations or read/update the table directly.

create or replace function public.kwop_track_referral(p_code text)
returns table(target_url text)
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  update public.kwop_tracking_links
     set clicks = clicks + 1
   where code = p_code
  returning kwop_tracking_links.target_url;
end;
$$;

revoke all on function public.kwop_track_referral(text) from public;
grant execute on function public.kwop_track_referral(text) to anon;

drop policy if exists "Public can increment referral clicks" on public.kwop_tracking_links;
drop policy if exists "Public can view referral destinations" on public.kwop_tracking_links;
