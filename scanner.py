# -*- coding: utf-8 -*-
"""
외국인·연기금 수급 스캐너 + 동시(both) 종목 분석 데이터 (Phase 1)
---------------------------------------------------------------
- 종목 명단: 네이버 시총순 (ETF·우선주·스팩 제외, 코스피 500 + 코스닥 300)
- 수급: 토스 trading-trend API (외국인·연기금 일별 순매수)
- 판정(각각 독립): 최근 WINDOW 거래일 누적 순매수>0 AND 순매수일 ≥ 70%
- 0순위(both) = 외국인 AND 연기금 둘 다 통과
- [신규] both 종목에만 분석 데이터(enrich) 부착:
    · 주가위치/추이/이평선(5·20·60·120)/매물대   ← 네이버 일봉
    · 밸류·포워드(PER·추정PER·PBR·EPS·추정EPS·BPS·배당)·컨센서스(목표주가·투자의견)·증권가리포트·업종비교  ← 네이버 integration
    · 최근 뉴스                                   ← 네이버 뉴스 API
    · 공매도는 Phase 2 (KRX)
- 결과를 data.json 으로 저장
실행: python scanner.py
---------------------------------------------------------------
"""
import re, ast, time, json, html
from statistics import mean
from datetime import datetime, timedelta
import requests

# ===== 설정 =====
KOSPI_N  = 500
KOSDAQ_N = 300
WINDOW   = 10
BUY_RATIO_MIN = 0.70
REQ_DELAY = 0.25          # 수급 스캔: 종목당 간격(초)
ENRICH_DELAY = 0.4        # enrich: 종목당 간격(초, both만이라 소수)
CANDLE_DAYS = 220         # 일봉 조회 일수(이평120 + 여유)
POCKET_BINS = 20          # 매물대 가격대 구간 수
POCKET_TOP  = 3           # 매물대 상위 N구간
# ===============

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDR_NAVER = {"User-Agent": UA, "Referer": "https://finance.naver.com/"}
HDR_TOSS  = {"User-Agent": UA, "Accept": "application/json",
             "Referer": "https://www.tossinvest.com/"}

TOSS_URL = ("https://wts-info-api.tossinvest.com/api/v1/stock-infos/trade/trend/"
            "trading-trend?productCode=A{code}&size=60")
CHART_URL = ("https://m.stock.naver.com/front-api/external/chart/domestic/info"
             "?symbol={code}&requestType=1&startTime={start}&endTime={end}&timeframe=day")
INTEG_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
NEWS_URL  = "https://api.stock.naver.com/news/stock/{code}?pageSize=6&page=1"

def nhdr(code):
    return {"User-Agent": UA,
            "Referer": f"https://m.stock.naver.com/domestic/stock/{code}/total",
            "Origin": "https://m.stock.naver.com",
            "Accept": "application/json, text/plain, */*"}

EXCLUDE_KEYWORDS = ["스팩", "KODEX", "TIGER", "PLUS", "ACE", "RISE", "SOL",
                    "KOSEF", "ARIRANG", "HANARO", "TIMEFOLIO", "KBSTAR",
                    "ETN", "선물", "레버리지", "인버스"]

def is_excluded(name):
    n = str(name)
    if re.search(r"우[A-C]?$", n):
        return True
    return any(kw in n for kw in EXCLUDE_KEYWORDS)

# ───────────────────────── 순수 파싱/계산 (네트워크 없이 테스트 가능) ─────────────────────────
def parse_num(s):
    """'27.36배'·'12,372원'·'377,000'·'47.63%' → float, 'N/A'·'-'·None → None"""
    if s is None:
        return None
    t = str(s).replace(",", "")
    m = re.search(r"-?\d+(?:\.\d+)?", t)
    return float(m.group()) if m else None

def parse_candles(raw):
    """일봉 텍스트배열 → [[date,o,h,l,c,v,foreignRate], ...] (오래된→최신)"""
    s = raw.strip()
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j < 0:
        return []
    arr = ast.literal_eval(s[i:j + 1])
    rows = arr[1:] if arr and isinstance(arr[0], list) and "날짜" in str(arr[0][0]) else arr
    return [r for r in rows if isinstance(r, list) and len(r) >= 6]

def compute_price_block(candles):
    closes = [float(r[4]) for r in candles]
    vols   = [float(r[5]) for r in candles]
    if len(closes) < 2:
        return None
    now, prev = closes[-1], closes[-2]
    def ma(n): return round(mean(closes[-n:]), 1) if len(closes) >= n else None
    m5, m20, m60, m120 = ma(5), ma(20), ma(60), ma(120)
    def vs(m): return round((now - m) / m * 100, 2) if m else None
    aligned = all(x is not None for x in (m5, m20, m60, m120)) and m5 > m20 > m60 > m120
    trend20 = round((now - closes[-21]) / closes[-21] * 100, 2) if len(closes) >= 21 else None
    # 매물대: (종가,거래량) 가격대 빈으로 집계 → 상위 구간
    pocket = []
    lo, hi = min(closes), max(closes)
    if hi > lo:
        size = (hi - lo) / POCKET_BINS
        bins = [0.0] * POCKET_BINS
        for c, v in zip(closes, vols):
            k = min(int((c - lo) / size), POCKET_BINS - 1)
            bins[k] += v
        tot = sum(bins) or 1
        order = sorted(range(POCKET_BINS), key=lambda k: bins[k], reverse=True)[:POCKET_TOP]
        for k in sorted(order):
            pocket.append({"low": round(lo + k * size), "high": round(lo + (k + 1) * size),
                           "volPct": round(bins[k] / tot * 100, 1)})
    return {
        "now": round(now), "prevClose": round(prev),
        "changePct": round((now - prev) / prev * 100, 2),
        "ma": {"ma5": m5, "ma20": m20, "ma60": m60, "ma120": m120, "aligned": aligned,
               "vs": {"ma5": vs(m5), "ma20": vs(m20), "ma60": vs(m60), "ma120": vs(m120)}},
        "trend20": trend20,
        "pocket": pocket,
    }

def parse_integration(obj, now_price=None):
    ti = {d.get("code"): d.get("value") for d in (obj.get("totalInfos") or [])}
    high52, low52 = parse_num(ti.get("highPriceOf52Weeks")), parse_num(ti.get("lowPriceOf52Weeks"))
    pos = None
    if high52 and low52 and now_price and high52 > low52:
        pos = round((now_price - low52) / (high52 - low52) * 100, 1)
    cons = obj.get("consensusInfo") or {}
    target = parse_num(cons.get("priceTargetMean"))
    upside = round((target - now_price) / now_price * 100, 1) if target and now_price else None
    reports = [{"firm": r.get("bnm"), "title": html.unescape(r.get("tit") or ""), "date": r.get("wdt")}
               for r in (obj.get("researches") or [])][:5]
    peers = []
    for p in (obj.get("industryCompareInfo") or []):
        if str(p.get("itemCode")) == str(obj.get("itemCode")):
            continue
        peers.append({"name": p.get("stockName"), "code": p.get("itemCode"),
                      "changePct": parse_num(p.get("fluctuationsRatio"))})
    return {
        "valuation": {
            "per": parse_num(ti.get("per")), "fwdPer": parse_num(ti.get("cnsPer")),
            "pbr": parse_num(ti.get("pbr")), "eps": parse_num(ti.get("eps")),
            "fwdEps": parse_num(ti.get("cnsEps")), "bps": parse_num(ti.get("bps")),
            "divYield": parse_num(ti.get("dividendYieldRatio")),
            "foreignRate": parse_num(ti.get("foreignRate")),
            "marketCap": ti.get("marketValue"),
        },
        "pos52w": {"high": high52, "low": low52, "pct": pos},
        "consensus": {"target": target, "upsidePct": upside,
                      "opinion": parse_num(cons.get("recommMean")), "date": cons.get("createDate")},
        "reports": reports,
        "peers": peers[:4],
    }

def parse_news(obj):
    items = []
    groups = obj if isinstance(obj, list) else [obj]
    for g in groups:
        if not isinstance(g, dict):
            continue
        for it in (g.get("items") or []):
            t = html.unescape((it.get("titleFull") or it.get("title") or "")).strip()
            if not t:
                continue
            items.append({"title": t, "office": it.get("officeName"),
                          "date": it.get("datetime"), "url": it.get("mobileNewsUrl")})
    return items[:6]

# ───────────────────────── 네트워크 fetch ─────────────────────────
def get_list(sosok, want):
    out, seen, page = [], set(), 1
    while len(out) < want and page <= 80:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
        r = requests.get(url, headers=HDR_NAVER, timeout=10); r.encoding = "euc-kr"
        codes = re.findall(r"/item/main\.naver\?code=(\d{6})", r.text)
        if not codes:
            break
        names = re.findall(r'/item/main\.naver\?code=\d{6}"[^>]*>\s*([^<>]+?)\s*</a>', r.text)
        for i, code in enumerate(codes):
            if code in seen:
                continue
            seen.add(code)
            name = names[i].strip() if i < len(names) else code
            if is_excluded(name):
                continue
            out.append((code, name))
            if len(out) >= want:
                break
        page += 1
        time.sleep(0.3)
    return out[:want]

def get_trend(code):
    try:
        r = requests.get(TOSS_URL.format(code=code), headers=HDR_TOSS, timeout=10)
        if r.status_code != 200:
            return None
        body = r.json().get("result", {}).get("body", [])
    except Exception:
        return None
    foreign = [row.get("netForeignerBuyVolume", 0) or 0 for row in body]
    pension = [row.get("netPensionFundBuyVolume", 0) or 0 for row in body]
    return foreign, pension

def judge(series):
    recent = series[:WINDOW]
    if len(recent) < WINDOW:
        return False, 0, 0
    cum = sum(recent); buy = sum(1 for x in recent if x > 0)
    return (cum > 0 and buy / WINDOW >= BUY_RATIO_MIN), cum, buy

def enrich_stock(code):
    """both 종목 1개 분석 데이터. 부분 실패 허용(개별 키 None/에러표시)."""
    out = {}
    now_price = None
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=CANDLE_DAYS)).strftime("%Y%m%d")
    # 1) 일봉 → 주가/이평선/매물대
    try:
        r = requests.get(CHART_URL.format(code=code, start=start, end=end), headers=nhdr(code), timeout=10)
        pb = compute_price_block(parse_candles(r.text))
        if pb:
            out.update(pb); now_price = pb["now"]
    except Exception as e:
        out["_priceErr"] = str(e)
    # 2) integration → 밸류/컨센서스/리포트/업종
    try:
        r = requests.get(INTEG_URL.format(code=code), headers=nhdr(code), timeout=10)
        out.update(parse_integration(r.json(), now_price))
    except Exception as e:
        out["_integErr"] = str(e)
    # 3) 뉴스
    try:
        r = requests.get(NEWS_URL.format(code=code), headers=nhdr(code), timeout=10)
        out["news"] = parse_news(r.json())
    except Exception as e:
        out["_newsErr"] = str(e)
    return out

def main():
    print("스캔 시작:", datetime.now().strftime("%Y-%m-%d %H:%M"), flush=True)
    kept  = [(c, n, "KOSPI")  for c, n in get_list(0, KOSPI_N)]
    kept += [(c, n, "KOSDAQ") for c, n in get_list(1, KOSDAQ_N)]
    print("스캔 대상(보통주):", len(kept), flush=True)

    foreign_pass, pension_pass = [], []
    for i, (code, name, mkt) in enumerate(kept, 1):
        tr = get_trend(code)
        if tr:
            f_series, p_series = tr
            f_ok, f_cum, f_bd = judge(f_series)
            p_ok, p_cum, p_bd = judge(p_series)
            if f_ok:
                foreign_pass.append({"market": mkt, "code": code, "name": name,
                                     "net": int(f_cum), "buydays": f"{f_bd}/{WINDOW}"})
            if p_ok:
                pension_pass.append({"market": mkt, "code": code, "name": name,
                                     "net": int(p_cum), "buydays": f"{p_bd}/{WINDOW}"})
        if i % 50 == 0:
            print(f"{i}/{len(kept)}", flush=True)
        time.sleep(REQ_DELAY)

    foreign_pass.sort(key=lambda x: x["net"], reverse=True)
    pension_pass.sort(key=lambda x: x["net"], reverse=True)
    f_map = {x["code"]: x for x in foreign_pass}
    p_map = {x["code"]: x for x in pension_pass}
    both_codes = set(f_map) & set(p_map)
    both = []
    for code in both_codes:
        f = f_map[code]; p = p_map[code]
        both.append({
            "market": f["market"], "code": code, "name": f["name"],
            "f_net": f["net"], "f_buydays": f["buydays"],
            "p_net": p["net"], "p_buydays": p["buydays"],
        })
    both.sort(key=lambda x: x["f_net"], reverse=True)

    # [신규] 동시 종목에만 분석 데이터 부착
    print(f"동시 {len(both)}개 분석 데이터 수집…", flush=True)
    for b in both:
        try:
            b["enrich"] = enrich_stock(b["code"])
        except Exception as e:
            b["enrich"] = None
            print(f"  enrich 실패 {b['code']}: {e}", flush=True)
        time.sleep(ENRICH_DELAY)

    result = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window": WINDOW, "buy_ratio": int(BUY_RATIO_MIN * 100),
        "scanned": len(kept),
        "foreign": foreign_pass,
        "pension": pension_pass,
        "both": both,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"완료. 외국인 {len(foreign_pass)} / 연기금 {len(pension_pass)} / 동시 {len(both)} → data.json", flush=True)

if __name__ == "__main__":
    main()
