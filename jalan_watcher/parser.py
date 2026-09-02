"""discountCoupon ページの HTML からクーポン情報を取り出す。

1ページに複数のクーポンが並ぶ。1件ぶんの単位は

    <div class="couponBox ">                     … 配布終了時は couponBox-endDistribute が付く
      <div class="couponBox__couponWrapper">
        <h4 class="couponUseTarget">近畿（…）</h4>
        <div class="couponBoxMain">              … 割引額・先着予約数・併用ラベル
        <table class="couponBoxInfo__table">     … 配布期間/予約期間/宿泊対象期間/利用条件/クーポンID/併用
        <div class="couponBoxInfoButtonArea">    … 「クーポンをGET」or「配布は終了しました」
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field

from bs4 import BeautifulSoup, Tag

CAMPAIGN_RE = re.compile(r"/discountCoupon/(CAM\d+)")
COUPON_ID_RE = re.compile(r"COU\d+")
STOCK_RE = re.compile(r"先着予約数\s*([0-9,]+)")
NUM_RE = re.compile(r"[0-9][0-9,]*")

# content_hash の対象。変化したら「内容が変わった」とみなすフィールド。
HASHED_FIELDS = (
    "discount_label",
    "stock",
    "distribute_period",
    "reserve_period",
    "stay_period",
    "conditions",
    "combinable",
    "target_text",
)


@dataclass
class Coupon:
    coupon_id: str
    source: str
    region_key: str
    region_name: str
    campaign_id: str
    page_url: str
    discount_yen: int | None = None
    discount_label: str = ""
    stock: int | None = None
    is_available: bool = False
    status_text: str = ""
    distribute_period: str = ""
    reserve_period: str = ""
    stay_period: str = ""
    conditions: str = ""
    combinable: str = ""
    target_text: str = ""
    extras: dict[str, str] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        payload = "|".join(f"{k}={asdict(self).get(k)!r}" for k in HASHED_FIELDS)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_row(self) -> dict:
        return {
            "coupon_id": self.coupon_id,
            "source": self.source,
            "region_key": self.region_key,
            "region_name": self.region_name,
            "campaign_id": self.campaign_id,
            "page_url": self.page_url,
            "discount_yen": self.discount_yen,
            "discount_label": self.discount_label,
            "stock": self.stock,
            "is_available": self.is_available,
            "status_text": self.status_text,
            "distribute_period": self.distribute_period,
            "reserve_period": self.reserve_period,
            "stay_period": self.stay_period,
            "conditions": self.conditions,
            "combinable": self.combinable,
            "target_text": self.target_text,
            "content_hash": self.content_hash,
        }


def _clean(node: Tag | None) -> str:
    """<br> を取り除いたうえでテキスト化し、空白を1つに詰める。"""
    if node is None:
        return ""
    copy = BeautifulSoup(str(node), "html.parser")
    for br in copy.find_all("br"):
        br.decompose()
    text = copy.get_text(" ", strip=True)
    text = re.sub(r"\s+", " ", text).strip()
    # 「10:00～<br>2026年…」が「10:00～ 2026年…」になるので詰める
    return text.replace("～ ", "～")


def _label(text: str) -> str:
    """見出しセルは「宿泊<br>対象期間」のように割れるので空白を全部落とす。"""
    return re.sub(r"\s+", "", text)


def _to_int(text: str) -> int | None:
    m = NUM_RE.search(text or "")
    if not m:
        return None
    try:
        return int(m.group(0).replace(",", ""))
    except ValueError:
        return None


def _parse_table(table: Tag | None) -> dict[str, str]:
    rows: dict[str, str] = {}
    if table is None:
        return rows
    for tr in table.find_all("tr"):
        cells = tr.find_all("td", recursive=False) or tr.find_all("td")
        if len(cells) < 2:
            continue
        key = _label(_clean(cells[0]))
        if key:
            rows[key] = _clean(cells[1])
    return rows


def parse_coupon_page(
    html: str, *, page_url: str, region_key: str, region_name: str, source: str
) -> list[Coupon]:
    soup = BeautifulSoup(html, "html.parser")
    m = CAMPAIGN_RE.search(page_url)
    campaign_id = m.group(1) if m else ""

    coupons: list[Coupon] = []
    for index, box in enumerate(soup.find_all("div", class_="couponBox")):
        main = box.find("div", class_="couponBoxMain")
        table = box.find("table", class_="couponBoxInfo__table")
        if main is None and table is None:
            continue

        rows = _parse_table(table)

        # --- クーポンID（無ければ doGetCoupon('COUxxxx') から拾う） ---
        coupon_id = rows.get("クーポンID", "")
        if not COUPON_ID_RE.fullmatch(coupon_id):
            found = COUPON_ID_RE.search(str(box))
            coupon_id = found.group(0) if found else ""
        if not coupon_id:
            # 最後の手段。IDが取れなくても取りこぼさないための合成キー。
            coupon_id = f"{campaign_id or region_key}-{index}"

        # --- 割引額 ---
        num = _clean(main.find("span", class_="couponBoxMainBannerText__discount")) if main else ""
        unit = _clean(main.find("span", class_="couponBoxMainBannerText__currency")) if main else ""
        discount_label = f"{num}{unit}".strip()
        discount_yen = _to_int(num) if "円" in unit else None

        # --- 先着予約数（表に無ければ couponBoxMain のテキストから） ---
        stock = _to_int(rows["先着予約数"]) if "先着予約数" in rows else None
        if stock is None and main is not None:
            sm = STOCK_RE.search(_clean(main))
            if sm:
                stock = _to_int(sm.group(1))

        # --- 配布状態 ---
        button = box.find("div", class_="couponBoxInfoButtonArea")
        anchor = button.find("a") if button else None
        status_text = _clean(anchor)
        box_classes = box.get("class") or []
        anchor_classes = (anchor.get("class") or []) if anchor else []
        # class が第一の根拠。文言は将来変わりうるので保険としてだけ見る。
        ended = (
            "couponBox-endDistribute" in box_classes
            or "is-disable" in anchor_classes
            or any(w in status_text for w in ("終了", "開始前", "受付前", "予定"))
        )
        is_available = bool(anchor) and not ended

        extras = {
            k: v
            for k, v in rows.items()
            if k
            not in {"配布期間", "予約期間", "宿泊対象期間", "利用条件", "クーポンID", "併用", "先着予約数"}
        }

        coupons.append(
            Coupon(
                coupon_id=coupon_id,
                source=source,
                region_key=region_key,
                region_name=region_name,
                campaign_id=campaign_id,
                page_url=page_url,
                discount_yen=discount_yen,
                discount_label=discount_label,
                stock=stock,
                is_available=is_available,
                status_text=status_text,
                distribute_period=rows.get("配布期間", ""),
                reserve_period=rows.get("予約期間", ""),
                stay_period=rows.get("宿泊対象期間", ""),
                conditions=rows.get("利用条件", ""),
                combinable=rows.get("併用", ""),
                target_text=_clean(box.find("h4", class_="couponUseTarget")),
                extras=extras,
            )
        )
    return coupons
