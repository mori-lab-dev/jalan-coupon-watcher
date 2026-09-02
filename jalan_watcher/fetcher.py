"""じゃらんへの読み取り専用アクセス。

このモジュールは GET しか行わない。ログイン・クーポン獲得（doGetCoupon 相当の
POST）は じゃらんnet宿泊予約規約 第5条6項12号 に抵触するため実装しない。
"""

from __future__ import annotations

import logging
import time

import requests

from .config import COUPON_JSON_URL, USER_AGENT

log = logging.getLogger(__name__)

TIMEOUT = 20
RETRIES = 3


def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": USER_AGENT,
            "Accept-Language": "ja,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
        }
    )
    return s


def _get(session: requests.Session, url: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(1, RETRIES + 1):
        try:
            resp = session.get(url, timeout=TIMEOUT, allow_redirects=True)
            resp.raise_for_status()
            return resp
        except Exception as exc:  # noqa: BLE001 - 上位でまとめて扱う
            last = exc
            log.warning("GET 失敗 (%d/%d) %s: %s", attempt, RETRIES, url, exc)
            if attempt < RETRIES:
                time.sleep(2 * attempt)
    raise RuntimeError(f"GET に {RETRIES} 回失敗しました: {url}") from last


def fetch_region_links(session: requests.Session) -> dict[str, str]:
    """coupon.json から 地域キー -> リンクURL を取得する。"""
    resp = _get(session, COUPON_JSON_URL)
    data = resp.json()
    if not isinstance(data, dict):
        raise RuntimeError(f"coupon.json の形式が想定外です: {type(data)}")
    return {str(k): str(v) for k, v in data.items() if v}


def fetch_html(session: requests.Session, url: str) -> tuple[str, str]:
    """リダイレクトを追跡して HTML を取得し、(最終URL, UTF-8文字列) を返す。

    じゃらんは Shift-JIS(Windows-31J) を返すため、requests の推測に任せず
    cp932 で明示的にデコードする。
    """
    resp = _get(session, url)
    text = resp.content.decode("cp932", errors="replace")
    return resp.url, text
