"""利用条件から予約金額を読む処理を、実際に出てくる書き方で確かめる。

  .venv/bin/python scripts/check_min_spend.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jalan_watcher.parser import extract_min_spend_yen  # noqa: E402

CASES: list[tuple[str, int | None]] = [
    ("【予約金額】300,000円(税込)以上", 300000),
    ("【予約金額】260,000円(税込)以上【大人人数】5名以上", 260000),
    ("【予約金額】210,000円(税込)以上【大人人数】2名以上2名以下", 210000),
    ("【予約金額】65,000円(税込)以上", 65000),
    (
        "宿・ホテル予約： 【予約金額】300,000円(税込)以上"
        " じゃらんパック予約： 【合計旅行代金】300,000円(税込)以上",
        300000,
    ),
    # 実例（PGMホテルリゾート沖縄）。宿側10万円が採られること
    (
        "宿・ホテル予約： 【予約金額】100,000円(税込)以上【泊数】2泊以上"
        " じゃらんパック予約： 【合計旅行代金】150,000円(税込)以上",
        100000,
    ),
    # 宿側が安く、パック側が高い。安いほうを採る
    (
        "宿・ホテル予約： 【予約金額】74,500円(税込)以上【大人人数】6名以上6名以下"
        " じゃらんパック予約： 【合計旅行代金】223,500円(税込)以上",
        74500,
    ),
    # パック側しか書かれていない場合はそちらを使う
    ("じゃらんパック予約： 【合計旅行代金】150,000円(税込)以上", 150000),
    # 同種が複数あるときは安いほうを採る
    ("【予約金額】80,000円(税込)以上 【予約金額】50,000円(税込)以上", 50000),
    ("なし", None),
    ("", None),
    ("【大人人数】2名以上", None),
]


def main() -> int:
    ng = 0
    for text, want in CASES:
        got = extract_min_spend_yen(text)
        mark = "OK " if got == want else "NG "
        if got != want:
            ng += 1
        shown = f"{got:,}" if got is not None else "条件不明"
        print(f"  {mark} {shown:>10}  <- {text[:66]}")
    print()
    if ng:
        print(f"{ng}件ずれています")
        return 1
    print(f"{len(CASES)}件すべて期待どおりでした")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
