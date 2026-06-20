-- Run this in the Supabase SQL editor to set up Wagmi Bot tables.

create table if not exists job_history (
  id bigserial primary key,
  company text not null,
  title text not null,
  location text not null default '',
  link text,
  posted_at_ts double precision not null default 0,
  created_at timestamptz not null default now(),
  unique (company, title, location, posted_at_ts)
);

create index if not exists idx_job_history_company on job_history (company);

create table if not exists guild_config (
  guild_id bigint primary key,
  country_filters text[] not null default '{}',
  company_filters text[] not null default '{}',
  remote_only boolean,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Migration for existing projects (safe to re-run)
alter table guild_config add column if not exists company_filters text[] not null default '{}';
alter table guild_config add column if not exists remote_only boolean;

create table if not exists guild_posting_channels (
  id bigserial primary key,
  guild_id bigint not null references guild_config (guild_id) on delete cascade,
  channel_id bigint not null,
  created_at timestamptz not null default now(),
  unique (guild_id, channel_id)
);

create index if not exists idx_guild_posting_channels_guild on guild_posting_channels (guild_id);

alter table job_history enable row level security;
alter table guild_config enable row level security;
alter table guild_posting_channels enable row level security;

create policy "Allow service role full access on job_history"
  on job_history for all using (true) with check (true);

create policy "Allow service role full access on guild_config"
  on guild_config for all using (true) with check (true);

create policy "Allow service role full access on guild_posting_channels"
  on guild_posting_channels for all using (true) with check (true);
