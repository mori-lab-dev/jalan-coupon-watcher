"""クーポン一覧の取得と解析。

一覧は「クーポン1件 = 1行」ではなく「クーポン × 対象施設」で行が出る。
複数施設にまたがるクーポンは参加施設の数だけ重複するため、
必ずクーポンIDで重複排除してから扱う。表示される総件数とページ数は当てにしない。

一覧が持っているのは 対象宿 / エリア / 各期間 / 予約金額 / クーポン額 だけで、
先着予約数と配布状態は入っていない。それらは詳細ページ側にしかない。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

log = logging.getLogger(__name__)

LIST_URL = "https://www.jalan.net/uw/uwp7800/uww7861.do"
FACILITY_DETAIL = "https://www.jalan.net/uw/uwp7800/uww7830.do?discountCouponId={id}"
CAMPAIGN_DETAIL = "https://www.jalan.net/discountCoupon/{id}/"

PER_PAGE = 30
FACILITY_RE = re.compile(r"doPromotionDtl\('([^']+)'\s*,\s*'([^']*)'\)")
CAMPAIGN_RE = re.compile(r"doCouponDtl\('([^']+)'\)")
NUM_RE = re.compile(r"[0-9][0-9,]*")

KIND_FACILITY = "facility"
KIND_CAMPAIGN = "campaign"

# 一覧の行が変わったら詳細を取り直す対象
LIST_HASHED = ("discount_label", "distribute_period", "reserve_period", "stay_period", "min_spend")


def list_params(min_yen: int, page: int) -> dict[str, str]:
    return {
        "screenId": "UWW7861",
        "searchType": "2",
        "stayCount": "1",
        "activeSort": "2",  # クーポン額の降順
        "couponPriceMin": str(min_yen),
        "couponPriceMax": "999999",
        "priceMax": "999999",
        "pageIdx": str(page),
    }


@dataclass
class ListingEntry:
    listing_id: str  # 施設型は COU（24桁）、キャンペーン型は CAM
    kind: str
    title: str = ""
    hotel_name: str = ""
    area_name: str = ""
    distribute_period: str = ""
    reserve_period: str = ""
    stay_period: str = ""
    min_spend: str = ""
    discount_label: str = ""
    discount_yen: int | None = None
    hotel_id: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def detail_url(self) -> str:
        tmpl = FACILITY_DETAIL if self.kind == KIND_FACILITY else CAMPAIGN_DETAIL
        return tmpl.format(id=self.listing_id)

    def to_row(self) -> dict:
        return {
            "listing_id": self.listing_id,
            "kind": self.kind,
            "title": self.title,
            "hotel_name": self.hotel_name,
            "area_name": self.area_name,
            "distribute_period": self.distribute_period,
            "reserve_period": self.reserve_period,
            "stay_period": self.stay_period,
            "min_spend": self.min_spend,
            "discount_label": self.discount_label,
            "discount_yen": self.discount_yen,
            "detail_url": self.detail_url,
        }

    def list_signature(self) -> str:
        return "|".join(f"{k}={getattr(self, k)!r}" for k in LIST_HASHED)


def _clean(node: Tag | None) -> str:
    if node is None:
        return ""
    copy = BeautifulSoup(str(node), "html.parser")
    for br in copy.find_all("br"):
        br.decompose()
    text = re.sub(r"\s+", " ", copy.get_text(" ", strip=True)).strip()
    return text.replace("～ ", "～")


def _to_int(text: str) -> int | None:
    m = NUM_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def parse_listing_page(html: str) -> list[ListingEntry]:
    soup = BeautifulSoup(html, "html.parser")
    entries: list[ListingEntry] = []

    for item in soup.find_all("li", class_="item"):
        raw = str(item)
        kind, listing_id, hotel_id = "", "", ""
        m = FACILITY_RE.search(raw)
        if m:
            kind, listing_id, hotel_id = KIND_FACILITY, m.group(1), m.group(2)
        else:
            m = CAMPAIGN_RE.search(raw)
            if m:
                kind, listing_id = KIND_CAMPAIGN, m.group(1)
        if not listing_id:
            continue

        detail: dict[str, str] = {}
        for dl in item.find_all("dl"):
            dts, dds = dl.find_all("dt"), dl.find_all("dd")
            for dt, dd in zip(dts, dds):
                key = re.sub(r"\s+", "", _clean(dt))
                if key:
                    detail[key] = _clean(dd)

        amount = detail.get("クーポン額", "")
        entries.append(
            ListingEntry(
                listing_id=listing_id,
                kind=kind,
                title=_clean(item.find(class_=re.compile("item-title"))),
                hotel_name=detail.get("対象宿", ""),
                area_name=detail.get("エリア", ""),
                distribute_period=detail.get("配布期間", ""),
                reserve_period=detail.get("予約期間", ""),
                stay_period=detail.get("宿泊対象期間", ""),
                min_spend=detail.get("予約金額", ""),
                discount_label=amount,
                discount_yen=_to_int(amount) if "円" in amount else None,
                hotel_id=hotel_id,
                extras={
                    k: v
                    for k, v in detail.items()
                    if k
                    not in {"対象宿", "エリア", "配布期間", "予約期間", "宿泊対象期間",
                            "予約金額", "クーポン額"}
                },
            )
        )
    return entries


def dedupe(entries: list[ListingEntry]) -> list[ListingEntry]:
    """クーポンIDで重複排除する。行は施設ごとに増えるので必須。"""
    seen: dict[str, ListingEntry] = {}
    for e in entries:
        seen.setdefault(e.listing_id, e)
    return list(seen.values())
