"""保存済み HTML に対してパーサを流す動作確認用スクリプト（ネットワーク不要）。

  python scripts/parse_local.py path/to/page.html --url https://www.jalan.net/discountCoupon/CAM0321815/
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jalan_watcher.parser import parse_coupon_page  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--url", default="https://www.jalan.net/discountCoupon/CAM0000000/")
    ap.add_argument("--encoding", default="cp932")
    ap.add_argument("--region-key", default="button-kinki")
    ap.add_argument("--region-name", default="近畿")
    ap.add_argument("--source", default="jalancouponfes")
    args = ap.parse_args()

    html = Path(args.path).read_bytes().decode(args.encoding, errors="replace")
    coupons = parse_coupon_page(
        html,
        page_url=args.url,
        region_key=args.region_key,
        region_name=args.region_name,
        source=args.source,
    )

    print(f"検出クーポン数: {len(coupons)}")
    for c in coupons:
        row = c.to_row()
        row["extras"] = c.extras
        print(json.dumps(row, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
