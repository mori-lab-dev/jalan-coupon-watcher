"""環境変数から設定を読む。認証情報は一切ハードコードしない。"""

from __future__ import annotations

import os
from dataclasses import dataclass

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

COUPON_JSON_URL = "https://www.jalan.net/theme/jalancouponfes/data/coupon.json"

# coupon.json のキー -> 日本語の地域名。未知のキーはキー名をそのまま使う。
REGION_NAMES = {
    "button-hokkaido": "北海道",
    "button-tohoku": "東北",
    "button-kitakanto": "北関東",
    "button-shutoken": "首都圏",
    "button-tokai": "東海",
    "button-koshinetsu": "甲信越",
    "button-hokuriku": "北陸",
    "button-kinki": "近畿",
    "button-saninsanyoshikoku": "山陰山陽四国",
    "button-kyushuokinawa": "九州沖縄",
}


def region_name(key: str) -> str:
    return REGION_NAMES.get(key, key.removeprefix("button-"))


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass
class Config:
    supabase_url: str = ""
    supabase_key: str = ""
    min_discount_yen: int = 0
    notify_on_sold_out: bool = False
    dry_run: bool = False
    request_delay_sec: float = 1.5
    # 一覧監視（Phase 2）。クーポンフェスの10地域巡回とは独立に並走させる
    watch_listing: bool = True
    listing_min_yen: int = 30000
    listing_max_pages: int = 4
    listing_max_details: int = 12

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
            min_discount_yen=_int("MIN_DISCOUNT_YEN", 0),
            notify_on_sold_out=_bool("NOTIFY_ON_SOLD_OUT", False),
            dry_run=_bool("DRY_RUN", False),
            request_delay_sec=_float("REQUEST_DELAY_SEC", 1.5),
            watch_listing=_bool("WATCH_LISTING", True),
            listing_min_yen=_int("LISTING_MIN_YEN", 30000),
            listing_max_pages=_int("LISTING_MAX_PAGES", 4),
            listing_max_details=_int("LISTING_MAX_DETAILS", 12),
        )

    def missing(self) -> list[str]:
        """未設定の必須項目を返す。

        Gmail認証情報(GMAIL_USER/GMAIL_APP_PASSWORD/MAIL_TO)は Edge Function
        (notify-jalan)側のシークレットに一本化したため、ここでは持たない。
        2026-09-02: GitHub Actions側にGMAIL_USERが誤って別アカウントの
        アドレスのまま設定されており、常に535 Bad Credentialsになっていた
        不具合の再発防止（認証情報を1箇所に集約する）。
        """
        missing: list[str] = []
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_key:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        return missing
