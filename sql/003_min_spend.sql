-- 利用条件から読み取った「必要な予約金額」を持たせる。
-- 通知するかどうかの判断に使うほか、あとから閾値を見直すときの材料になる。
-- 適用: Supabase ダッシュボード > SQL Editor に貼って実行

alter table public.jalan_coupons add column if not exists min_spend_yen integer;

create index if not exists jalan_coupons_min_spend_idx
  on public.jalan_coupons (min_spend_yen);
