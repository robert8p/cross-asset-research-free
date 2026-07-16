create extension if not exists pgcrypto;

create table if not exists instruments (
  instrument_id uuid primary key default gen_random_uuid(),
  canonical_symbol text not null unique,
  canonical_name text not null,
  asset_class text not null,
  subcategory text,
  instrument_type text not null,
  economic_exposure text not null,
  provider text not null,
  provider_symbol text not null,
  exchange text,
  exchange_timezone text not null default 'UTC',
  currency text,
  price_unit text,
  contract_multiplier numeric,
  contract_code text,
  contract_code_key text generated always as (coalesce(contract_code, '')) stored,
  expiry_date date,
  is_continuous boolean not null default false,
  continuous_method text,
  volume_type text not null default 'unavailable',
  data_frequency text not null,
  normal_session text,
  extended_session text,
  active_from date,
  active_to date,
  data_licence text,
  redistribution_restrictions text,
  methodological_limitations text,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists market_bars (
  bar_id bigserial primary key,
  instrument_id uuid not null references instruments(instrument_id),
  interval text not null,
  provider_timestamp text,
  bar_open_timestamp_utc timestamptz not null,
  bar_close_timestamp_utc timestamptz not null,
  open numeric not null,
  high numeric not null,
  low numeric not null,
  close numeric not null,
  volume numeric,
  vwap numeric,
  trade_count bigint,
  bid numeric,
  ask numeric,
  mid numeric,
  open_interest numeric,
  source text not null,
  source_symbol text not null,
  contract_code text,
  contract_code_key text generated always as (coalesce(contract_code, '')) stored,
  expiry_date date,
  is_continuous boolean not null default false,
  continuous_method text,
  is_roll_affected boolean not null default false,
  session_type text,
  exchange_trading_date date,
  is_regular_session boolean,
  is_extended_session boolean,
  minutes_since_session_open integer,
  minutes_until_session_close integer,
  day_of_week smallint,
  is_holiday boolean not null default false,
  is_shortened_session boolean not null default false,
  is_partial_bar boolean not null default false,
  is_stale boolean not null default false,
  quality_status text not null default 'unchecked',
  raw_payload_json jsonb,
  ingested_at timestamptz not null default now()
);

create unique index if not exists uq_market_bars_identity
  on market_bars (instrument_id, interval, bar_open_timestamp_utc, contract_code_key, is_continuous);
create index if not exists ix_market_bars_instrument_time
  on market_bars (instrument_id, bar_open_timestamp_utc);
create index if not exists ix_market_bars_source_symbol_time
  on market_bars (source, source_symbol, bar_open_timestamp_utc);

create table if not exists yield_observations (
  observation_id bigserial primary key,
  instrument_id uuid not null references instruments(instrument_id),
  observation_timestamp_utc timestamptz not null,
  observation_date date not null,
  maturity text not null,
  yield_value numeric not null,
  yield_type text not null,
  source text not null,
  source_series text not null,
  published_at timestamptz,
  vintage_date date not null,
  is_revised boolean not null default false,
  original_value numeric,
  ingested_at timestamptz not null default now(),
  unique (instrument_id, observation_date, maturity, vintage_date)
);

create table if not exists futures_rolls (
  roll_id bigserial primary key,
  continuous_instrument_id uuid not null references instruments(instrument_id),
  outgoing_contract text not null,
  incoming_contract text not null,
  decision_timestamp timestamptz not null,
  roll_timestamp timestamptz not null,
  roll_basis text not null,
  price_adjustment numeric,
  adjustment_method text not null,
  outgoing_volume numeric,
  incoming_volume numeric,
  outgoing_open_interest numeric,
  incoming_open_interest numeric,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  unique (continuous_instrument_id, roll_timestamp, incoming_contract)
);

create table if not exists ingestion_runs (
  run_id uuid primary key default gen_random_uuid(),
  source text not null,
  job_type text not null,
  started_at timestamptz not null default now(),
  ended_at timestamptz,
  requested_start timestamptz,
  requested_end timestamptz,
  rows_received bigint not null default 0,
  rows_validated bigint not null default 0,
  rows_inserted bigint not null default 0,
  rows_updated bigint not null default 0,
  duplicates bigint not null default 0,
  rejected_rows bigint not null default 0,
  api_calls integer not null default 0,
  retries integer not null default 0,
  status text not null default 'running',
  error_details jsonb,
  software_version text,
  git_commit text,
  configuration_hash text,
  metadata_json jsonb not null default '{}'::jsonb
);

create table if not exists data_quality_issues (
  issue_id bigserial primary key,
  run_id uuid references ingestion_runs(run_id),
  instrument_id uuid references instruments(instrument_id),
  issue_timestamp timestamptz,
  issue_type text not null,
  severity text not null,
  observed_value text,
  expected_condition text not null,
  resolution text,
  disposition text not null default 'retained',
  original_value_json jsonb,
  corrected_value_json jsonb,
  created_at timestamptz not null default now()
);
create index if not exists ix_quality_issue_instrument_time
  on data_quality_issues (instrument_id, issue_timestamp);

create table if not exists collector_checkpoints (
  provider text not null,
  canonical_symbol text not null,
  interval text not null,
  last_complete_timestamp_utc timestamptz,
  metadata_json jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (provider, canonical_symbol, interval)
);

create table if not exists market_sessions (
  session_id bigserial primary key,
  canonical_symbol text not null,
  exchange_trading_date date not null,
  exchange_timezone text not null,
  regular_open_utc timestamptz,
  regular_close_utc timestamptz,
  extended_open_utc timestamptz,
  extended_close_utc timestamptz,
  is_holiday boolean not null default false,
  is_shortened_session boolean not null default false,
  source text not null,
  metadata_json jsonb not null default '{}'::jsonb,
  unique (canonical_symbol, exchange_trading_date)
);

create table if not exists export_runs (
  export_id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  discovery_start timestamptz not null,
  discovery_end timestamptz not null,
  untouched_start timestamptz not null,
  untouched_end timestamptz not null,
  discovery_archive text,
  untouched_archive text,
  full_archive text,
  manifest_sha256 text,
  status text not null default 'running',
  metadata_json jsonb not null default '{}'::jsonb
);

create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_instruments_updated_at on instruments;
create trigger trg_instruments_updated_at before update on instruments
for each row execute function set_updated_at();

create table if not exists system_settings (
  setting_key text primary key,
  setting_value text not null,
  updated_at timestamptz not null default now()
);

create table if not exists control_jobs (
  job_id uuid primary key default gen_random_uuid(),
  job_type text not null,
  status text not null default 'queued',
  created_at timestamptz not null default now(),
  started_at timestamptz,
  ended_at timestamptz,
  current_step text,
  progress_percent integer not null default 0,
  output_text text,
  error_text text,
  metadata_json jsonb not null default '{}'::jsonb
);
create index if not exists ix_control_jobs_created_at on control_jobs (created_at desc);
