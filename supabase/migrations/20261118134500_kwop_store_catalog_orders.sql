create table if not exists public.kwop_customers (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  name text not null default '',
  email text not null,
  phone text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(user_id,email)
);
create table if not exists public.kwop_products (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  launch_id uuid references public.kwop_launches(id) on delete cascade,
  name text not null,
  description text not null default '',
  price numeric(12,2) not null check(price >= 0),
  currency text not null default 'EUR' check(currency in ('EUR','USD','GBP')),
  stock integer check(stock is null or stock >= 0),
  sku text not null default '',
  image_url text not null default '',
  checkout_url text not null default '',
  active boolean not null default true,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create table if not exists public.kwop_orders (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  launch_id uuid references public.kwop_launches(id) on delete set null,
  product_id uuid references public.kwop_products(id) on delete set null,
  customer_id uuid references public.kwop_customers(id) on delete set null,
  customer_name text not null default '',
  customer_email text not null,
  quantity integer not null check(quantity between 1 and 50),
  unit_price numeric(12,2) not null check(unit_price >= 0),
  total numeric(12,2) not null check(total >= 0),
  currency text not null default 'EUR' check(currency in ('EUR','USD','GBP')),
  status text not null default 'pending' check(status in ('pending','paid','fulfilled','cancelled')),
  payment_reference text not null default '',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.kwop_customers enable row level security;
alter table public.kwop_products enable row level security;
alter table public.kwop_orders enable row level security;
grant select, insert, update, delete on public.kwop_customers to authenticated;
grant select, insert, update, delete on public.kwop_products to authenticated;
grant select, insert, update, delete on public.kwop_orders to authenticated;
grant select on public.kwop_products to anon;
drop policy if exists kwop_customers_owner_all on public.kwop_customers;
create policy kwop_customers_owner_all on public.kwop_customers for all to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
drop policy if exists kwop_products_owner_all on public.kwop_products;
create policy kwop_products_owner_all on public.kwop_products for all to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
drop policy if exists kwop_products_public_select on public.kwop_products;
create policy kwop_products_public_select on public.kwop_products for select to anon using (active = true and exists (select 1 from public.kwop_launches l where l.id = launch_id and l.published = true));
drop policy if exists kwop_orders_owner_all on public.kwop_orders;
create policy kwop_orders_owner_all on public.kwop_orders for all to authenticated using ((select auth.uid()) = user_id) with check ((select auth.uid()) = user_id);
create index if not exists kwop_products_user_launch_idx on public.kwop_products(user_id, launch_id, active);
create index if not exists kwop_orders_user_created_idx on public.kwop_orders(user_id, created_at desc);
create index if not exists kwop_orders_customer_idx on public.kwop_orders(customer_id);
create or replace function public.kwop_create_public_order(p_product_id uuid,p_customer_name text,p_customer_email text,p_quantity integer)
returns table(order_id uuid,total numeric) language plpgsql security definer set search_path = public, pg_temp as $$
declare product_row public.kwop_products%rowtype; customer_row_id uuid; order_row_id uuid; order_total numeric(12,2); clean_email text := lower(trim(coalesce(p_customer_email,''))); clean_name text := trim(coalesce(p_customer_name,''));
begin
  if p_quantity is null or p_quantity < 1 or p_quantity > 50 then raise exception 'Quantity must be between 1 and 50.'; end if;
  if length(clean_name) < 2 or length(clean_name) > 120 then raise exception 'Enter a valid name.'; end if;
  if length(clean_email) > 254 or clean_email !~ '^[^@[:space:]]+@[^@[:space:]]+[.][^@[:space:]]+$' then raise exception 'Enter a valid email address.'; end if;
  select p.* into product_row from public.kwop_products p join public.kwop_launches l on l.id = p.launch_id where p.id = p_product_id and p.active = true and l.published = true for update of p;
  if not found then raise exception 'This product is not available.'; end if;
  if product_row.stock is not null and product_row.stock < p_quantity then raise exception 'Not enough stock is available.'; end if;
  insert into public.kwop_customers(user_id,name,email) values(product_row.user_id,clean_name,clean_email) on conflict(user_id,email) do update set name = excluded.name, updated_at = now() returning id into customer_row_id;
  order_total := round(product_row.price * p_quantity, 2);
  insert into public.kwop_orders(user_id,launch_id,product_id,customer_id,customer_name,customer_email,quantity,unit_price,total,currency,status) values(product_row.user_id,product_row.launch_id,product_row.id,customer_row_id,clean_name,clean_email,p_quantity,product_row.price,order_total,product_row.currency,'pending') returning id into order_row_id;
  if product_row.stock is not null then update public.kwop_products set stock = stock - p_quantity, updated_at = now() where id = product_row.id; end if;
  return query select order_row_id, order_total;
end; $$;
revoke all on function public.kwop_create_public_order(uuid,text,text,integer) from public;
grant execute on function public.kwop_create_public_order(uuid,text,text,integer) to anon;
