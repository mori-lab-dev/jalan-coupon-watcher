// supabase/functions/jalan-dashboard-api/index.ts
//
// ダッシュボード用の**読み取り専用**API。
// 現在配布中のクーポン・直近の実行状況・通知履歴をJSONで返す。
//
// **書き込みは一切しない。** 検知・通知のロジックにも触れない。
// service_role キーはこのEdge Function側のシークレットにのみ置く
// （notify-jalan と同じ方針）。
//
// このリポジトリはpublicなので、URLを知れば誰でも叩ける。
// 中身はじゃらんの公開クーポン情報の写しだが、念のため
// 共有シークレット（?key= または X-Api-Key ヘッダ）で最低限の制限をかける。
// キーが無い/違えば 401。

const SUPABASE_URL = Deno.env.get("SUPABASE_URL") ?? "";
const SERVICE_KEY = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "";
const API_KEY = Deno.env.get("DASHBOARD_API_KEY") ?? "";

// 通知条件。GitHub Actions 側の Variable と同じ値を入れておく
const NOTIFY_REGIONS = Deno.env.get("NOTIFY_REGIONS") ?? "";
const MAX_MIN_SPEND_YEN = Number(Deno.env.get("MAX_MIN_SPEND_YEN") ?? "50000");
const NOTIFY_UNKNOWN_REGION =
  (Deno.env.get("NOTIFY_UNKNOWN_REGION") ?? "true").toLowerCase() !== "false";

// ── 地域判定 ────────────────────────────────
// **jalan_watcher/region.py の写し。** どちらかを直したら両方直すこと。
// ここで判定しているのは「通知条件を満たすか」の表示用で、
// 実際の通知可否は Python 側が決める。
const REGION_KEYS: Record<string, string> = {
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
};

const PREF_TO_REGION: Record<string, string> = {
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
};

const AREA_TO_PREF: Record<string, string> = {
  "博多": "福岡", "天神": "福岡", "中洲": "福岡", "北九州": "福岡",
  "門司": "福岡", "太宰府": "福岡", "柳川": "福岡", "原鶴": "福岡",
  "筑後": "福岡", "宗像": "福岡",
  "嬉野": "佐賀", "武雄": "佐賀", "唐津": "佐賀", "有田": "佐賀",
  "古湯": "佐賀",
  "島原": "長崎", "雲仙": "長崎", "小浜": "長崎", "佐世保": "長崎",
  "ハウステンボス": "長崎", "五島": "長崎", "対馬": "長崎",
  "壱岐": "長崎", "平戸": "長崎", "長崎市": "長崎",
  "阿蘇": "熊本", "黒川": "熊本", "天草": "熊本", "人吉": "熊本",
  "山鹿": "熊本", "菊池": "熊本",
  "湯布院": "大分", "由布院": "大分", "別府": "大分", "日田": "大分",
  "竹田": "大分", "国東": "大分", "中津": "大分",
  "青島": "宮崎", "日南": "宮崎", "高千穂": "宮崎", "延岡": "宮崎",
  "えびの": "宮崎",
  "霧島": "鹿児島", "指宿": "鹿児島", "屋久島": "鹿児島",
  "奄美": "鹿児島", "桜島": "鹿児島", "種子島": "鹿児島",
  "那覇": "沖縄", "石垣": "沖縄", "宮古": "沖縄", "恩納": "沖縄",
  "名護": "沖縄", "北谷": "沖縄", "西海岸": "沖縄", "東海岸": "沖縄",
  "本部": "沖縄", "読谷": "沖縄", "久米島": "沖縄", "西表": "沖縄",
};

function prefOf(text: string): string | null {
  const t = (text ?? "").trim();
  if (!t) return null;
  for (const pref of Object.keys(PREF_TO_REGION)) if (t.includes(pref)) return pref;
  for (const [area, pref] of Object.entries(AREA_TO_PREF)) {
    if (t.includes(area)) return pref;
  }
  return null;
}

// 対象欄は施設名が入りうるので都道府県・地域名だけで照合する
function prefOfStrict(text: string): string | null {
  const t = (text ?? "").trim();
  if (!t) return null;
  for (const pref of Object.keys(PREF_TO_REGION)) if (t.includes(pref)) return pref;
  return null;
}

function regionOf(c: Record<string, unknown>): { region: string | null; why: string } {
  const key = String(c.region_key ?? "").trim();
  if (REGION_KEYS[key]) return { region: REGION_KEYS[key], why: "地域ボタン" };

  const names = [String(c.region_name ?? ""), String(c.area_name ?? "")];
  for (const n of names) {
    if (Object.values(REGION_KEYS).includes(n.trim())) {
      return { region: n.trim(), why: "地域名" };
    }
  }
  const sources: [string, string][] = [
    [names[0], "詳細ページの地域"],
    [names[1], "一覧のエリア"],
  ];
  for (const [name, where] of sources) {
    const pref = prefOf(name);
    if (pref) return { region: PREF_TO_REGION[pref], why: `${where}（${pref}）` };
  }
  // 最後に対象欄。それまでの判定を上書きしない
  const tgt = String(c.target_text ?? "").trim();
  if (tgt) {
    for (const region of Object.values(REGION_KEYS)) {
      if (tgt.includes(region)) return { region, why: `対象欄（${region}）` };
    }
    const pref = prefOfStrict(tgt);
    if (pref) return { region: PREF_TO_REGION[pref], why: `対象欄（${pref}）` };
  }
  return { region: null, why: "地域を特定できない" };
}

function allowedRegions(): Set<string> {
  return new Set(
    NOTIFY_REGIONS.replace(/、/g, ",").split(",").map((s) => s.trim()).filter(Boolean),
  );
}

// ── DB読み取り（service_role・読み取り専用）─────
async function select(path: string, query: string): Promise<unknown[]> {
  const url = `${SUPABASE_URL}/rest/v1/${path}?${query}`;
  const r = await fetch(url, {
    headers: {
      apikey: SERVICE_KEY,
      Authorization: `Bearer ${SERVICE_KEY}`,
      Accept: "application/json",
    },
  });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${(await r.text()).slice(0, 200)}`);
  return await r.json();
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 1), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Headers": "x-api-key, content-type",
    },
  });
}

Deno.serve(async (req) => {
  if (req.method === "OPTIONS") return json({ ok: true });
  if (req.method !== "GET") return json({ ok: false, error: "GETのみ" }, 405);

  const url = new URL(req.url);
  const given = url.searchParams.get("key") ?? req.headers.get("x-api-key") ?? "";
  if (!API_KEY) {
    return json({ ok: false, error: "サーバ側にAPIキーが設定されていません" }, 500);
  }
  if (given !== API_KEY) {
    return json({ ok: false, error: "キーが違います" }, 401);
  }

  const limit = Math.min(Number(url.searchParams.get("limit") ?? "20") || 20, 100);

  try {
    const allowed = allowedRegions();
    const cols = [
      "coupon_id", "source", "region_key", "region_name", "area_name",
      "hotel_name", "title", "target_text", "discount_yen", "discount_label",
      "stock", "is_available", "status_text", "distribute_period",
      "reserve_period", "stay_period", "min_spend", "min_spend_yen",
      "page_url", "first_seen_at", "last_seen_at", "notified_at",
    ].join(",");

    const raw = await select(
      "jalan_coupons",
      `select=${cols}&is_available=is.true&order=discount_yen.desc.nullslast&limit=500`,
    ) as Record<string, unknown>[];

    const coupons = raw.map((c) => {
      const { region, why } = regionOf(c);
      const need = c.min_spend_yen === null || c.min_spend_yen === undefined
        ? null
        : Number(c.min_spend_yen);
      // 予算: 読み取れなかったものは通知する側に倒す（Python側と同じ）
      const withinBudget = MAX_MIN_SPEND_YEN <= 0 || need === null ||
        need <= MAX_MIN_SPEND_YEN;
      const withinRegion = allowed.size === 0
        ? true
        : (region === null ? NOTIFY_UNKNOWN_REGION : allowed.has(region));
      return {
        coupon_id: c.coupon_id,
        source: c.source,
        hotel_name: c.hotel_name || null,
        area_name: c.area_name || null,
        region_name: c.region_name || null,
        region: region,
        region_why: why,
        title: c.title || null,
        discount_yen: c.discount_yen,
        discount_label: c.discount_label,
        stock: c.stock,
        min_spend: c.min_spend || null,
        min_spend_yen: need,
        distribute_period: c.distribute_period,
        reserve_period: c.reserve_period,
        stay_period: c.stay_period,
        url: c.page_url,
        first_seen_at: c.first_seen_at,
        last_seen_at: c.last_seen_at,
        notified_at: c.notified_at,
        // 通知条件を満たすか（AND条件）。実際の通知可否はPython側が決める
        matches_filters: withinRegion && withinBudget,
        within_region: withinRegion,
        within_budget: withinBudget,
        // **予算を読み取れていないことを隠さない。**
        // 条件文が「210,000円（税込）以上」のように【予約金額】の前置きが
        // 無いと監視側が金額を取れず、予算フィルタは通す側に倒れる。
        // 画面で「予算不明」と出せるように印を付ける
        budget_unknown: need === null && !!c.min_spend,
      };
    });

    const runs = await select(
      "jalan_watch_runs",
      "select=id,started_at,finished_at,coupons_seen,coupons_available," +
        `notified_count,regions_failed,error&order=started_at.desc&limit=${limit}`,
    );

    const notified = await select(
      "jalan_coupons",
      `select=coupon_id,region_name,area_name,hotel_name,discount_label,` +
        `discount_yen,min_spend_yen,page_url,notified_at&notified_at=not.is.null` +
        `&order=notified_at.desc&limit=${limit}`,
    );

    return json({
      ok: true,
      generated_at: new Date().toISOString(),
      filters: {
        notify_regions: NOTIFY_REGIONS || null,
        max_min_spend_yen: MAX_MIN_SPEND_YEN,
        notify_unknown_region: NOTIFY_UNKNOWN_REGION,
        _note: "この条件は表示用の再現です。実際の通知可否は監視側(Python)が決めます",
      },
      counts: {
        available: coupons.length,
        matches_filters: coupons.filter((c) => c.matches_filters).length,
        region_unknown: coupons.filter((c) => c.region === null).length,
      },
      coupons,
      runs,
      notified,
    });
  } catch (e) {
    return json({ ok: false, error: String(e).slice(0, 300) }, 500);
  }
});
