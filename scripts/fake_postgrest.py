"""PostgREST の最低限の振る舞いを模した検証用サーバー（本番に触らず全経路を通す）。

  .venv/bin/python scripts/fake_postgrest.py --port 877
  SUPABASE_URL=http://127.0.0.1:877 SUPABASE_SERVICE_ROLE_KEY=dummy DRY_RUN=true \
    .venv/bin/python -m jalan_watcher.main

リクエストの形（in.(...) の組み立て、on_conflict、Prefer ヘッダ）を目視できるように
受け取った内容をそのまま標準出力へ出す。
"""

from __future__ import annotations

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# テーブル名 -> 主キー列。ここに無いテーブルは追記だけの記録用とみなす。
KEYED = {"jalan_coupons": "coupon_id", "jalan_listing": "listing_id"}
STORE: dict[str, dict[str, dict]] = {t: {} for t in KEYED}
APPENDED: dict[str, list[dict]] = {}
IN_RE = re.compile(r"^in\.\((.*)\)$")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args) -> None:  # デフォルトのアクセスログを止める
        pass

    def _json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        table = url.path.rsplit("/", 1)[-1]
        pk = KEYED.get(table)
        if pk is None:
            print(f"[GET ] {table} （記録用テーブル）")
            return self._json(200, [])

        rows = STORE[table]
        raw = (query.get(pk) or [""])[0]
        if not raw:  # 絞り込みなし＝全件（jalan_listing はこの形で読む）
            print(f"[GET ] {table} 全件 -> {len(rows)}件")
            return self._json(200, list(rows.values()))

        print(f"[GET ] {table} {pk}={raw[:110]}{'…' if len(raw) > 110 else ''}")
        m = IN_RE.match(raw)
        if not m:
            print("       !! in.(...) の形になっていない")
            return self._json(200, [])
        ids = [v.strip().strip('"') for v in m.group(1).split(",") if v.strip()]
        print(f"       -> {len(ids)}件を問い合わせ / 既知 {sum(1 for i in ids if i in rows)}件")
        self._json(200, [rows[i] for i in ids if i in rows])

    def do_POST(self) -> None:  # noqa: N802
        url = urlparse(self.path)
        query = parse_qs(url.query)
        table = url.path.rsplit("/", 1)[-1]
        length = int(self.headers.get("Content-Length") or 0)
        rows = json.loads(self.rfile.read(length) or b"[]")
        prefer = self.headers.get("Prefer", "")
        print(
            f"[POST] {table} rows={len(rows)} "
            f"on_conflict={query.get('on_conflict', ['-'])[0]} Prefer={prefer}"
        )

        pk = KEYED.get(table)
        if pk is None:  # 追記だけの記録用（jalan_watch_runs / jalan_delist_checks）
            APPENDED.setdefault(table, []).extend(rows)
            for r in rows:
                print(f"       -> {json.dumps(r, ensure_ascii=False)[:220]}")
            return self._json(201, [])

        if "merge-duplicates" not in prefer:
            print("       !! upsert の Prefer が付いていない")
        for row in rows:
            if pk not in row:
                print(f"       !! {pk} が無い行がある")
                continue
            STORE[table][row[pk]] = row
        print(f"       -> {table} 保存後の総件数 {len(STORE[table])}")
        self._json(201, [])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=877)
    args = ap.parse_args()
    print(f"疑似 PostgREST を http://127.0.0.1:{args.port} で起動")
    HTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
