# jalan-coupon-watcher

じゃらんの先着クーポンの配布開始を検知して、自分宛にメールで知らせるだけの仕組み。
GitHub Actions で定期実行し、状態は Supabase に保存する。

## できること・やらないこと

- **やること**: クーポンページを GET して読み、前回との差分をメールで通知する
- **やらないこと**: 自動ログイン、自動クーポン獲得（`doGetCoupon` 相当の POST）

じゃらんnet宿泊予約規約 第5条6項12号で「クーポン・ポイントを不正に取得する目的での
予約行為」が禁止されている。違反すると会員資格剥奪・損害賠償請求の対象になる。
このリポジトリのコードは **GET しか行わない**。メールに載せた直リンクを開いて、
「クーポンをGET」は必ず自分の手で押すこと。

`https://www.jalan.net/robots.txt` は確認済み。`/discountCoupon/` と `/theme/` は
どちらも Disallow に含まれておらず、`Crawl-delay` の指定もない（2026-08-31 時点）。
それでも地域ごとのリクエスト間に 1.5 秒のディレイを入れている。

## 仕組み

1. `https://www.jalan.net/theme/jalancouponfes/data/coupon.json` を GET
   → `{"button-kinki": "https://m.recruit.co.jp/link/...", ...}` の10地域
2. 各リンクをリダイレクト追跡 → `https://www.jalan.net/discountCoupon/CAMxxxxxxx/` に着地
   （campaign ID は変動するのでハードコードせず、毎回 JSON から辿る）
3. 着地先は Shift-JIS(Windows-31J)。`cp932` でデコードしてからパース
4. 1ページに複数クーポンが並ぶ。1件の単位は `div.couponBox`：

   ```
   div.couponBox                       ← 配布終了なら couponBox-endDistribute が付く
     h4.couponUseTarget                ← 対象エリア
     div.couponBoxMain                 ← 割引額 / 先着予約数 / 併用ラベル
     table.couponBoxInfo__table        ← 配布期間 / 予約期間 / 宿泊対象期間 / 利用条件 / クーポンID / 併用
     div.couponBoxInfoButtonArea > a   ← 「クーポンをGET」 or 「クーポンの配布は終了しました」
   ```

5. `クーポンID`（`COU5094634` 形式）を主キーにして Supabase の前回状態と突き合わせ、
   差分をメールで送る

### 通知の判定

| 状況 | 通知 |
|---|---|
| 未知のクーポンが**配布中**で現れた | する（`新規`） |
| 既知のクーポンが 終了 → 配布中 に変わった | する（`配布再開`） |
| 割引額・先着数・期間・条件・併用が変わった | する（`内容変更`） |
| 配布中 → 終了 に変わった | 既定はしない（`NOTIFY_ON_SOLD_OUT=true` で有効） |
| 未知のクーポンが最初から配布終了だった | しない（記録だけする） |

`先着予約数` は総枠であって残数ではない（減っていかない）ので、差分検知のノイズにはならない。

## セットアップ

### 1. Supabase にテーブルを作る

Supabase ダッシュボード → SQL Editor に `sql/001_jalan_coupons.sql` を貼って実行する。
`jalan_coupons`（22列 / index 4）と `jalan_watch_runs`（8列 / index 2）ができる。

RLS は有効にしたうえでポリシーを作っていない。service_role キーは RLS をバイパスするので
このスクリプトからは読み書きでき、anon / authenticated からは触れない。
個人用の想定なので、これ以上のポリシー設計はしていない。

どのプロジェクトを使うかは `SUPABASE_URL` の Secret で切り替える。移設するときは
新しいプロジェクトで同じ SQL を流し、Secret を差し替えるだけでよい。

### 2. Gmail のアプリパスワードを発行する

1. Google アカウントで **2段階認証を有効にする**（有効でないとアプリパスワードの項目が出ない）
2. https://myaccount.google.com/apppasswords を開く
3. アプリ名に `jalan-coupon-watcher` などと入れて作成
4. 表示された16桁（スペース区切りで表示されるが、スペースは詰めても入れても可）を控える
   — この画面を閉じると二度と表示されない

通常のログインパスワードでは SMTP 認証できない。

### 3. GitHub Secrets を登録する

リポジトリ → Settings → Secrets and variables → Actions → **Secrets** タブ:

| 名前 | 値 |
|---|---|
| `SUPABASE_URL` | `https://<project-ref>.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase → Settings → API → `service_role` キー |
| `GMAIL_USER` | 送信元 Gmail アドレス |
| `GMAIL_APP_PASSWORD` | 手順2で発行した16桁 |
| `MAIL_TO` | 受信先。送信元と同じで可。カンマ区切りで複数可。未設定なら `GMAIL_USER` 宛 |

同じ画面の **Variables** タブ:

| 名前 | 既定 | 意味 |
|---|---|---|
| `MIN_DISCOUNT_YEN` | `0` | この額未満のクーポンは通知しない。まずは `0` で全件通して動作確認する |
| `MAX_MIN_SPEND_YEN` | `50000` | 利用条件の予約金額がこの額を超えるものは通知しない。`0` で無制限 |
| `NOTIFY_ON_SOLD_OUT` | `false` | 配布終了への遷移も通知するか |

### 予算に合わないクーポンを黙らせる

高額クーポンほど「◯◯円以上の予約で使える」という条件が重い。
30,000円分もらえても 300,000円の予約が要るなら、普段の旅行では使えない。

`MAX_MIN_SPEND_YEN` は利用条件から読み取った必要予約金額を見て、
超えるものを**通知だけ止める**（検知とDB保存はこれまでどおり続ける）。
`NOTIFY_ON_SOLD_OUT` と同じ「検知はするが通知しない」扱い。

読み取りは「予約金額」を優先し、無いときだけ「合計旅行代金」で代用する。
同じ種類が複数書かれていれば安いほうを採る。
**読み取れなかったものは通知する側に倒す** — 絞りすぎて見逃すより、
余計に届くほうがましなため。読み取った額は `jalan_coupons.min_spend_yen` に残る。

閾値を動かすと通知量がどう変わるかは、実データで試算できる。

```sql
select
  count(*) filter (where min_spend_yen is null)   as unknown,
  count(*) filter (where min_spend_yen <= 50000)  as within_50k,
  count(*) filter (where min_spend_yen <= 65000)  as within_65k,
  count(*) filter (where min_spend_yen <= 100000) as within_100k
from public.jalan_coupons;
```

CLI で入れるなら:

```sh
gh secret set SUPABASE_SERVICE_ROLE_KEY
gh secret set GMAIL_USER
gh secret set GMAIL_APP_PASSWORD
```

### 4. 初回の取り込み（seed）

そのまま動かすと「今ある全部が新規」になって初回だけ大量の内容がメールに載る。
Actions → `baseline watch` → Run workflow → `seed` を `true` にして1回流し、
現状を通知せずに DB へ入れておく。以降は差分だけが飛ぶ。

## スケジュール

| ワークフロー | cron (UTC) | JST | 動き |
|---|---|---|---|
| `baseline.yml` | `17 */2 * * *` | 2時間おき | 1回だけ巡回 |
| `burst.yml` | `38 23 * * *` | 08:38 起動 | 09:50 まで待機 → 10:35 まで60秒間隔で巡回 |

### schedule は「数時間」ずれる（実測）

2026-09-01 の private リポジトリでの実測値:

| 予定 (UTC) | 実際に作成された時刻 (UTC) | ずれ |
|---|---|---|
| `burst` 00:47 | 05:44 | **約5時間** |
| `baseline` 22:17 (8/31) | 23:50 | 約1時間33分 |
| `baseline` 04:17 | 05:07 | 約50分 |

しかも予定された回そのものが間引かれる（8/31 12:17〜9/1 05:07 の間に9回予定されて実際は3回）。
**private リポジトリの無料プランでは scheduled workflow が大きく後回しにされる。**
10:00 ちょうどを狙うこの用途では致命的なので、**public にすること**を強く勧める
（認証情報はすべて Secrets にあり、コードには含まれない）。

この遅延を前提に3段構えにしてある:

1. 起動を張り込み窓よりずっと早い 08:38 JST に置き、ジョブの中で 09:50 まで待ってから
   1分間隔で回す（`--loop-from` / `--loop-until`）。起動が最大70分ずれても間に合う
2. それでも 10:35 を過ぎて起動した場合は、翌日の窓まで待たずに**1回だけ巡回して終了**する。
   （待つとジョブが何時間も居座って実行時間を食い潰すため）
3. `--max-minutes 125` で待機込みの上限をかける

### 実行時間の枠

`burst.yml` は待機込みで1日およそ115分使う。private の無料枠2000分/月では到底足りない。
public なら Actions は無制限。

どうしても private のままにしたい場合は、GitHub Actions ではなく
ローカルの cron / launchd で `python -m jalan_watcher.main` を回すほうが、
時刻の正確さの点でもむしろ確実。

また GitHub は、60日間コミットのないリポジトリの scheduled workflow を自動で止める。
止まったら Actions 画面から手動で再有効化する。

## スマホで即座に気づくために

メールが届いても端末が取りに行かなければ意味がない。iPhone なら
設定 → アプリ → メール → メールアカウント → データの取得方法 で、
**該当アカウントを「プッシュ」にする**（Gmail アプリを使う場合は Gmail 側の通知を許可する）。
「フェッチ（15分ごと）」のままだと最大15分遅れる。

件名だけで判断できるように `【近畿】20,000円分クーポン出現（先着3）` の形にしてあるので、
通知バナーだけ見て開くかどうか決められる。

## ローカルで動かす

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env   # 値を埋める
```

```sh
# 実際に巡回して、検出結果とメール本文を表示（Supabase/Gmail 不要）
.venv/bin/python scripts/preview.py --available-only

# 保存済みHTMLに対してパーサだけ流す（ネットワーク不要）
.venv/bin/python scripts/parse_local.py path/to/page.html --url https://www.jalan.net/discountCoupon/CAM0321815/

# 本番と同じ経路で1回だけ。メールは送らず標準出力に出す
set -a; . ./.env; set +a
.venv/bin/python -m jalan_watcher.main --dry-run

# 通知せずDBへ取り込む
.venv/bin/python -m jalan_watcher.main --seed
```

Supabase 本番に触らずに保存経路まで通したいときは、疑似 PostgREST を立てる:

```sh
.venv/bin/python scripts/fake_postgrest.py --port 8771 &
SUPABASE_URL=http://127.0.0.1:8771 SUPABASE_SERVICE_ROLE_KEY=dummy DRY_RUN=true \
  .venv/bin/python -m jalan_watcher.main
# 2回目を流して「通知0件」になれば、差分検知が正しく効いている
```

メールの件名・本文の型を確認するだけなら:

```sh
.venv/bin/python scripts/check_mail_format.py
```

## 構成

```
jalan_watcher/
  config.py     環境変数と地域名テーブル
  fetcher.py    GET のみ。cp932 デコード。リトライ
  parser.py     couponBox 単位のパース
  store.py      Supabase(PostgREST) への読み書き
  notifier.py   件名・本文の組み立てと Gmail SMTP 送信
  main.py       巡回・差分判定・ループ実行
sql/            テーブル定義
scripts/        ローカル確認用
```

## 現状わかっていること（2026-08-31 実測）

- 10地域 × 6本 = 60クーポン。各地域とも 20,000 / 5,000 / 3,000 / 2,000 / 1,000 / 500 円分の構成
- 20,000円分の先着枠は地域あたり **1〜4本**しかなく、この時点ではすでに全地域で配布終了
- 配布中だったのは各地域の 1,000円分と 500円分のみ
- つまり高額枠は 10:00 の配布開始直後に消える。`burst.yml` の張り込みが本体で、
  `baseline.yml` は新しいキャンペーンの開始を拾うための保険という位置づけ

## 監視対象2: クーポン一覧（施設単位の高額クーポン）

クーポンフェスの10地域巡回とは別に、クーポン一覧も並走して見ている。
こちらは施設単位の高額クーポン（3万円以上）が主な対象で、
クーポンフェス側には出てこないものが拾える。

一覧は「クーポン1件 = 1行」ではなく **「クーポン × 対象施設」で行が出る**。
複数施設にまたがるクーポンは参加施設の数だけ重複するので、
必ずクーポンIDで重複排除してから扱う。表示件数もページ数も当てにしない。

一覧が持っているのは 対象宿 / エリア / 各期間 / 予約金額 / クーポン額 だけで、
**先着予約数と配布状態は入っていない**。そのため二段構えにしている。

1. 毎周回：一覧を数リクエストだけ取得し、新しいクーポンIDの出現と
   一覧項目の変化を検知する
2. 動きがあったものだけ：詳細ページを叩いて先着予約数と配布状態を確定する

1周で叩く詳細の本数には上限（既定12件）をかけてある。取り切れなかった分は
`jalan_listing.detail_signature` が空のまま残り、次の周回で拾われる。
初回は35件を3周かけて取り終える。金額の高いものから順に処理する。

### 一覧から消えたものの扱い

「一覧から消えた＝配布終了」かはまだ確定していない。消えたIDを見つけたら
詳細ページを叩いて実際の状態を `jalan_delist_checks` に記録している。
`still_available` が常に false なら仮説の裏付けになる。溜まったら次のSQLで見る。

```sql
select still_available, count(*) from public.jalan_delist_checks group by 1;
```

### 設定

| 環境変数 | 既定 | 意味 |
|---|---|---|
| `WATCH_LISTING` | `true` | 一覧監視を動かすか |
| `LISTING_MIN_YEN` | `30000` | 一覧で拾う下限額。下げるほど件数が跳ね上がる |
| `LISTING_MAX_PAGES` | `4` | 一覧を辿る最大ページ数 |
| `LISTING_MAX_DETAILS` | `12` | 1周で叩く詳細ページの上限 |

下限を3万円にしてあるのは、そこが件数と重複のちょうど境目だから。
2万円まで下げると特定の1〜2件が数百行を占めて一覧が使い物にならなくなる。

エンドポイントの構造調査メモはリポジトリには置いていない。
