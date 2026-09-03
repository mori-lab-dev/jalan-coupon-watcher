"""エントリポイント。

  python -m jalan_watcher.main                       1回だけ実行
  python -m jalan_watcher.main --seed                通知せず現状をDBに取り込む（初回用）
  python -m jalan_watcher.main --loop-until 10:35 --interval-sec 60
                                                     JST指定時刻まで一定間隔で回す
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from .config import Config, region_name
from .facility import parse_facility_page
from .fetcher import fetch_html, fetch_region_links, fetch_listing_page, new_session
from .listing import KIND_CAMPAIGN, KIND_FACILITY, ListingEntry, dedupe, parse_listing_page
from .notifier import Event, check_login, send
from .parser import HASHED_FIELDS, Coupon, parse_coupon_page
from .store import Store

JST = timezone(timedelta(hours=9))
SOURCE = "jalancouponfes"
SOURCE_FACILITY = "listing_facility"
SOURCE_CAMPAIGN = "listing_campaign"

FIELD_LABELS = {
    "discount_label": "割引額",
    "stock": "先着予約数",
    "distribute_period": "配布期間",
    "reserve_period": "予約期間",
    "stay_period": "宿泊対象期間",
    "conditions": "利用条件",
    "combinable": "併用",
    "target_text": "対象",
}

log = logging.getLogger("jalan_watcher")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _at(now: datetime, hhmm: str) -> datetime:
    """同じ日の JST HH:MM を返す。"""
    hh, mm = (int(x) for x in hhmm.split(":"))
    return now.replace(hour=hh, minute=mm, second=0, microsecond=0)


def collect(cfg: Config) -> tuple[list[Coupon], list[str]]:
    """全地域を巡回してクーポンを集める。戻り値は (クーポン一覧, エラー一覧)。"""
    session = new_session()
    links = fetch_region_links(session)
    log.info("coupon.json: %d地域", len(links))

    coupons: list[Coupon] = []
    errors: list[str] = []
    seen_ids: set[str] = set()

    for i, (key, link) in enumerate(sorted(links.items())):
        if i:
            time.sleep(cfg.request_delay_sec)
        try:
            final_url, html = fetch_html(session, link)
            found = parse_coupon_page(
                html,
                page_url=final_url,
                region_key=key,
                region_name=region_name(key),
                source=SOURCE,
            )
            # 同一キャンペーンが複数地域から参照されても二重計上しない
            fresh = [c for c in found if c.coupon_id not in seen_ids]
            seen_ids.update(c.coupon_id for c in fresh)
            coupons.extend(fresh)
            log.info("%s (%s): %d件 -> %s", region_name(key), key, len(fresh), final_url)
        except Exception as exc:  # noqa: BLE001
            msg = f"{key}: {exc}"
            errors.append(msg)
            log.error("地域取得に失敗 %s", msg)

    return coupons, errors


def collect_listing(cfg: Config) -> tuple[list[ListingEntry], list[str]]:
    """一覧を巡回して、クーポンIDで重複排除した一覧項目を返す。

    行は「クーポン × 対象施設」で出るため、同じIDが何百行も並ぶことがある。
    表示件数もページ数も当てにできないので、
    「1ページ分に新しいIDが1件も無くなったら終わり」で止める。
    """
    from .listing import PER_PAGE, list_params

    session = new_session()
    entries: list[ListingEntry] = []
    errors: list[str] = []
    seen: set[str] = set()

    for page in range(1, cfg.listing_max_pages + 1):
        if page > 1:
            time.sleep(cfg.request_delay_sec)
        try:
            html = fetch_listing_page(session, list_params(cfg.listing_min_yen, page))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"一覧 page{page}: {exc}")
            log.error("一覧の取得に失敗 page%d: %s", page, exc)
            break

        rows = parse_listing_page(html)
        fresh = [e for e in rows if e.listing_id not in seen]
        seen.update(e.listing_id for e in rows)
        entries.extend(fresh)
        log.info("一覧 page%d: %d行 / 新しいID %d件", page, len(rows), len(fresh))

        if len(rows) < PER_PAGE or not fresh:
            break

    return dedupe(entries), errors


def fetch_detail(cfg: Config, entry: ListingEntry, session) -> list[Coupon]:
    """一覧の1件を詳細ページまで見に行く。

    施設クーポンは1ページ1本。じゃらんクーポン(CAM)は1ページに複数本入っていて、
    そちらは Phase 1 のパーサをそのまま使える。
    """
    url, html = fetch_html(session, entry.detail_url)

    if entry.kind == KIND_CAMPAIGN:
        found = parse_coupon_page(
            html,
            page_url=url,
            region_key="listing",
            region_name=entry.area_name or "一覧",
            source=SOURCE_CAMPAIGN,
        )
        for c in found:
            c.title = c.title or entry.title
            c.area_name = c.area_name or entry.area_name
            c.hotel_name = c.hotel_name or entry.hotel_name
            c.min_spend = c.min_spend or entry.min_spend
        return found

    coupon = parse_facility_page(
        html, page_url=url, listing_id=entry.listing_id, source=SOURCE_FACILITY
    )
    if coupon is None:
        return []
    coupon.title = coupon.title or entry.title
    coupon.hotel_name = coupon.hotel_name or entry.hotel_name
    coupon.area_name = coupon.area_name or entry.area_name
    coupon.min_spend = coupon.min_spend or entry.min_spend
    if not coupon.discount_label:
        coupon.discount_label = entry.discount_label
        coupon.discount_yen = entry.discount_yen
    return [coupon]


def diff_fields(before: dict, coupon: Coupon) -> list[str]:
    after = coupon.to_row()
    out: list[str] = []
    for f in HASHED_FIELDS:
        old, new = before.get(f), after.get(f)
        if str(old or "") != str(new or ""):
            out.append(f"{FIELD_LABELS.get(f, f)}: {old or '―'} → {new or '―'}")
    return out


def classify(
    coupons: list[Coupon], existing: dict[str, dict], cfg: Config
) -> tuple[list[Event], list[dict]]:
    """イベント判定と、保存する行の組み立てを行う。"""
    events: list[Event] = []
    rows: list[dict] = []
    now = _now()

    for c in coupons:
        row = c.to_row()
        prev = existing.get(c.coupon_id)

        if prev is None:
            kind = "new" if c.is_available else None
            diff: list[str] = []
        else:
            diff = diff_fields(prev, c)
            was = bool(prev.get("is_available"))
            if not was and c.is_available:
                kind = "restock"
            elif was and not c.is_available:
                kind = "soldout" if cfg.notify_on_sold_out else None
            elif diff:
                kind = "changed"
            else:
                kind = None

        changed = prev is None or bool(diff) or bool(prev.get("is_available")) != c.is_available
        row["first_seen_at"] = (prev or {}).get("first_seen_at") or now
        row["last_seen_at"] = now
        row["last_changed_at"] = now if changed else (prev or {}).get("last_changed_at") or now

        # notified_at は「実際にメールを送れたら」main 側で上書きする。
        row["notified_at"] = (prev or {}).get("notified_at")

        if kind is not None and (c.discount_yen or 0) >= cfg.min_discount_yen:
            events.append(Event(kind=kind, coupon=c, diff=diff))

        rows.append(row)

    return events, rows


def listing_pass(cfg: Config, store: Store) -> tuple[list[Coupon], list[dict], list[dict], list[str]]:
    """一覧を見て、新規・変化したものだけ詳細まで取りに行く。

    戻り値は (詳細まで取れたクーポン, 一覧の保存行, 消滅の検証記録, エラー)。
    毎周回でやるのは一覧の数リクエストだけで、詳細は動きがあった分しか叩かない。
    """
    entries, errors = collect_listing(cfg)
    if not entries:
        return [], [], [], errors

    known = store.fetch_listing()
    now = _now()
    session = new_session()

    need_detail: list[ListingEntry] = []
    listing_rows: list[dict] = []
    row_by_id: dict[str, dict] = {}

    for e in entries:
        prev = known.get(e.listing_id)
        sig = e.list_signature()
        row = e.to_row()
        row["list_signature"] = sig
        # 詳細を取れたときだけ後段で sig に更新する。取り切れなかった分は
        # 前回値（初回は null）のままにして、次の周回で必ず拾い直す。
        row["detail_signature"] = (prev or {}).get("detail_signature")
        row["first_seen_at"] = (prev or {}).get("first_seen_at") or now
        row["last_listed_at"] = now
        row["delisted_at"] = None  # 載っている＝生きている
        listing_rows.append(row)
        row_by_id[e.listing_id] = row

        if prev is None:
            need_detail.append(e)
            log.info("一覧に新規: %s %s %s", e.listing_id[:14], e.discount_label, e.hotel_name[:24])
        elif prev.get("detail_signature") != sig:
            # 内容が変わったか、前回は上限で取り切れなかったもの
            need_detail.append(e)
            log.info("詳細が未取得か内容が変化: %s %s", e.listing_id[:14], e.discount_label)
        elif prev.get("delisted_at"):
            need_detail.append(e)
            log.info("一覧に再掲載: %s %s", e.listing_id[:14], e.discount_label)

    # 高額から順に。1周で叩く詳細の本数には上限をかける
    need_detail.sort(key=lambda e: e.discount_yen or 0, reverse=True)
    if len(need_detail) > cfg.listing_max_details:
        log.warning(
            "詳細取得が %d件。上限 %d件に絞ります（残りは次の周回で拾います）",
            len(need_detail),
            cfg.listing_max_details,
        )
        need_detail = need_detail[: cfg.listing_max_details]

    coupons: list[Coupon] = []
    for i, e in enumerate(need_detail):
        if i:
            time.sleep(cfg.request_delay_sec)
        try:
            found = fetch_detail(cfg, e, session)
            coupons.extend(found)
            # ここまで来たら詳細は取れている。次の周回で取り直さないよう印を付ける。
            row_by_id[e.listing_id]["detail_signature"] = e.list_signature()
            for c in found:
                log.info(
                    "  詳細: %s %s 先着%s 配布中=%s %s",
                    c.coupon_id[:14],
                    c.discount_label,
                    c.stock,
                    c.is_available,
                    (c.hotel_name or c.region_name)[:22],
                )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"詳細 {e.listing_id}: {exc}")
            log.error("詳細の取得に失敗 %s: %s", e.listing_id, exc)

    # 一覧から消えたものを調べる。「消えた＝配布終了」かはまだ確定していないので、
    # 詳細を叩いて実際の状態を記録し、あとで仮説を検証できるようにしておく。
    present = {e.listing_id for e in entries}
    delist_rows: list[dict] = []
    gone = [
        (lid, row)
        for lid, row in known.items()
        if lid not in present and not row.get("delisted_at")
    ]
    gone.sort(key=lambda kv: kv[1].get("discount_yen") or 0, reverse=True)

    for lid, prev in gone[: cfg.listing_max_details]:
        entry = ListingEntry(listing_id=lid, kind=prev.get("kind") or KIND_FACILITY)
        still, status, note = None, "", None
        try:
            time.sleep(cfg.request_delay_sec)
            found = fetch_detail(cfg, entry, session)
            if found:
                still = any(c.is_available for c in found)
                status = found[0].status_text
                coupons.extend(found)
            else:
                note = "詳細を解析できなかった"
        except Exception as exc:  # noqa: BLE001
            note = f"詳細の取得に失敗: {exc}"
            log.warning("消滅確認に失敗 %s: %s", lid, exc)

        log.info("一覧から消滅: %s 詳細上の配布中=%s %s", lid[:14], still, status[:24])
        delist_rows.append(
            {
                "listing_id": lid,
                "kind": prev.get("kind"),
                "discount_yen": prev.get("discount_yen"),
                "checked_at": _now(),
                "still_available": still,
                "status_text": status[:200] or None,
                "note": note,
            }
        )
        listing_rows.append({**prev, "delisted_at": _now(), "last_listed_at": prev.get("last_listed_at")})

    return coupons, listing_rows, delist_rows, errors


def run_once(cfg: Config, store: Store, *, seed: bool) -> int:
    started = _now()
    coupons, errors = collect(cfg)

    # Phase 2: 一覧監視を並走させる。片方が転んでももう片方は続ける。
    listing_rows: list[dict] = []
    delist_rows: list[dict] = []
    listing_count = 0
    if cfg.watch_listing:
        try:
            found, listing_rows, delist_rows, list_errors = listing_pass(cfg, store)
            coupons.extend(found)
            errors.extend(list_errors)
            listing_count = len(found)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"一覧監視: {exc}")
            log.exception("一覧監視に失敗しました: %s", exc)

    events: list[Event] = []
    rows: list[dict] = []
    subject = ""
    problems = list(errors)

    send_failed = False
    if coupons:
        existing = store.fetch_existing([c.coupon_id for c in coupons])
        events, rows = classify(coupons, existing, cfg)

        if seed:
            log.info("--seed のため %d件の通知を抑止しました", len(events))
            events = []

        if events:
            try:
                subject = send(
                    events=events,
                    supabase_url=cfg.supabase_url,
                    supabase_key=cfg.supabase_key,
                    dry_run=cfg.dry_run,
                )
            except Exception as exc:  # noqa: BLE001
                # 送信に失敗したまま保存すると次回は差分ゼロになり取りこぼす。
                # 保存を丸ごと見送って、次の巡回で必ず再検知させる。
                send_failed = True
                problems.append(f"メール送信に失敗: {exc}")
                log.error("メール送信に失敗しました（保存を見送り、次回再検知します）: %s", exc)
            else:
                notified = {e.coupon.coupon_id for e in events}
                for row in rows:
                    if row["coupon_id"] in notified:
                        row["notified_at"] = _now()

        if not send_failed:
            store.upsert_coupons(rows)

    # 一覧側の状態は、通知の成否と関係なく残す（次周回の差分判定の土台になるため）。
    # 送信に失敗したときだけは、再検知させたいので保存を見送る。
    if not send_failed and cfg.watch_listing:
        if listing_rows:
            store.upsert_listing(listing_rows)
        if delist_rows:
            store.log_delist_checks(delist_rows)

    available = sum(1 for c in coupons if c.is_available)
    log.info(
        "結果: %d件検出 / 配布中%d件 / 通知%d件 / 失敗地域%d",
        len(coupons),
        available,
        len(events),
        len(errors),
    )
    if subject:
        log.info("送信件名: %s", subject)

    store.log_run(
        {
            "started_at": started,
            "finished_at": _now(),
            "coupons_seen": len(coupons),
            "coupons_available": available,
            "notified_count": 0 if send_failed else len(events),
            "regions_failed": len(errors),
            "error": "; ".join(problems)[:1000] or None,
        }
    )

    # 1地域も取れなかったとき、またはメール送信に失敗したときを失敗扱いにする
    return 1 if send_failed or (errors and not coupons) else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="じゃらんクーポン監視（検知・通知のみ）")
    ap.add_argument(
        "--check-mail",
        action="store_true",
        help="巡回せず、Gmail にログインできるかだけを確かめる",
    )
    ap.add_argument("--seed", action="store_true", help="通知せず現状をDBへ取り込む")
    ap.add_argument("--dry-run", action="store_true", help="メールを送らず標準出力に出す")
    ap.add_argument("--loop-from", metavar="HH:MM", help="このJST時刻まで待ってから巡回を始める")
    ap.add_argument("--loop-until", metavar="HH:MM", help="このJST時刻まで繰り返す")
    ap.add_argument("--interval-sec", type=int, default=60, help="繰り返し間隔（既定60秒）")
    ap.add_argument(
        "--max-minutes",
        type=float,
        default=150.0,
        help="待機とループを合わせた最長時間。起動が遅れたときの暴走よけ（既定150分）",
    )
    args = ap.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stdout,
    )

    cfg = Config.from_env()
    if args.dry_run:
        cfg.dry_run = True

    if args.check_mail:
        if not cfg.supabase_url or not cfg.supabase_key:
            log.error("SUPABASE_URL と SUPABASE_SERVICE_ROLE_KEY が要ります")
            return 2
        try:
            check_login(cfg.supabase_url, cfg.supabase_key)
        except Exception as exc:  # noqa: BLE001
            log.error("%s", exc)
            return 1
        log.info("notify-jalan 経由でメール送信できました")
        return 0

    missing = cfg.missing()
    if missing:
        log.error("環境変数が未設定です: %s", ", ".join(missing))
        return 2

    store = Store(cfg.supabase_url, cfg.supabase_key)

    if not args.loop_until:
        return run_once(cfg, store, seed=args.seed)

    now = datetime.now(JST)
    deadline = _at(now, args.loop_until)
    start_at = _at(now, args.loop_from) if args.loop_from else None
    if start_at and deadline <= start_at:
        deadline += timedelta(days=1)  # 日をまたぐ窓

    if now >= deadline:
        # GitHub Actions の schedule は数時間ずれることがある。翌日の窓まで待つと
        # ジョブが何時間も居座って実行時間を食い潰すので、1回だけ巡回して終える。
        log.warning(
            "起動が張り込み窓（〜%s）より後でした。1回だけ巡回して終了します",
            deadline.strftime("%m/%d %H:%M"),
        )
        return run_once(cfg, store, seed=args.seed)

    hard_stop = now + timedelta(minutes=args.max_minutes)
    if deadline > hard_stop:
        log.warning(
            "--max-minutes(%.0f分) により終了を %s に切り詰めます",
            args.max_minutes,
            hard_stop.strftime("%m/%d %H:%M"),
        )
        deadline = hard_stop

    if start_at and now < start_at:
        wait = (start_at - now).total_seconds()
        log.info("%s まで待機します（%.0f分）", start_at.strftime("%H:%M"), wait / 60)
        time.sleep(wait)

    log.info("ループ開始: %s まで %d秒間隔", deadline.isoformat(), args.interval_sec)

    rc = 0
    seed = args.seed
    while True:
        cycle_started = time.monotonic()
        try:
            rc = run_once(cfg, store, seed=seed)
        except Exception as exc:  # noqa: BLE001 - ループは1回の失敗で止めない
            log.exception("周回に失敗しました: %s", exc)
        seed = False  # seed は初回のみ
        if datetime.now(JST) >= deadline:
            break
        elapsed = time.monotonic() - cycle_started
        time.sleep(max(5.0, args.interval_sec - elapsed))
    log.info("ループ終了")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
