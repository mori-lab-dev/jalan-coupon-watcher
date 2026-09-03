-- Phase 2: クーポン一覧の監視を追加する
-- 適用: Supabase ダッシュボード > SQL Editor に貼って実行

-- 1) 施設クーポン用の項目を jalan_coupons に足す（クーポンフェス側は空のまま）
alter table public.jalan_coupons add column if not exists title        text;
alter table public.jalan_coupons add column if not exists hotel_name   text;
alter table public.jalan_coupons add column if not exists area_name    text;
alter table public.jalan_coupons add column if not exists usable_plans text;
alter table public.jalan_coupons add column if not exists min_spend    text;

create index if not exists jalan_coupons_source_idx on public.jalan_coupons (source);

-- 2) 一覧の行そのものを持つ表
--    施設クーポンは listing_id = クーポンID、じゃらんクーポンは listing_id = キャンペーンID。
--    キャンペーン型は1件が複数クーポンに展開されるため、
--    「一覧に載っていたか」は jalan_coupons とは別にここで管理する。
create table if not exists public.jalan_listing (
  listing_id        text primary key,          -- COU（24桁）または CAM
  kind              text not null,             -- facility / campaign
  title             text,
  hotel_name        text,                      -- 施設名、またはキャンペーンなら「58宿」等
  area_name         text,
  distribute_period text,
  reserve_period    text,
  stay_period       text,
  min_spend         text,                      -- 一覧の「予約金額」
  discount_label    text,
  discount_yen      integer,
  detail_url        text not null,
  list_signature    text not null,             -- 一覧側の項目が変わったかの判定用
  -- 詳細ページを最後に取ったときの list_signature。
  -- 1周で叩く詳細には上限があるので、取り切れなかった分はここが null のまま残り、
  -- 次の周回で拾われる。これが無いと「一覧には載ったが詳細は未取得」を取りこぼす。
  detail_signature  text,
  first_seen_at     timestamptz not null default now(),
  last_listed_at    timestamptz not null default now(),
  delisted_at       timestamptz                -- 一覧から消えたら入る。再掲載で null に戻す
);

create index if not exists jalan_listing_kind_idx on public.jalan_listing (kind);
create index if not exists jalan_listing_yen_idx  on public.jalan_listing (discount_yen desc);
create index if not exists jalan_listing_delisted_idx on public.jalan_listing (delisted_at);

-- 3) 「一覧から消えた＝配布終了なのか」を検証するための記録
--    消えたのを見つけた時点で詳細ページを叩き、その場の状態を残す。
--    still_available が常に false なら「消えた＝終了」の仮説が裏付けられる。
create table if not exists public.jalan_delist_checks (
  id              bigserial primary key,
  listing_id      text not null,
  kind            text,
  discount_yen    integer,
  checked_at      timestamptz not null default now(),
  still_available boolean,                     -- 詳細ページ上でまだ配布中だったか
  status_text     text,
  note            text
);

create index if not exists jalan_delist_checks_listing_idx on public.jalan_delist_checks (listing_id);
create index if not exists jalan_delist_checks_at_idx on public.jalan_delist_checks (checked_at desc);

alter table public.jalan_listing enable row level security;
alter table public.jalan_delist_checks enable row level security;
