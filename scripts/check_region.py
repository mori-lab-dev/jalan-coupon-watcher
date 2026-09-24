"""通知する地域の絞り込みを、実際に出てくる書き方で確かめる。

  .venv/bin/python scripts/check_region.py

守らせたいこと。

  1. Phase1（地域ボタン）は region_key でそのまま判定できる
  2. Phase2（一覧）は都道府県まで辿る。細かい地区名からも辿る
  3. **地域を特定できないものは通知する側に倒す**（見逃すより余計に届く方がまし）
  4. 対象外の地域は通知しない
  5. NOTIFY_REGIONS が空なら制限なし（従来どおり全部通知）
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jalan_watcher.region import (AREA_TO_PREF, DEFAULT_REGIONS,  # noqa: E402
                                  PREF_TO_REGION, REGION_KEYS,
                                  parse_regions, region_of, should_notify)

fails: list[str] = []
checks = 0


def check(label, got, want) -> None:
    global checks
    checks += 1
    if got != want:
        fails.append(f"{label}: 期待 {want} / 実際 {got}")


KYUSHU = parse_regions("九州沖縄")

# ── 1. 設定の読み取り ─────────────────────────
check("設定: 空は制限なし", parse_regions(""), set())
check("設定: Noneも制限なし", parse_regions(None), set())
check("設定: 1つ", parse_regions("九州沖縄"), {"九州沖縄"})
check("設定: カンマ区切り", parse_regions("九州沖縄,近畿"), {"九州沖縄", "近畿"})
check("設定: 全角読点も区切る", parse_regions("九州沖縄、近畿"),
      {"九州沖縄", "近畿"})
check("設定: 余白を落とす", parse_regions(" 九州沖縄 , 東海 "),
      {"九州沖縄", "東海"})
check("設定: 既定値", DEFAULT_REGIONS, "九州沖縄")

# ── 2. Phase1（地域ボタン）─────────────────────
check("Phase1: 10地域ある", len(REGION_KEYS), 10)
for key, name in REGION_KEYS.items():
    got, why = region_of(region_key=key, region_name=name)
    check(f"Phase1: {name}", got, name)
    checks += 1
    if why != "地域ボタン":
        fails.append(f"Phase1: {name} の根拠が「{why}」")

check("Phase1: 九州沖縄は通す",
      should_notify(KYUSHU, region_key="button-kyushuokinawa",
                    region_name="九州沖縄")[0], True)
check("Phase1: 近畿は止める",
      should_notify(KYUSHU, region_key="button-kinki",
                    region_name="近畿")[0], False)

# ── 3. Phase2（一覧）都道府県から ───────────────
# 実際にDBに入っている書き方で試す
for name, want in (("沖縄", "九州沖縄"), ("大分", "九州沖縄"),
                   ("長崎", "九州沖縄"), ("宮崎", "九州沖縄"),
                   ("鹿児島", "九州沖縄"),
                   ("鹿児島県②（対象施設のみ）", "九州沖縄"),
                   ("福島", "東北"), ("愛知", "東海"), ("京都", "近畿"),
                   ("新潟", "甲信越"), ("富山", "北陸"), ("千葉", "首都圏")):
    check(f"Phase2: 「{name}」",
          region_of(region_key="listing", region_name=name)[0], want)

# 九州・沖縄8県が全部そろっているか
for pref in ("福岡", "佐賀", "長崎", "熊本", "大分", "宮崎", "鹿児島", "沖縄"):
    check(f"8県: {pref}", PREF_TO_REGION.get(pref), "九州沖縄")

# ── 4. Phase2 細かい地区名から ─────────────────
# **地区名の対応表は九州・沖縄だけ厚く持つ。**
# 目的は九州・沖縄を取りこぼさないことなので、他地域の地区名
# （白河・志摩・佐渡など）は特定できなくてよい。
# 特定できなければ通す側に倒れるが、それらは region_name に
# 都道府県が入っていることが多く、そちらで判定される。
for area, want in (("湯布院", "九州沖縄"), ("霧島", "九州沖縄"),
                   ("島原・雲仙・小浜", "九州沖縄"),
                   ("宮崎・青島・日南", "九州沖縄"),
                   ("西海岸・東海岸", "九州沖縄"),
                   ("白河", None), ("志摩", None), ("佐渡", None)):
    got, _ = region_of(region_key="listing", region_name="一覧",
                       area_name=area)
    check(f"地区: 「{area}」", got, want)

# 五島・対馬・壱岐（友人との話に出た離島）が拾えること
for island in ("五島", "対馬", "壱岐"):
    check(f"離島: {island}", AREA_TO_PREF.get(island), "長崎")
    check(f"離島: {island} は通す",
          should_notify(KYUSHU, region_key="listing", region_name="一覧",
                        area_name=island)[0], True)

# ── 5. 特定できないものは通す ─────────────────
ok, why = should_notify(KYUSHU, region_key="listing", region_name="一覧",
                        area_name="")
check("不明: 既定では通す", ok, True)
checks += 1
if "特定できない" not in why:
    fails.append(f"不明: 理由が分かりにくい「{why}」")
check("不明: 設定で止めることもできる",
      should_notify(KYUSHU, region_key="listing", region_name="一覧",
                    area_name="", notify_unknown=False)[0], False)

# ── 6. 制限なしなら全部通す ───────────────────
for kw in (dict(region_key="button-kinki", region_name="近畿"),
           dict(region_key="listing", region_name="福島"),
           dict(region_key="listing", region_name="一覧")):
    check(f"制限なし: {kw.get('region_name')}",
          should_notify(set(), **kw)[0], True)

# ── 7. 地域名が直接入っていても拾う ────────────
check("地域名: region_name が「九州沖縄」",
      region_of(region_key="listing", region_name="九州沖縄")[0], "九州沖縄")

# 他地域でも region_name に都道府県があれば判定できる
check("他地域: 三重＋志摩",
      region_of(region_key="listing", region_name="三重",
                area_name="志摩")[0], "東海")
check("他地域: 福島＋白河",
      region_of(region_key="listing", region_name="福島",
                area_name="白河")[0], "東北")

# ── 7-2. 対象欄（target_text）からの判定 ───────
# かごしま観光応援割は region_name が「一覧」で area_name も空。
# 地域が分かるのは対象欄だけ（「鹿児島県①（対象施設のみ）」）。
check("対象欄: かごしま観光応援割",
      region_of(region_key="listing", region_name="一覧", area_name="",
                target_text="鹿児島県①（対象施設のみ）")[0], "九州沖縄")
check("対象欄: 鹿児島県②も同じ",
      region_of(region_key="listing", region_name="一覧", area_name="",
                target_text="鹿児島県②（対象施設のみ）")[0], "九州沖縄")
check("対象欄: 地域名そのもの",
      region_of(region_key="listing", region_name="一覧", area_name="",
                target_text="九州沖縄（クーポンフェス掲載宿のみ）")[0], "九州沖縄")

# **対象欄には施設名が入ることがある。** 地区名まで拾うと宿の名前で誤判定する。
# 都道府県・地域名だけで照合しているので、宿名からは判定しない。
for hotel in ("志摩観光ホテル ザ クラシック", "エンゼルフォレスト白河高原",
              "HOTEL OOSADO（ホテル大佐渡）", "変なホテル ラグーナテンボス",
              "由布岳一望 朝霧のみえる宿 ゆふいん花由",
              "宮古島来間リゾート シーウッドホテル"):
    check(f"対象欄: 宿名では判定しない「{hotel[:12]}」",
          region_of(region_key="listing", region_name="一覧", area_name="",
                    target_text=hotel)[0], None)

# 宿名に他地域の地名が入っていても、九州の宿を取り逃がさない
check("対象欄: 宿名＋region_nameがあればそちらが勝つ",
      region_of(region_key="listing", region_name="大分",
                area_name="湯布院",
                target_text="由布岳一望 朝霧のみえる宿 ゆふいん花由")[0],
      "九州沖縄")

# **対象欄はいちばん最後。** 既に判定できているものを上書きしない
check("対象欄: region_name を上書きしない",
      region_of(region_key="listing", region_name="福島", area_name="白河",
                target_text="鹿児島県①（対象施設のみ）")[0], "東北")
check("対象欄: 地域ボタンを上書きしない",
      region_of(region_key="button-tohoku", region_name="東北",
                target_text="鹿児島県①（対象施設のみ）")[0], "東北")

# unknown=false でも かごしま分は残ること（この修正のいちばんの目的）
check("対象欄: 不明を止める設定でも残る",
      should_notify(KYUSHU, region_key="listing", region_name="一覧",
                    area_name="", target_text="鹿児島県①（対象施設のみ）",
                    notify_unknown=False)[0], True)

# ── 8. 都道府県の対応表が47件そろっているか ────
check("対応表: 47都道府県", len(PREF_TO_REGION), 47)
check("対応表: 地域は10種類", len(set(PREF_TO_REGION.values())), 10)

if fails:
    print(f"× {len(fails)}件の不一致（全{checks}項目）")
    for f in fails:
        print(f"   - {f}")
    sys.exit(1)
print(f"{checks}項目すべて期待どおりです。")
