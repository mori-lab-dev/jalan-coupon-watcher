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
from .fetcher import fetch_html, fetch_region_links, new_session
from .notifier import Event, check_login, send
from .parser import HASHED_FIELDS, Coupon, parse_coupon_page
from .store import Store

JST = timezone(timedelta(hours=9))
SOURCE = "jalancouponfes"

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


def run_once(cfg: Config, store: Store, *, seed: bool) -> int:
    started = _now()
    coupons, errors = collect(cfg)

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
                    gmail_user=cfg.gmail_user,
                    gmail_password=cfg.gmail_password,
                    mail_to=cfg.mail_to,
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
        if not cfg.gmail_user or not cfg.gmail_password:
            log.error("GMAIL_USER と GMAIL_APP_PASSWORD が要ります")
            return 2
        log.info("送信元 %s / パスワード長 %d文字", cfg.gmail_user, len(cfg.gmail_password))
        try:
            check_login(cfg.gmail_user, cfg.gmail_password)
        except Exception as exc:  # noqa: BLE001
            log.error("%s", exc)
            return 1
        log.info("Gmail にログインできました")
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
