"""件名・本文の組み立てを、代表的なパターンで確認する（ネットワーク不要）。

  .venv/bin/python scripts/check_mail_format.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jalan_watcher.notifier import Event, build_body, build_subject  # noqa: E402
from jalan_watcher.parser import Coupon  # noqa: E402


def coupon(region: str, yen: int, stock: int, available: bool, cid: str) -> Coupon:
    return Coupon(
        coupon_id=cid,
        source="jalancouponfes",
        region_key=f"button-{region}",
        region_name=region,
        campaign_id="CAM0321815",
        page_url="https://www.jalan.net/discountCoupon/CAM0321815/",
        discount_yen=yen,
        discount_label=f"{yen:,}円分",
        stock=stock,
        is_available=available,
        status_text="クーポンをGET" if available else "クーポンの配布は終了しました",
        distribute_period="2026年9月10日(木)10:00～2026年9月18日(金)23:59",
        reserve_period="2026年9月10日(木)10:00～2026年9月18日(金)23:59",
        stay_period="2026年9月10日(木)チェックイン～2027年3月4日(木)チェックアウト",
        conditions=f"【予約金額】{yen * 3 + 5000:,}円(税込)以上",
        combinable="併用可",
        target_text=f"{region}（クーポンフェス掲載宿のみ）",
    )


CASES: list[tuple[str, list[Event]]] = [
    (
        "高額1件が出現",
        [Event("new", coupon("近畿", 20000, 3, True, "COU5094634"), [])],
    ),
    (
        "複数地域で同時に出現",
        [
            Event("new", coupon("近畿", 20000, 3, True, "COU5094634")),
            Event("new", coupon("首都圏", 20000, 4, True, "COU2115543")),
            Event("new", coupon("東海", 5000, 63, True, "COU4891485")),
        ],
    ),
    (
        "配布再開",
        [Event("restock", coupon("北海道", 20000, 1, True, "COU7927856"), [])],
    ),
    (
        "内容変更のみ",
        [
            Event(
                "changed",
                coupon("東北", 5000, 39, False, "COU4910412"),
                ["先着予約数: 39 → 12"],
            )
        ],
    ),
    (
        "配布終了",
        [Event("soldout", coupon("北陸", 20000, 1, False, "COU3787469"), [])],
    ),
]


def main() -> int:
    for name, events in CASES:
        print(f"--- {name} ---")
        print(f"件名: {build_subject(events)}")
        text, body_html = build_body(events)
        for line in text.splitlines():
            if line.startswith("■"):
                print(f"  {line}")
        assert "クーポンページを開く" in body_html, "本文に直リンクが無い"
        assert "自動獲得は行いません" in text, "免責の記載が無い"
        print()
    print("すべてのパターンで件名・本文を生成できました")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
