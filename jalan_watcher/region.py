"""通知する地域の絞り込み。

**検知・保存・重複排除・状態判定は変えない。** メールを出すかどうかだけを見る。
MAX_MIN_SPEND_YEN と同じ「検知はするが通知しない」やり方に合わせてある。

地域の材料は2系統ある。

  Phase1（地域ボタン経由）
    region_key が button-* で、region_name は10地域のどれか。
    そのまま比べられる。

  Phase2（一覧経由）
    region_key は listing。都道府県まで分かることが多いが、置き場所が2つある。
      region_name … 施設型は詳細ページの都道府県（沖縄・大分など）
                    キャンペーン型は一覧の「エリア」（白河・志摩など）
      area_name   … 一覧の「エリア」。都道府県より細かい地区名
    どちらも見て、都道府県まで辿れたら地域に直す。

**辿れなかったものは通知する側に倒す。** 見逃すより余計に届くほうがまし
（over_budget と同じ考え方）。
"""

from __future__ import annotations

# 10地域（クーポンフェスの地域ボタン）
REGION_KEYS = {
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

# 都道府県 → 地域。Phase2 の判定に使う
PREF_TO_REGION = {
    "北海道": "北海道",
    "青森": "東北", "岩手": "東北", "宮城": "東北", "秋田": "東北",
    "山形": "東北", "福島": "東北",
    "茨城": "北関東", "栃木": "北関東", "群馬": "北関東",
    "埼玉": "首都圏", "千葉": "首都圏", "東京": "首都圏", "神奈川": "首都圏",
    "岐阜": "東海", "静岡": "東海", "愛知": "東海", "三重": "東海",
    "新潟": "甲信越", "山梨": "甲信越", "長野": "甲信越",
    "富山": "北陸", "石川": "北陸", "福井": "北陸",
    "滋賀": "近畿", "京都": "近畿", "大阪": "近畿", "兵庫": "近畿",
    "奈良": "近畿", "和歌山": "近畿",
    "鳥取": "山陰山陽四国", "島根": "山陰山陽四国", "岡山": "山陰山陽四国",
    "広島": "山陰山陽四国", "山口": "山陰山陽四国", "徳島": "山陰山陽四国",
    "香川": "山陰山陽四国", "愛媛": "山陰山陽四国", "高知": "山陰山陽四国",
    "福岡": "九州沖縄", "佐賀": "九州沖縄", "長崎": "九州沖縄",
    "熊本": "九州沖縄", "大分": "九州沖縄", "宮崎": "九州沖縄",
    "鹿児島": "九州沖縄", "沖縄": "九州沖縄",
}

# 地区名 → 都道府県。一覧の「エリア」は都道府県より細かいので補う。
# **九州・沖縄を取りこぼさないことを優先**して厚めに入れてある。
AREA_TO_PREF = {
    # 福岡
    "博多": "福岡", "天神": "福岡", "中洲": "福岡", "北九州": "福岡",
    "門司": "福岡", "太宰府": "福岡", "柳川": "福岡", "原鶴": "福岡",
    "筑後": "福岡", "宗像": "福岡",
    # 佐賀
    "嬉野": "佐賀", "武雄": "佐賀", "唐津": "佐賀", "有田": "佐賀",
    "古湯": "佐賀",
    # 長崎
    "島原": "長崎", "雲仙": "長崎", "小浜": "長崎", "佐世保": "長崎",
    "ハウステンボス": "長崎", "五島": "長崎", "対馬": "長崎",
    "壱岐": "長崎", "平戸": "長崎", "長崎市": "長崎",
    # 熊本
    "阿蘇": "熊本", "黒川": "熊本", "天草": "熊本", "人吉": "熊本",
    "山鹿": "熊本", "菊池": "熊本",
    # 大分
    "湯布院": "大分", "由布院": "大分", "別府": "大分", "日田": "大分",
    "竹田": "大分", "国東": "大分", "中津": "大分",
    # 宮崎
    "青島": "宮崎", "日南": "宮崎", "高千穂": "宮崎", "延岡": "宮崎",
    "えびの": "宮崎",
    # 鹿児島
    "霧島": "鹿児島", "指宿": "鹿児島", "屋久島": "鹿児島",
    "奄美": "鹿児島", "桜島": "鹿児島", "種子島": "鹿児島",
    # 沖縄
    "那覇": "沖縄", "石垣": "沖縄", "宮古": "沖縄", "恩納": "沖縄",
    "名護": "沖縄", "北谷": "沖縄", "西海岸": "沖縄", "東海岸": "沖縄",
    "本部": "沖縄", "読谷": "沖縄", "久米島": "沖縄", "西表": "沖縄",
}

DEFAULT_REGIONS = "九州沖縄"


def parse_regions(raw: str | None) -> set[str]:
    """`NOTIFY_REGIONS` を集合にする。空なら「制限なし」を表す空集合。"""
    if raw is None:
        return set()
    parts = [p.strip() for p in raw.replace("、", ",").split(",")]
    return {p for p in parts if p}


def _pref_of(text: str) -> str | None:
    """文字列から都道府県を拾う。細かい地区名からも辿る。"""
    if not text:
        return None
    t = text.strip()
    for pref in PREF_TO_REGION:
        if pref in t:                      # 「鹿児島県②（対象施設のみ）」も拾える
            return pref
    for area, pref in AREA_TO_PREF.items():
        if area in t:
            return pref
    return None


def region_of(*, region_key: str = "", region_name: str = "",
              area_name: str = "") -> tuple[str | None, str]:
    """その クーポンの地域を決める。戻り値は (地域, 根拠)。

    分からなければ (None, 理由)。**推測で埋めない。**
    """
    key = (region_key or "").strip()
    if key in REGION_KEYS:
        return REGION_KEYS[key], "地域ボタン"

    # 地域名そのものが入っていることがある（「九州沖縄」など）
    for name in (region_name, area_name):
        n = (name or "").strip()
        if n in REGION_KEYS.values():
            return n, "地域名"

    for name, where in ((region_name, "詳細ページの地域"),
                        (area_name, "一覧のエリア")):
        pref = _pref_of(name or "")
        if pref:
            return PREF_TO_REGION[pref], f"{where}（{pref}）"

    return None, "地域を特定できない"


def should_notify(allowed: set[str], *, region_key: str = "",
                  region_name: str = "", area_name: str = "",
                  notify_unknown: bool = True) -> tuple[bool, str]:
    """通知してよいか。戻り値は (通知する, 理由)。"""
    if not allowed:
        return True, "地域の制限なし"
    region, why = region_of(region_key=region_key, region_name=region_name,
                            area_name=area_name)
    if region is None:
        # **分からないものは通知する側に倒す。** 見逃すより余計に届くほうがまし
        return notify_unknown, f"{why}（{'通知する' if notify_unknown else '通知しない'}）"
    if region in allowed:
        return True, f"{region}（{why}）"
    return False, f"{region}（{why}）は対象外"
