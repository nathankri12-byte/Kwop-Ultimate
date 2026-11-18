drop policy if exists kwop_launches_public_select on public.kwop_launches;
create policy kwop_launches_public_select on public.kwop_launches for select to anon, authenticated using (published = true);

drop policy if exists kwop_products_public_select on public.kwop_products;
create policy kwop_products_public_select on public.kwop_products for select to anon, authenticated using (
  active = true and exists (
    select 1 from public.kwop_launches l
    where l.id = launch_id and l.published = true
  )
);
