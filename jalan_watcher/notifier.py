"""Gmail SMTP でのメール通知。

件名だけで内容が分かるように「地域・割引額・先着数」を先頭に置く。本文には
クーポンページへの直リンクを入れるが、「獲得する」操作は必ず利用者本人が
手動で行う（自動獲得は規約違反のため実装しない）。
"""

from __future__ import annotations

import html
import logging
import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage

from .parser import Coupon

log = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

KIND_LABEL = {
    "new": "新規",
    "restock": "配布再開",
    "changed": "内容変更",
    "soldout": "配布終了",
}


@dataclass
class Event:
    kind: str  # new / restock / changed / soldout
    coupon: Coupon
    diff: list[str] = field(default_factory=list)

    @property
    def is_gettable(self) -> bool:
        return self.kind in {"new", "restock"} and self.coupon.is_available


def _amount(c: Coupon) -> str:
    return c.discount_label or (f"{c.discount_yen:,}円分" if c.discount_yen else "金額不明")


def _stock(c: Coupon) -> str:
    return f"先着{c.stock}" if c.stock is not None else "先着数不明"


def build_subject(events: list[Event]) -> str:
    gettable = [e for e in events if e.is_gettable]
    target = gettable or events
    target = sorted(target, key=lambda e: e.coupon.discount_yen or 0, reverse=True)
    head = target[0]
    c = head.coupon

    if len(target) == 1:
        if head.is_gettable:
            return f"【{c.region_name}】{_amount(c)}クーポン出現（{_stock(c)}）"
        return f"【{c.region_name}】{_amount(c)}クーポン{KIND_LABEL.get(head.kind, head.kind)}"

    regions = []
    for e in target:
        if e.coupon.region_name not in regions:
            regions.append(e.coupon.region_name)
    region_part = regions[0] + ("ほか" if len(regions) > 1 else "")
    verb = "出現" if gettable else KIND_LABEL.get(head.kind, "更新")
    return f"【じゃらん】最大{_amount(c)}クーポン{verb}（{region_part}計{len(target)}件）"


def _lines_for(e: Event) -> list[str]:
    c = e.coupon
    state = "配布中（獲得可）" if c.is_available else "配布終了"
    lines = [
        f"■ [{KIND_LABEL.get(e.kind, e.kind)}] {c.region_name} / {_amount(c)} / {_stock(c)}",
        f"   状態     : {state}",
        f"   利用条件 : {c.conditions or '記載なし'}",
        f"   配布期間 : {c.distribute_period or '記載なし'}",
        f"   予約期間 : {c.reserve_period or '記載なし'}",
        f"   宿泊対象 : {c.stay_period or '記載なし'}",
        f"   併用     : {c.combinable or '記載なし'}",
        f"   対象     : {c.target_text or '記載なし'}",
        f"   クーポンID: {c.coupon_id}",
        f"   ページ   : {c.page_url}",
    ]
    if e.diff:
        lines.append(f"   変化     : {' / '.join(e.diff)}")
    return lines


def build_body(events: list[Event]) -> tuple[str, str]:
    ordered = sorted(
        events,
        key=lambda e: (not e.is_gettable, -(e.coupon.discount_yen or 0)),
    )

    text_parts = ["じゃらんクーポンに動きがありました。", ""]
    for e in ordered:
        text_parts.extend(_lines_for(e))
        text_parts.append("")
    text_parts.append("※「獲得する」操作はページを開いてご自身で行ってください。")
    text_parts.append("※このシステムは検知と通知のみを行います（自動獲得は行いません）。")
    text = "\n".join(text_parts)

    rows = []
    for e in ordered:
        c = e.coupon
        state = "配布中（獲得可）" if c.is_available else "配布終了"
        badge = "#d93025" if e.is_gettable else "#5f6368"
        diff_html = (
            f"<div style='color:#b06000;font-size:13px'>変化: {html.escape(' / '.join(e.diff))}</div>"
            if e.diff
            else ""
        )
        rows.append(
            f"""
<div style="border:1px solid #ddd;border-radius:8px;padding:12px;margin:0 0 12px">
  <div style="font-size:13px;color:{badge};font-weight:bold">
    [{html.escape(KIND_LABEL.get(e.kind, e.kind))}] {html.escape(c.region_name)}
  </div>
  <div style="font-size:22px;font-weight:bold;margin:4px 0">
    {html.escape(_amount(c))} <span style="font-size:14px;font-weight:normal">/ {html.escape(_stock(c))}</span>
  </div>
  <div style="font-size:14px;margin-bottom:6px">状態: {html.escape(state)}</div>
  <div style="font-size:13px;line-height:1.7">
    利用条件: {html.escape(c.conditions or '記載なし')}<br>
    配布期間: {html.escape(c.distribute_period or '記載なし')}<br>
    予約期間: {html.escape(c.reserve_period or '記載なし')}<br>
    宿泊対象: {html.escape(c.stay_period or '記載なし')}<br>
    併用: {html.escape(c.combinable or '記載なし')}<br>
    対象: {html.escape(c.target_text or '記載なし')}<br>
    クーポンID: {html.escape(c.coupon_id)}
  </div>
  {diff_html}
  <div style="margin-top:10px">
    <a href="{html.escape(c.page_url)}"
       style="display:inline-block;background:#ff5a1f;color:#fff;text-decoration:none;
              padding:10px 16px;border-radius:6px;font-weight:bold">クーポンページを開く</a>
  </div>
</div>"""
        )

    body_html = f"""<div style="font-family:-apple-system,'Hiragino Sans',sans-serif;max-width:640px">
<p style="font-size:15px">じゃらんクーポンに動きがありました。</p>
{''.join(rows)}
<p style="font-size:12px;color:#666">
※「獲得する」操作はページを開いてご自身で行ってください。<br>
※このシステムは検知と通知のみを行います（自動獲得は行いません）。
</p>
</div>"""
    return text, body_html


def send(
    *,
    events: list[Event],
    gmail_user: str,
    gmail_password: str,
    mail_to: list[str],
    dry_run: bool = False,
) -> str:
    subject = build_subject(events)
    text, body_html = build_body(events)

    if dry_run:
        print("=== DRY_RUN: 送信せず表示のみ ===")
        print(f"Subject: {subject}")
        print(text)
        return subject

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = gmail_user
    msg["To"] = ", ".join(mail_to)
    msg.set_content(text)
    msg.add_alternative(body_html, subtype="html")

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        smtp.login(gmail_user, gmail_password)
        smtp.send_message(msg)
    log.info("メール送信: %s -> %s", subject, mail_to)
    return subject
