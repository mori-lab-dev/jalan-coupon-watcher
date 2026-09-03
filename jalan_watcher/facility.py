"""施設クーポン詳細ページの解析。

クーポンフェス側（parser.py）とは構造が違う。

    parser.py（じゃらんクーポン）   facility.py（施設クーポン）
    div.couponBox                   div.couponArea
    table.couponBoxInfo__table      table.couponAreaMainInfo__table
      見出しは <td>（td/td）          見出しは <th>（th/td）
    1ページに複数本                  1ページ1本
    「使用できるプラン」なし          あり（<br>入りの派生形あり）

配布終了の判定はテーマ名に依存させない。ラッパーの class は
theme-highclass / theme-summer のように案件ごとに変わるため、
couponArea-endDistribute と、ボタンの is-disable の有無だけで見る。
"""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup, Tag

from .parser import Coupon

log = logging.getLogger(__name__)

COUPON_ID_RE = re.compile(r"COU\d+")
NUM_RE = re.compile(r"[0-9][0-9,]*")
PLAN_KEY_RE = re.compile(r"^使用できるプラン")


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


def _rows(soup: BeautifulSoup) -> dict[str, str]:
    out: dict[str, str] = {}
    for table in soup.find_all("table", class_=re.compile("couponAreaMainInfo")):
        for tr in table.find_all("tr"):
            head = tr.find("th") or tr.find("td")
            value = tr.find_all("td")
            if head is None or not value:
                continue
            # th/td が基本だが、th が無い行は td/td として扱う
            val = value[-1] if head.name == "th" else (value[1] if len(value) > 1 else None)
            if val is None:
                continue
            key = re.sub(r"\s+", "", _clean(head))
            if key:
                out[key] = _clean(val)
    return out


def parse_facility_page(
    html: str, *, page_url: str, listing_id: str = "", source: str = "listing_facility"
) -> Coupon | None:
    soup = BeautifulSoup(html, "html.parser")
    rows = _rows(soup)

    coupon_id = rows.get("クーポンID", "")
    if not COUPON_ID_RE.fullmatch(coupon_id):
        found = COUPON_ID_RE.search(html)
        coupon_id = found.group(0) if found else listing_id
    if not coupon_id:
        log.warning("クーポンIDを取れませんでした: %s", page_url)
        return None

    # 使用できるプランは「（宿・ホテル予約）」「（じゃらんパック予約）」の派生がある
    plans = [f"{k}: {v}" if k != "使用できるプラン" else v
             for k, v in rows.items() if PLAN_KEY_RE.match(k)]

    label = _clean(soup.find(class_=re.compile(r"__discount$|discount-num")))
    if not label:
        label = _clean(soup.find(class_=re.compile("js-discountNum")))
    discount_yen = _to_int(label) if "円" in label else None

    # 状態: テーマ名に依存せず endDistribute と is-disable だけで判定する
    wrapper = soup.find(
        "div",
        class_=lambda c: bool(c) and "couponArea" in c,
    )
    wrapper_classes = (wrapper.get("class") or []) if wrapper else []
    ended_by_wrapper = any("endDistribute" in c for c in wrapper_classes)

    anchor = soup.select_one("div[class*=ButtonArea] a[href*=doGetCoupon], "
                             "div[class*=ButtonArea] a.c-button")
    anchor_classes = (anchor.get("class") or []) if anchor else []
    status_text = _clean(anchor)
    ended = (
        ended_by_wrapper
        or "is-disable" in anchor_classes
        or any(w in status_text for w in ("終了", "開始前", "受付前", "予定"))
    )
    is_available = bool(anchor) and not ended

    hotel = _clean(soup.find(class_=re.compile("hotelName")))
    area = _clean(soup.find(class_=re.compile("areaName")))
    title = _clean(soup.find(class_=re.compile("js-couponTitle|MainBannerBody__title")))

    return Coupon(
        coupon_id=coupon_id,
        source=source,
        region_key="listing",
        region_name=area or "施設クーポン",
        campaign_id="",
        page_url=page_url,
        discount_yen=discount_yen,
        discount_label=label,
        stock=_to_int(rows.get("先着予約数", "")),
        is_available=is_available,
        status_text=status_text,
        distribute_period=rows.get("配布期間", ""),
        reserve_period=rows.get("予約期間", ""),
        stay_period=rows.get("宿泊対象期間", ""),
        conditions=rows.get("利用条件", ""),
        combinable=rows.get("併用", ""),
        target_text=hotel or area,
        title=title,
        hotel_name=hotel,
        area_name=area,
        usable_plans=" / ".join(plans),
        extras={k: v for k, v in rows.items()
                if k not in {"配布期間", "予約期間", "宿泊対象期間", "利用条件",
                             "先着予約数", "クーポンID", "併用"} and not PLAN_KEY_RE.match(k)},
    )
