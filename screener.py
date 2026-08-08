# -*- coding: utf-8 -*-
"""
kfilter 확장 — 기술적 스크리닝 필터 (저가매수형 / 모멘텀형)

[설계 원칙]
· 수급은 필터가 아니라 라벨. 필터로 쓰면 "수급 없이 기술만 좋은 종목"을 못 본다.
· 【저가매수형】 4축 = ①잘 번다(ROE) ②잘 벌 거다(EPS 성장) ③주가가 낮다(이익수익률) ④지수 대비 더 낮다(초과낙폭).
    - 자격은 시장 국면에 안 흔들리는 것만 절대 기준으로. 개수는 ROE 순위로 자른다.
    - ★상위 20개 고정. 꾸준한 모니터링이 목적이라 개수가 일정해야 시간에 따른 변화가 보인다.
    - 백분위(순위) 폐기 — "PER 하위 40%"는 싸다는 뜻이 아니라 남들보다 덜 비싸다는 뜻이다.
    - 절대 낙폭 폐기 — 하락장에서 전부 통과한다. 지수 대비 초과낙폭으로 본다.
    - 컨센서스 폐기 — 크게 빠진 종목은 이미 전망이 틀린 집단이다. 실측 낙관배율 중앙값 1.65배.
· 【모멘텀형】 현행 유지. 시장 국면에 따라 개수가 고무줄인 게 정상이다. 0개면 0개.
· 필터는 판단하지 않는다. 매수 우선순위는 일괄 분석 리포트에서 정한다.

[호출 최적화 — 실측 기준]
· 유니버스: sectors 2회(4.6초). ★page 파라미터 무의미 — 1페이지에 전체가 온다.
· 시세    : quotesv4 벌크. ★한계 700개(800은 503). 2735종목 → 7회 3.0초.
· 펀더멘털: 분기 캐시(494KB). 매일 호출 0회. PER은 당일가÷캐시EPS로 계산.
· 일봉    : 시총 상위 → 흑자 게이트 통과분만. ★순서를 바꾸지 마라
            (흑자를 먼저 걸러 800을 채우면 소형주가 들어와 시총 컷이 낮아진다).
"""

import os, json, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import requests

# ===== 설정 =====
DAUM = "https://finance.daum.net/api"
HDR = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.daum.net/",
}
CACHE_DIR   = "cache"
FUND_CACHE  = os.path.join(CACHE_DIR, "fundamentals.json")   # 분기 갱신
UNIV_CACHE  = os.path.join(CACHE_DIR, "universe.json")       # 주 1회 갱신

TOP_N        = 800     # 시총 상위 N (실측 컷 ≈ 2,251억)
BULK_MAX     = 400     # quotesv4 1회 청크 (실측: 400은 2735전량 100%성공. 700은 URL길이로 간헐 503)
DAYS_N       = 320     # 일봉 조회 건수 (MA60 + 정배열진입 120일 역산 + 여유)
MIN_ROWS     = 130     # 유효행 최소치 (미만이면 제외)
WORKERS      = 12      # 병렬 스레드

# 【저가매수형】 자격 — 시장 국면에 안 흔들리는 절대 기준만
VP_DD_FLOOR  = -70.0   # 낙폭 하한(%). 이보다 더 빠졌으면 눌림이 아니라 붕괴
VP_PEAK_DAYS = 180     # 52주 고점이 이 일수 이내여야 "단기 눌림". 오래된 고점은 장기 우하향이다
VP_EY_MIN    = 5.0     # 이익수익률 최소(%). 연율PER 20배 — "주가가 낮다" 축의 최소선
VP_EXCESS_MAX = -5.0   # 초과낙폭 최소선(%p). 이보다 얕으면 신고가 근처지 눌린 게 아니다
VP_TOP_N     = 20      # ★상한(ROE 순). 상승장엔 후보 자체가 적어 안 채워지는 날이 있다
FIN_CACHE    = os.path.join(CACHE_DIR, "financials.json")   # 분기 재무 캐시(분기 갱신)

# ===== 공통 =====
def _get(url, timeout=15):
    r = requests.get(url, headers=HDR, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ───────────────────── 1. 유니버스 (주 1회) ─────────────────────
def fetch_universe(force=False):
    """다음 sectors에서 전 종목 코드 수집. 주 1회만 갱신(신규상장·상폐 반영).
    ★page 파라미터는 무의미하다(p1~p5가 동일 응답). 시장당 1회면 충분."""
    cache = _load(UNIV_CACHE)
    if cache and not force:
        try:
            age = datetime.now() - datetime.fromisoformat(cache["updated"])
            if age < timedelta(days=7):
                return cache["codes"]
        except Exception:
            pass

    codes = {}
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            data = _get(f"{DAUM}/quotes/sectors?page=1&perPage=40"
                        f"&fieldName=changeRate&order=desc&market={mkt}&pagination=true").get("data") or []
            for sec in data:
                for s in (sec.get("includedStocks") or []):
                    sc = s.get("symbolCode")
                    if sc:
                        codes[sc] = {"name": s.get("name"), "market": mkt}
        except Exception as e:
            print(f"  [경고] 유니버스 수집 실패 {mkt}: {e}", flush=True)

    if codes:
        _save(UNIV_CACHE, {"updated": datetime.now().isoformat(timespec="seconds"), "codes": codes})
    elif cache:
        return cache["codes"]          # 실패 시 기존 캐시 유지
    return codes


# ───────────────────── 2. 펀더멘털 캐시 (분기 1회) ─────────────────────
def _is_earnings_season():
    """실적 시즌(2·5·8·11월)이면 캐시 갱신 대상."""
    return datetime.now().month in (2, 5, 8, 11)


def fetch_fundamentals(symbol_codes, force=False):
    """quotes에서 분기성 필드만 캐시. 매일은 캐시에서 읽어 호출 0회.
    캐시: {symbolCode: {op, ni, eps, mcap, sectorPer, per, pbr, dps, high52w, low52w, name, market}}"""
    cache = _load(FUND_CACHE)
    if cache and not force:
        try:
            upd = datetime.fromisoformat(cache["updated"])
            stale = (datetime.now() - upd) > timedelta(days=100)
            # 실적 시즌이고 이번 달에 아직 안 받았으면 갱신
            need = stale or (_is_earnings_season() and upd.month != datetime.now().month)
            if not need:
                return cache["data"]
        except Exception:
            pass

    print(f"  [캐시] 펀더멘털 갱신 {len(symbol_codes)}종목…", flush=True)
    out = {}

    def one(sc):
        try:
            d = _get(f"{DAUM}/quotes/{sc}?summary=false&changeStatistics=true", timeout=12)
            return sc, {
                "op": d.get("operatingProfit"), "ni": d.get("netIncome"),
                "eps": d.get("eps"), "mcap": d.get("marketCap"),
                "sectorPer": d.get("sectorPer"), "per": d.get("per"),
                "pbr": d.get("pbr"), "dps": d.get("dps"),
                "high52w": d.get("high52wPrice"), "low52w": d.get("low52wPrice"),
                "name": d.get("name"), "market": d.get("market"),
                "sector": d.get("wicsSectorName"),
            }
        except Exception:
            return sc, None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sc, v in ex.map(one, symbol_codes):
            if v:
                out[sc] = v

    if out:
        _save(FUND_CACHE, {"updated": datetime.now().isoformat(timespec="seconds"), "data": out})
        return out
    return (cache or {}).get("data", {})


# ───────────────────── 3. 당일 시세 (벌크) ─────────────────────
def fetch_prices(symbol_codes):
    """quotesv4 벌크로 당일 시세. ★1회 700개까지(800은 503)."""
    out = {}
    codes = list(symbol_codes)
    for i in range(0, len(codes), BULK_MAX):
        chunk = codes[i:i + BULK_MAX]
        for attempt in (1, 2):
            try:
                d = _get(f"{DAUM}/quotesv4?codes={','.join(chunk)}", timeout=25)
                for q in (d.get("quotes") or []):
                    sc = q.get("symbolCode")
                    if sc:
                        tp = q.get("tradePrice")
                        cr = q.get("changeRate")
                        # ★전일 종가 역산 — 다음 PER은 '전일종가 ÷ EPS' 기준이다.
                        #   당일가로 계산하면 장중 등락만큼 PER이 왜곡된다(대원전선 +13.7%일 때 PER 13% 부풀려짐).
                        prev = (tp / (1 + cr)) if (tp and cr is not None and (1 + cr) != 0) else tp
                        out[sc] = {
                            "price": tp,
                            "prevClose": prev,
                            "changeRate": cr,
                            "volume": q.get("accTradeVolume"),
                            "amount": q.get("accTradePrice"),
                            "foreignRatio": q.get("foreignRatio"),
                            "name": q.get("name"),
                        }
                break
            except Exception as e:
                if attempt == 2:
                    print(f"  [경고] 시세 벌크 실패({i}~{i+len(chunk)}): {e}", flush=True)
                else:
                    time.sleep(1.0)
        time.sleep(0.15)          # 연속 호출 간격 (503 예방)
    return out


# ───────────────────── 4. 일봉 + 지표 ─────────────────────
def fetch_days(sc):
    """일봉 조회. ★accTradeVolume==0 행 제거(PRE_MARKET 더미 + 거래정지).
    파두는 30건이었다. 제거 후 MIN_ROWS 미만이면 None."""
    try:
        d = _get(f"{DAUM}/quote/{sc}/days?perPage={DAYS_N}&page=1", timeout=15)
        rows = [r for r in (d.get("data") or []) if (r.get("accTradeVolume") or 0) > 0]
        return rows if len(rows) >= MIN_ROWS else None
    except Exception:
        return None


def calc_indicators(rows):
    """일봉(최신순)에서 지표 계산. rows[0]이 최근."""
    cl = [r.get("tradePrice") for r in rows if r.get("tradePrice")]
    vo = [r.get("accTradeVolume") or 0 for r in rows]
    if len(cl) < 130:
        return None

    def ma(n, off=0):
        seg = cl[off:off + n]
        return sum(seg) / len(seg) if len(seg) == n else None

    cur = cl[0]
    ma5, ma20, ma60 = ma(5), ma(20), ma(60)
    if not (ma5 and ma20 and ma60):
        return None

    # 주봉 — 일봉 5개마다 샘플링
    wk = [cl[i] for i in range(0, min(len(cl), 300), 5)]
    w60 = sum(wk[:60]) / 60 if len(wk) >= 60 else None

    # 정배열 진입 일수: 오늘부터 역산해 정배열이 깨진 첫 지점
    entry = 120
    for i in range(0, 120):
        if len(cl) < i + 60:
            entry = 120
            break
        a, b, c = ma(5, i), ma(20, i), ma(60, i)
        if not (a and b and c) or not (a > b > c):
            entry = i
            break

    # 52주 고저 — ★반드시 날짜 기준. 252건 기준은 거래정지 종목에서 최대 12% 어긋난다.
    cut = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d")
    yr = [r for r in rows if (r.get("date") or "")[:10] >= cut]
    hi = max((r.get("highPrice") or 0) for r in yr) if yr else None
    lo = min((r.get("lowPrice") or 0) for r in yr if (r.get("lowPrice") or 0) > 0) if yr else None

    # ★저가매수형용 — 종가 기준 52주 고점(지수도 종가 기준이라 단위를 맞춰야 초과낙폭이 정확하다)
    pk = max(yr, key=lambda r: (r.get("tradePrice") or 0)) if yr else None
    peak_close = (pk.get("tradePrice") if pk else None)
    peak_date = ((pk.get("date") or "")[:10] if pk else None)
    peak_days = None
    if peak_date:
        try:
            peak_days = (datetime.now() - datetime.strptime(peak_date, "%Y-%m-%d")).days
        except Exception:
            peak_days = None

    v20 = sum(vo[:20]) / 20 if len(vo) >= 20 else 0
    v60 = sum(vo[:60]) / 60 if len(vo) >= 60 else 0

    return {
        "cur": cur, "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "daily_aligned": ma5 > ma20 > ma60,
        "ma5_above": cur > ma5,
        "peak_close": peak_close, "peak_date": peak_date, "peak_days": peak_days,
        "dd_close": ((cur / peak_close - 1) * 100) if peak_close else None,
        "weekly_ma60_above": (cur / w60 - 1) > 0 if w60 else False,
        "entry_days": entry,
        "r1": (cl[0] / cl[21] - 1) * 100 if len(cl) > 21 else None,
        "r6": (cl[0] / cl[126] - 1) * 100 if len(cl) > 126 else None,
        "v2060": (v20 / v60) if v60 else None,
        "ma20_gap": (cur / ma20 - 1) * 100,
        "high52w": hi, "low52w": lo,
        "pos52w": ((cur - lo) / (hi - lo) * 100) if (hi and lo and hi > lo) else None,
    }


# ───────────────────── 5. 지수 일봉 (초과낙폭용) ─────────────────────
def fetch_index_days():
    """코스피·코스닥 일봉 → {market: {date: close}}.
    ★저가매수형 ④축(지수 대비 초과낙폭) 계산용. 절대 낙폭만 쓰면 하락장에서 전부 통과한다."""
    out = {}
    for mkt in ("KOSPI", "KOSDAQ"):
        try:
            d = _get(f"{DAUM}/market_index/days?page=1&perPage={DAYS_N}"
                     f"&market={mkt}&pagination=true", timeout=20).get("data") or []
            # ★장전엔 당일 행이 미확정이므로 종목 일봉 기준일과 맞추기 위해 그대로 담고 조회 시 보정
            out[mkt] = {(r.get("date") or "")[:10]: r.get("tradePrice")
                        for r in d if r.get("tradePrice")}
        except Exception as e:
            print(f"  [경고] 지수 일봉 실패 {mkt}: {e}", flush=True)
            out[mkt] = {}
    return out


def index_at(idx, market, date):
    """해당 일자(없으면 그 이전 최근 거래일)의 지수 종가."""
    ks = idx.get("KOSDAQ" if market == "KOSDAQ" else "KOSPI") or {}
    if not ks:
        return None
    if date in ks:
        return ks[date]
    prev = [d for d in ks if d <= date]
    return ks[max(prev)] if prev else None


# ───────────────────── 6. 분기 재무 캐시 (분기 갱신) ─────────────────────
def fetch_financials(symbol_codes, force=False):
    """QUARTER 4건을 캐시. ★매일 부르면 613회다 — 분기 캐시로 흡수해 호출 0회.
    캐시: {symbolCode: [{date, eps, op, sales, roe}, ...4건]}
    ROE는 분기 단독값(소수). 연환산은 최근 2분기 합 x 2로 계산한다."""
    cache = _load(FIN_CACHE)
    if cache and not force:
        try:
            upd = datetime.fromisoformat(cache["updated"])
            stale = (datetime.now() - upd) > timedelta(days=100)
            need = stale or (_is_earnings_season() and upd.month != datetime.now().month)
            if not need:
                return cache["data"]
        except Exception:
            pass

    print(f"  [캐시] 분기재무 갱신 {len(symbol_codes)}종목…", flush=True)
    out = {}

    def one(sc):
        try:
            q = (_get(f"{DAUM}/quote/{sc}/financials", timeout=12).get("data") or {}).get("QUARTER") or []
            return sc, [{"date": r.get("date"), "eps": r.get("eps"),
                         "op": r.get("operatingProfit"), "sales": r.get("sales"),
                         "roe": r.get("roe")} for r in q[:4]]
        except Exception:
            return sc, None

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sc, v in ex.map(one, symbol_codes):
            if v:
                out[sc] = v

    if out:
        _save(FIN_CACHE, {"updated": datetime.now().isoformat(timespec="seconds"), "data": out})
        return out
    return (cache or {}).get("data", {})


def derive_fundamentals(q):
    """분기 4건 → 저가매수형 4축 원재료.
    ·연환산 ROE = 최근 2분기 ROE 합 x 2   (분기 단독값이므로)
    ·연환산 EPS = 최근 반기 EPS 합 x 2    (TTM은 과거 부진을 끌고 와 개선 국면을 과소평가)
    ·반기 EPS 성장 = 최근 반기 / 직전 반기"""
    if not q or len(q) < 4:
        return None
    e = [(r.get("eps") or 0) for r in q[:4]]
    op = [(r.get("op") or 0) for r in q[:4]]
    ro = [(r.get("roe") or 0) for r in q[:4]]
    hn, ho = e[0] + e[1], e[2] + e[3]
    turn = (ho <= 0 and hn > 0)
    return {
        "roe_ann": (ro[0] + ro[1]) * 2 * 100,
        "eps_ann": hn * 2,
        "eps_growth": ((hn / ho - 1) * 100) if ho > 0 else None,
        "eps_turnaround": turn,
        "eps_up": turn or (ho > 0 and hn > ho),
        "op_2q_pos": all(v > 0 for v in op[:2]),
    }


# ───────────────────── 7. 백분위 (모멘텀형 전용) ─────────────────────
def percentile_map(items, key):
    """유니버스 내 순위(0~100). ★지수 대비가 아니다."""
    vals = [(x["code"], x[key]) for x in items if x.get(key) is not None]
    vals.sort(key=lambda z: z[1])
    n = len(vals)
    if n <= 1:
        return {c: 50.0 for c, _ in vals}
    return {c: i / (n - 1) * 100 for i, (c, _) in enumerate(vals)}


# ───────────────────── 8. 메인 ─────────────────────
def run_screeners(supply_map=None):
    """저가매수형·모멘텀형 스크리닝.
    supply_map: {code6: {f_net,f_buydays,p_net,p_buydays,is_zero_rank}} — 수급 '라벨'용(필터 아님)
    반환: {"value_pick": [...], "momentum": [...], "stats": {...}}
    """
    t0 = time.time()
    supply_map = supply_map or {}

    # 1) 유니버스
    univ = fetch_universe()
    print(f"  유니버스 {len(univ)}종목", flush=True)
    if not univ:
        return {"value_pick": [], "momentum": [], "stats": {"error": "유니버스 수집 실패"}}

    # 2) 펀더멘털 캐시 + 당일 시세
    fund = fetch_fundamentals(list(univ.keys()))
    prices = fetch_prices(list(univ.keys()))
    print(f"  펀더멘털 {len(fund)} / 시세 {len(prices)}", flush=True)

    # 2-b) 지수 일봉 — 저가매수형 ④축(초과낙폭)
    idx = fetch_index_days()

    # 3) 시총 상위 → 흑자 게이트 (★순서 고정)
    ranked = sorted(
        [(sc, f) for sc, f in fund.items() if f.get("mcap")],
        key=lambda z: z[1]["mcap"], reverse=True)[:TOP_N]
    mcap_cut = ranked[-1][1]["mcap"] if ranked else 0

    profit_ok = [(sc, f) for sc, f in ranked
                 if (f.get("op") or 0) > 0 and (f.get("ni") or 0) > 0]
    print(f"  시총상위 {len(ranked)} → 흑자 {len(profit_ok)}", flush=True)

    # 3-b) 분기재무 캐시 — 시총 상위 전체 기준(흑자 통과분은 매번 바뀌므로 모집단을 고정한다)
    fin = fetch_financials([sc for sc, _ in ranked])

    # 4) 일봉 (흑자 통과분만)
    def load(item):
        sc, f = item
        rows = fetch_days(sc)
        if not rows:
            return None
        ind = calc_indicators(rows)
        if not ind:
            return None
        p = prices.get(sc) or {}
        cur = p.get("price") or ind["cur"]
        eps = f.get("eps")
        out = {
            "code": sc[1:] if sc.startswith("A") else sc, "symbol": sc,
            "name": f.get("name") or p.get("name"), "market": f.get("market"),
            "sector": f.get("sector"),
            "cur": cur, "changeRate": (p.get("changeRate") or 0) * 100,
            "volume": p.get("volume"), "foreignRatio": p.get("foreignRatio"),
            # ★PER은 '전일종가 ÷ EPS' — 다음 산출 기준과 동일(실측 검증: PER×EPS = 전일종가).
            #   당일가로 계산하면 장중 등락만큼 왜곡되어 '저가매수형 PER 하위40%' 판정이 틀어진다.
            "per": ((p.get("prevClose") or cur) / eps) if (eps and eps > 0) else None,
            "pbr": f.get("pbr"), "dps": f.get("dps"),
            "sectorPer": f.get("sectorPer"), "mcap": f.get("mcap"),
            **ind,
        }
        # 【저가매수형 4축 원재료】
        d = derive_fundamentals(fin.get(sc))
        if d:
            ann = d["eps_ann"]
            out.update({
                "roe_ann": d["roe_ann"],                                   # ①잘 번다
                "eps_growth": d["eps_growth"],                             # ②잘 벌 거다
                "eps_turnaround": d["eps_turnaround"], "eps_up": d["eps_up"],
                "op_2q_pos": d["op_2q_pos"],
                "eps_ann": ann,
                "earn_yield": (ann / cur * 100) if (ann > 0 and cur) else None,   # ③주가가 낮다
                "per_ann": (cur / ann) if (ann > 0 and cur) else None,
                "eps_quarters": fin.get(sc),
            })
        # ★낙폭은 실시간 cur 기준으로 재계산 — earn_yield와 기준을 통일한다
        #   (calc_indicators의 dd_close는 일봉 첫 행 기준. 장중 갱신 지연 시 어긋날 수 있다)
        if ind.get("peak_close"):
            out["dd_close"] = (cur / ind["peak_close"] - 1) * 100
        # ④지수 대비 더 낮다 — 고점일부터 오늘까지 지수 변화를 빼준다
        if ind.get("peak_date") and out.get("dd_close") is not None:
            base = (rows[0].get("date") or "")[:10]
            i_now = index_at(idx, f.get("market"), base)
            i_pk = index_at(idx, f.get("market"), ind["peak_date"])
            if i_now and i_pk:
                out["idx_since_peak"] = (i_now / i_pk - 1) * 100
                out["excess_dd"] = out["dd_close"] - out["idx_since_peak"]
        return out

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        items = [x for x in ex.map(load, profit_ok) if x]
    print(f"  일봉 유효 {len(items)}", flush=True)
    if not items:
        return {"value_pick": [], "momentum": [], "stats": {"error": "일봉 수집 실패"}}

    # 5) 백분위 — ★모멘텀형 1개월 순위에만 사용. 저가매수형은 백분위를 쓰지 않는다.
    p1 = percentile_map(items, "r1")

    # 6) 필터
    value_cand, momentum = [], []
    for x in items:
        c = x["code"]
        # 【저가매수형】 자격 — 시장 국면에 안 흔들리는 절대 기준만.
        #   문턱을 조이면 상승장에서 0개가 된다(실측: 5개 시점 중 2개가 0). 개수는 ROE 순위로 자른다.
        if (x.get("op_2q_pos")                                    # ①실적이 살아있다
                and x.get("eps_up")                               # ②실적이 늘고 있다
                and (x.get("earn_yield") or 0) >= VP_EY_MIN       # ③주가가 낮다
                and x.get("ma5_above")                            #  반등 시작
                and x.get("dd_close") is not None
                and x["dd_close"] >= VP_DD_FLOOR                  #  붕괴 배제
                and (x.get("peak_days") is not None
                     and x["peak_days"] <= VP_PEAK_DAYS)          #  고점이 최근 = 단기 눌림
                and x.get("excess_dd") is not None
                and x["excess_dd"] <= VP_EXCESS_MAX):             # ④지수보다 확실히 못 갔다
            value_cand.append(x)
        # 【모멘텀형】 ★현행 유지 — 시장 국면에 따라 개수가 고무줄인 게 정상이다
        if (x["daily_aligned"]
                and x["entry_days"] <= 20
                and p1.get(c) is not None and p1[c] > 80
                and (x["v2060"] or 0) > 1.0):
            momentum.append(x)

    # 7) 저가매수형 — ROE(잘 번다) 순으로 상위 N개 고정
    value_cand.sort(key=lambda z: -(z.get("roe_ann") or -9e9))
    value_pick = value_cand[:VP_TOP_N]
    vp_qualified = len(value_cand)

    # 8) 라벨 부착 (통과분에만)
    for lst in (value_pick, momentum):
        for x in lst:
            sup = supply_map.get(x["code"])
            x["labels"] = {
                "supply": sup,   # None이면 수급 신호 없음 — 그것도 정보다
                "valuation": {
                    "per": x.get("per"), "sectorPer": x.get("sectorPer"),
                    # ★업종PER 무효 24%(음수·200초과) — 무효면 절대PER·동종 개별로 대체
                    "sector_valid": bool(x.get("sectorPer") and 0 < x["sectorPer"] < 200),
                    "pbr": x.get("pbr"), "dps": x.get("dps"),
                },
            }
            for k in ("ma5", "ma60", "symbol"):
                x.pop(k, None)

    # ★저가매수형은 이미 ROE 순으로 정렬됨 — 다시 정렬하지 마라
    momentum.sort(key=lambda z: z.get("entry_days", 999))

    stats = {
        "universe": len(univ), "mcap_cut": mcap_cut,
        "top_n": len(ranked), "profit": len(profit_ok), "valid": len(items),
        "value_pick": len(value_pick), "vp_qualified": vp_qualified, "vp_cap": VP_TOP_N,
        "momentum": len(momentum),
        "elapsed": round(time.time() - t0, 1),
    }
    print(f"  → 저가매수 {len(value_pick)}(자격 {vp_qualified}) / 모멘텀 {len(momentum)}"
          f"  ({stats['elapsed']}초)", flush=True)
    return {"value_pick": value_pick, "momentum": momentum, "stats": stats}
