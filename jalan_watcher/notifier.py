"""notify-jalan Edge Function 経由でのメール通知。

件名だけで内容が分かるように「地域・割引額・先着数」を先頭に置く。本文には
クーポンページへの直リンクを入れるが、「獲得する」操作は必ず利用者本人が
手動で行う（自動獲得は規約違反のため実装しない）。

送信の実体（Gmail SMTP 認証情報）は Edge Function 側に一本化してある。
2026-09-02: GitHub Actions から Gmail SMTP を直接叩く方式は、GMAIL_USER が
誤って別アカウントのアドレスのまま設定されており常に535 Bad Credentials に
なっていた。認証情報を呼び出し元から切り離すことで再発を防ぐ。
"""

from __future__ import annotations

import html
import logging
import urllib.error
import urllib.request
import json as json_lib
from dataclasses import dataclass, field

from .parser import Coupon

log = logging.getLogger(__name__)

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


def _who(c: Coupon) -> str:
    """件名の頭に出す名前。施設クーポンなら宿名、地域ものならエリア名。"""
    return (c.hotel_name or c.region_name or c.area_name or "じゃらん").strip()


def build_subject(events: list[Event]) -> str:
    gettable = [e for e in events if e.is_gettable]
    target = gettable or events
    target = sorted(target, key=lambda e: e.coupon.discount_yen or 0, reverse=True)
    head = target[0]
    c = head.coupon

    if len(target) == 1:
        if head.is_gettable:
            return f"【{_who(c)}】{_amount(c)}クーポン出現（{_stock(c)}）"
        return f"【{_who(c)}】{_amount(c)}クーポン{KIND_LABEL.get(head.kind, head.kind)}"

    regions = []
    for e in target:
        if _who(e.coupon) not in regions:
            regions.append(_who(e.coupon))
    region_part = regions[0] + ("ほか" if len(regions) > 1 else "")
    verb = "出現" if gettable else KIND_LABEL.get(head.kind, "更新")
    return f"【じゃらん】最大{_amount(c)}クーポン{verb}（{region_part}計{len(target)}件）"


def _lines_for(e: Event) -> list[str]:
    c = e.coupon
    state = "配布中（獲得可）" if c.is_available else "配布終了"
    lines = [
        f"■ [{KIND_LABEL.get(e.kind, e.kind)}] {_who(c)} / {_amount(c)} / {_stock(c)}",
        f"   状態     : {state}",
    ]
    if c.title:
        lines.append(f"   クーポン : {c.title}")
    if c.hotel_name:
        lines.append(f"   宿       : {c.hotel_name}（{c.area_name or 'エリア不明'}）")
    if c.usable_plans:
        lines.append(f"   対象プラン: {c.usable_plans}")
    if c.min_spend_yen is not None:
        lines.append(f"   要予約額 : {c.min_spend_yen:,}円以上")
    lines += [
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
    [{html.escape(KIND_LABEL.get(e.kind, e.kind))}] {html.escape(_who(c))}
  </div>
  {f'<div style="font-size:14px;margin-top:2px">{html.escape(c.title)}</div>' if c.title else ''}
  <div style="font-size:22px;font-weight:bold;margin:4px 0">
    {html.escape(_amount(c))} <span style="font-size:14px;font-weight:normal">/ {html.escape(_stock(c))}</span>
  </div>
  <div style="font-size:14px;margin-bottom:6px">状態: {html.escape(state)}</div>
  <div style="font-size:13px;line-height:1.7">
    {f"宿: {html.escape(c.hotel_name)}（{html.escape(c.area_name or 'エリア不明')}）<br>" if c.hotel_name else ""}
    {f"対象プラン: {html.escape(c.usable_plans)}<br>" if c.usable_plans else ""}
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


def check_login(supabase_url: str, supabase_key: str) -> None:
    """notify-jalan Edge Function に到達でき、送信まで通るかを確かめる。

    Edge Function側の認証情報(GMAIL_*)はここでは検証できないため、実際に
    軽いテストメールを1通送って mailed=true が返ることで確認する。
    """
    r = _call_edge_function(
        supabase_url, supabase_key,
        subject="【疎通確認】notify-jalan --check-mail",
        text="jalan_watcher --check-mail からのテスト送信です。実際の通知ではありません。",
    )
    if not r.get("mailed"):
        raise RuntimeError(
            f"notify-jalan からの送信に失敗しました: {r.get('error')}\n"
            "  Edge Function側のシークレット(GMAIL_USER/GMAIL_APP_PASSWORD/MAIL_TO)を"
            "確認してください（jalanプロジェクトの Supabase secrets）。"
        )


def _call_edge_function(supabase_url: str, supabase_key: str, *, subject: str,
                         text: str, body_html: str | None = None) -> dict:
    url = f"{supabase_url}/functions/v1/notify-jalan"
    payload = {"subject": subject, "text": text}
    if body_html:
        payload["html"] = body_html
    req = urllib.request.Request(
        url,
        data=json_lib.dumps(payload).encode("utf-8"),
        headers={
            "apikey": supabase_key,
            "Authorization": f"Bearer {supabase_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json_lib.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"notify-jalan呼び出しに失敗(HTTP {exc.code}): {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"notify-jalanに接続できません: {exc}") from exc


def send(
    *,
    events: list[Event],
    supabase_url: str,
    supabase_key: str,
    dry_run: bool = False,
) -> str:
    subject = build_subject(events)
    text, body_html = build_body(events)

    if dry_run:
        print("=== DRY_RUN: 送信せず表示のみ ===")
        print(f"Subject: {subject}")
        print(text)
        return subject

    result = _call_edge_function(supabase_url, supabase_key, subject=subject,
                                  text=text, body_html=body_html)
    if not result.get("mailed"):
        raise RuntimeError(f"notify-jalan がメール送信に失敗: {result.get('error')}")
    log.info("メール送信: %s", subject)
    return subject
