"""Supabase/Gmail なしで、実際に全地域を巡回して結果とメール本文を確認する。

  .venv/bin/python scripts/preview.py
  .venv/bin/python scripts/preview.py --available-only
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jalan_watcher.config import Config  # noqa: E402
from jalan_watcher.main import collect  # noqa: E402
from jalan_watcher.notifier import Event, build_body, build_subject  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--available-only", action="store_true", help="配布中のものだけメール化")
    ap.add_argument("--delay", type=float, default=1.5)
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stdout)

    cfg = Config.from_env()
    cfg.request_delay_sec = args.delay
    coupons, errors = collect(cfg)

    print()
    print(f"=== 検出 {len(coupons)}件 / 失敗地域 {len(errors)} ===")
    for c in sorted(coupons, key=lambda x: (x.region_name, -(x.discount_yen or 0))):
        mark = "配布中" if c.is_available else "終了  "
        stock = f"先着{c.stock}" if c.stock is not None else "先着?"
        print(
            f"  [{mark}] {c.region_name:<8} {c.discount_label:>10} {stock:>8} "
            f"{c.coupon_id} {c.conditions}"
        )
    for e in errors:
        print(f"  [失敗] {e}")

    targets = [c for c in coupons if c.is_available] if args.available_only else coupons
    if not targets:
        print("\nメール対象なし")
        return 0

    events = [Event(kind="new", coupon=c, diff=[]) for c in targets]
    text, _ = build_body(events)
    print("\n=== 件名 ===")
    print(build_subject(events))
    print("\n=== 本文(text) ===")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
