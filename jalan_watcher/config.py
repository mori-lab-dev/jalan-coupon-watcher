"""環境変数から設定を読む。認証情報は一切ハードコードしない。"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

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
    gmail_user: str = ""
    gmail_password: str = ""
    mail_to: list[str] = field(default_factory=list)
    min_discount_yen: int = 0
    notify_on_sold_out: bool = False
    dry_run: bool = False
    request_delay_sec: float = 1.5

    @classmethod
    def from_env(cls) -> "Config":
        gmail_user = os.environ.get("GMAIL_USER", "").strip()
        raw_to = os.environ.get("MAIL_TO", "").strip()
        mail_to = [a.strip() for a in raw_to.split(",") if a.strip()] or (
            [gmail_user] if gmail_user else []
        )
        return cls(
            supabase_url=os.environ.get("SUPABASE_URL", "").strip().rstrip("/"),
            supabase_key=os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip(),
            gmail_user=gmail_user,
            # アプリパスワードは英数16文字。Googleの画面は "abcd efgh ijkl mnop" と
            # 4桁ずつ空けて表示するうえ、Secret 登録時に改行が紛れ込むこともある。
            # そのまま渡すと 535 BadCredentials になるので空白類を全部落とす。
            gmail_password=re.sub(r"\s+", "", os.environ.get("GMAIL_APP_PASSWORD", "")),
            mail_to=mail_to,
            min_discount_yen=_int("MIN_DISCOUNT_YEN", 0),
            notify_on_sold_out=_bool("NOTIFY_ON_SOLD_OUT", False),
            dry_run=_bool("DRY_RUN", False),
            request_delay_sec=_float("REQUEST_DELAY_SEC", 1.5),
        )

    def missing(self) -> list[str]:
        """未設定の必須項目を返す。DRY_RUN 時はメール設定を必須にしない。"""
        missing: list[str] = []
        if not self.supabase_url:
            missing.append("SUPABASE_URL")
        if not self.supabase_key:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        if not self.dry_run:
            if not self.gmail_user:
                missing.append("GMAIL_USER")
            if not self.gmail_password:
                missing.append("GMAIL_APP_PASSWORD")
            if not self.mail_to:
                missing.append("MAIL_TO")
        return missing
