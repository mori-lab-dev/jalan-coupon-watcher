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

STORE: dict[str, dict] = {}
RUNS: list[dict] = []
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
        raw = (query.get("coupon_id") or [""])[0]
        print(f"[GET ] {table} coupon_id={raw[:120]}{'…' if len(raw) > 120 else ''}")

        if table != "jalan_coupons":
            return self._json(200, [])

        m = IN_RE.match(raw)
        if not m:
            print("       !! in.(...) の形になっていない")
            return self._json(200, [])
        ids = [v.strip().strip('"') for v in m.group(1).split(",") if v.strip()]
        print(f"       -> {len(ids)}件を問い合わせ / 既知 {sum(1 for i in ids if i in STORE)}件")
        self._json(200, [STORE[i] for i in ids if i in STORE])

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

        if table == "jalan_watch_runs":
            RUNS.append(rows[0])
            print(f"       -> {json.dumps(rows[0], ensure_ascii=False)}")
            return self._json(201, [])

        if "merge-duplicates" not in prefer:
            print("       !! upsert の Prefer が付いていない")
        for row in rows:
            if "coupon_id" not in row:
                print("       !! coupon_id が無い行がある")
                continue
            STORE[row["coupon_id"]] = row
        print(f"       -> 保存後の総件数 {len(STORE)}")
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
