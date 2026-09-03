"""Supabase(PostgREST)への状態保存。service_role キーで RLS をバイパスする。"""

from __future__ import annotations

import logging

import requests

log = logging.getLogger(__name__)

TIMEOUT = 20
COUPON_TABLE = "jalan_coupons"
RUN_TABLE = "jalan_watch_runs"
LISTING_TABLE = "jalan_listing"
DELIST_TABLE = "jalan_delist_checks"
CHUNK = 50


class Store:
    def __init__(self, base_url: str, service_key: str) -> None:
        self.rest = f"{base_url.rstrip('/')}/rest/v1"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": service_key,
                "Authorization": f"Bearer {service_key}",
                "Content-Type": "application/json",
            }
        )

    def fetch_existing(self, coupon_ids: list[str]) -> dict[str, dict]:
        """既知クーポンを coupon_id -> 行 で返す。"""
        found: dict[str, dict] = {}
        for i in range(0, len(coupon_ids), CHUNK):
            chunk = coupon_ids[i : i + CHUNK]
            quoted = ",".join(f'"{cid}"' for cid in chunk)
            resp = self.session.get(
                f"{self.rest}/{COUPON_TABLE}",
                params={"select": "*", "coupon_id": f"in.({quoted})"},
                timeout=TIMEOUT,
            )
            resp.raise_for_status()
            for row in resp.json():
                found[row["coupon_id"]] = row
        return found

    def upsert_coupons(self, rows: list[dict]) -> None:
        if not rows:
            return
        for i in range(0, len(rows), CHUNK):
            resp = self.session.post(
                f"{self.rest}/{COUPON_TABLE}",
                params={"on_conflict": "coupon_id"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=rows[i : i + CHUNK],
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"upsert 失敗 {resp.status_code}: {resp.text[:500]}")

    def fetch_listing(self) -> dict[str, dict]:
        """一覧の既知分を listing_id -> 行 で返す。件数が少ないので全件取る。"""
        resp = self.session.get(
            f"{self.rest}/{LISTING_TABLE}",
            params={"select": "*"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        return {row["listing_id"]: row for row in resp.json()}

    def upsert_listing(self, rows: list[dict]) -> None:
        if not rows:
            return
        for i in range(0, len(rows), CHUNK):
            resp = self.session.post(
                f"{self.rest}/{LISTING_TABLE}",
                params={"on_conflict": "listing_id"},
                headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
                json=rows[i : i + CHUNK],
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                raise RuntimeError(f"listing upsert 失敗 {resp.status_code}: {resp.text[:500]}")

    def log_delist_checks(self, rows: list[dict]) -> None:
        """「一覧から消えた＝終了か」の検証データ。失敗しても本処理は止めない。"""
        if not rows:
            return
        try:
            resp = self.session.post(
                f"{self.rest}/{DELIST_TABLE}",
                headers={"Prefer": "return=minimal"},
                json=rows,
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                log.warning("消滅記録に失敗 %s: %s", resp.status_code, resp.text[:300])
        except Exception as exc:  # noqa: BLE001
            log.warning("消滅記録に失敗: %s", exc)

    def log_run(self, payload: dict) -> None:
        """実行ログ。失敗しても本処理は落とさない。"""
        try:
            resp = self.session.post(
                f"{self.rest}/{RUN_TABLE}",
                headers={"Prefer": "return=minimal"},
                json=[payload],
                timeout=TIMEOUT,
            )
            if resp.status_code >= 400:
                log.warning("実行ログの記録に失敗 %s: %s", resp.status_code, resp.text[:300])
        except Exception as exc:  # noqa: BLE001
            log.warning("実行ログの記録に失敗: %s", exc)
