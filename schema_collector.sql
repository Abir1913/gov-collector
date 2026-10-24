-- Run in the Supabase SQL editor AFTER schema.sql. Adds what the collector needs.
alter table public.notices add column if not exists fingerprint text;
alter table public.notices add column if not exists needs_review boolean default false;
alter table public.notices add column if not exists confidence numeric;
alter table public.notices add column if not exists lang text;
alter table public.notices add column if not exists first_seen timestamptz;
alter table public.notices add column if not exists last_seen timestamptz;
alter table public.notices add column if not exists extra jsonb default '{}';
create unique index if not exists notices_fingerprint_key on public.notices(fingerprint);

-- Live crawl status shown by the website's "Update all sources" button. Written only by the collector (service key).
create table if not exists public.crawl_status(
  id int primary key, state text, started timestamptz, finished timestamptz,
  updated timestamptz default now(), stats jsonb default '{}', note text);
alter table public.crawl_status enable row level security;
drop policy if exists "crawl status readable" on public.crawl_status;
create policy "crawl status readable" on public.crawl_status for select using (true);
insert into public.crawl_status(id,state) values (1,'idle') on conflict (id) do nothing;
