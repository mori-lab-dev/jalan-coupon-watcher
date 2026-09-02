-- じゃらんクーポン監視のテーブル
-- 適用: Supabase ダッシュボード > SQL Editor に貼って実行

create table if not exists public.jalan_coupons (
  coupon_id         text primary key,              -- 例: COU5094634（ページ内のクーポンID）
  source            text not null,                 -- 例: jalancouponfes
  region_key        text not null,                 -- coupon.json のキー 例: button-kinki
  region_name       text not null,                 -- 例: 近畿
  campaign_id       text,                          -- 例: CAM0321815（毎回JSONから辿るので変動前提）
  page_url          text not null,
  discount_yen      integer,                       -- 円以外（%等）のときは null
  discount_label    text,                          -- 例: 20,000円分
  stock             integer,                       -- 先着予約数（総枠。残数ではない）
  is_available      boolean not null default false,-- 「クーポンをGET」が押せる状態か
  status_text       text,
  distribute_period text,
  reserve_period    text,
  stay_period       text,
  conditions        text,
  combinable        text,
  target_text       text,
  content_hash      text not null,
  first_seen_at     timestamptz not null default now(),
  last_seen_at      timestamptz not null default now(),
  last_changed_at   timestamptz not null default now(),
  notified_at       timestamptz
);

create index if not exists jalan_coupons_region_idx on public.jalan_coupons (region_key);
create index if not exists jalan_coupons_available_idx on public.jalan_coupons (is_available, discount_yen desc);
create index if not exists jalan_coupons_last_seen_idx on public.jalan_coupons (last_seen_at desc);

create table if not exists public.jalan_watch_runs (
  id                bigserial primary key,
  started_at        timestamptz not null default now(),
  finished_at       timestamptz,
  coupons_seen      integer,
  coupons_available integer,
  notified_count    integer,
  regions_failed    integer,
  error             text
);

create index if not exists jalan_watch_runs_started_idx on public.jalan_watch_runs (started_at desc);

-- 個人用。service_role キーのみが読み書きする想定なので、
-- RLS を有効にしたうえでポリシーは作らない（anon/authenticated からは触れない）。
alter table public.jalan_coupons enable row level security;
alter table public.jalan_watch_runs enable row level security;
