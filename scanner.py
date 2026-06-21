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
- 결과를 data.json 으로 저장
실행: python scanner.py
---------------------------------------------------------------
"""
import re, ast, time, json, html, os
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
HIST_DAYS   = 60          # 수급·외인소진율 일별 이력 보관 일수(토스 size=60 → "외국인 비중 길게")

# 내 보유 종목(포트폴리오) — 수급 필터와 무관하게 항상 처리/표시
#  ptype: stock=일반주 표준 / single_lev=단일종목 레버리지(원종목 리포트 재사용)
#         index_lev=지수 레버리지(기초 수급·추세) / sector_lev=섹터 레버리지(기초+업황)
PORTFOLIO = [
    {"code": "005930", "name": "삼성전자",   "market": "KOSPI",  "ptype": "stock"},
    {"code": "000660", "name": "SK하이닉스", "market": "KOSPI",  "ptype": "stock"},
    {"code": "440110", "name": "파두",       "market": "KOSDAQ", "ptype": "stock"},
    {"code": "402340", "name": "SK스퀘어",   "market": "KOSPI",  "ptype": "stock"},
    {"code": "009150", "name": "삼성전기",   "market": "KOSPI",  "ptype": "stock"},
    {"code": "074600", "name": "원익QnC",    "market": "KOSDAQ", "ptype": "stock"},
    {"code": "823410", "name": "KODEX 삼성전자레버리지",   "market": "KOSPI", "ptype": "single_lev", "underlying": "005930", "under_name": "삼성전자"},
    {"code": "823420", "name": "KODEX SK하이닉스레버리지", "market": "KOSPI", "ptype": "single_lev", "underlying": "000660", "under_name": "SK하이닉스"},
    {"code": "122630", "name": "KODEX 레버리지",            "market": "KOSPI", "ptype": "index_lev",  "basis": "코스피200"},
    {"code": "580023", "name": "키움 레버리지 전력TOP5 ETN", "market": "KOSPI", "ptype": "sector_lev", "basis": "전력기기 섹터"},
]
# ===============

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HDR_NAVER = {"User-Agent": UA, "Referer": "https://finance.naver.com/"}
HDR_TOSS  = {"User-Agent": UA, "Accept": "application/json",
             "Referer": "https://www.tossinvest.com/"}

TOSS_URL = ("https://wts-info-api.tossinvest.com/api/v1/stock-infos/trade/trend/"
            "trading-trend?productCode=A{code}&size=60")
CHART_URL = ("https://m.stock.naver.com/front-api/external/chart/domestic/info"
             "?symbol={code}&requestType=1&startTime={start}&endTime={end}&timeframe={tf}")
INTEG_URL = "https://m.stock.naver.com/api/stock/{code}/integration"
NEWS_URL  = "https://api.stock.naver.com/news/stock/{code}?pageSize=6&page=1"
FIN_A_URL = "https://m.stock.naver.com/api/stock/{code}/finance/annual"
FIN_Q_URL = "https://m.stock.naver.com/api/stock/{code}/finance/quarter"
DISC_URL  = "https://m.stock.naver.com/api/stock/{code}/disclosure?pageSize=10&page=1"
REPORT_DIR = "report-data"

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

def port_supply(code):
    """포트폴리오 표기용: 외국인·연기금 최근 WINDOW일 누적·매수일·필터통과 여부."""
    tr = get_trend(code)
    if not tr:
        return None
    f_series, p_series = tr
    f_ok, f_cum, f_bd = judge(f_series)
    p_ok, p_cum, p_bd = judge(p_series)
    return {
        "foreign_net_10d": int(f_cum), "foreign_buydays": f"{f_bd}/{WINDOW}", "foreign_pass": f_ok,
        "pension_net_10d": int(p_cum), "pension_buydays": f"{p_bd}/{WINDOW}", "pension_pass": p_ok,
    }

def toi(x):
    try:
        return int(float(str(x).replace(",", "").replace("+", "").strip()))
    except Exception:
        return 0

INV_FIELDS = [
    ("netIndividualsBuyVolume", "개인"),
    ("netForeignerBuyVolume", "외국인"),
    ("netInstitutionBuyVolume", "기관계"),
    ("netFinancialInvestmentBuyVolume", "금융투자"),
    ("netTrustBuyVolume", "투신"),
    ("netPrivateEquityFundBuyVolume", "사모"),
    ("netInsuranceBuyVolume", "보험"),
    ("netBankBuyVolume", "은행"),
    ("netOtherFinancialInstitutionsBuyVolume", "기타금융"),
    ("netPensionFundBuyVolume", "연기금"),
    ("netOtherCorporationBuyVolume", "기타법인"),
]

def build_supply_detail(body):
    """토스 일별 투자자별 → 최근 HIST_DAYS일 + 누적(5/20/60일) by 투자주체 + 외인보유율 추이."""
    if not body:
        return None
    daily = []
    for r in body[:HIST_DAYS]:
        rec = {"date": r.get("baseDate"), "close": toi(r.get("close")),
               "foreignRatio": r.get("foreignerRatio")}
        for k, lab in INV_FIELDS:
            rec[lab] = toi(r.get(k))
        daily.append(rec)
    def cum(n):
        return {lab: sum(toi(row.get(k)) for row in body[:n]) for k, lab in INV_FIELDS}
    fr = [{"date": r.get("baseDate"), "ratio": r.get("foreignerRatio")} for r in body[:HIST_DAYS]]
    return {"recent_daily": daily, "cum_5d": cum(5), "cum_20d": cum(20), "cum_60d": cum(60),
            "foreign_ratio_trend": fr}

def compute_tf(candles, ma_list, recent_n):
    """주봉/월봉 등 일반 타임프레임 블록: 이평·정배열·구간내 위치·최근봉."""
    closes = [float(r[4]) for r in candles]
    if len(closes) < 2:
        return None
    now = closes[-1]
    mas = {f"ma{w}": (round(mean(closes[-w:]), 1) if len(closes) >= w else None) for w in ma_list}
    vals = [mas[f"ma{w}"] for w in ma_list]
    aligned = all(v is not None for v in vals) and all(vals[i] > vals[i + 1] for i in range(len(vals) - 1))
    hi, lo = max(closes), min(closes)
    pos = round((now - lo) / (hi - lo) * 100, 1) if hi > lo else None
    recent = [{"d": r[0], "c": round(float(r[4]))} for r in candles[-recent_n:]]
    return {"now": round(now), "ma": mas, "aligned": aligned,
            "rangeHigh": round(hi), "rangeLow": round(lo), "posPct": pos, "recent": recent}

def fetch_all(code):
    """원본 응답 한 번에(일/주/월봉 + integration + 실적 + 공시 + 뉴스 + 토스 투자자별). 개별 실패 허용."""
    end = datetime.now().strftime("%Y%m%d")
    start   = (datetime.now() - timedelta(days=CANDLE_DAYS)).strftime("%Y%m%d")
    start_w = (datetime.now() - timedelta(days=365 * 5)).strftime("%Y%m%d")
    start_m = (datetime.now() - timedelta(days=365 * 10)).strftime("%Y%m%d")
    spec = [
        ("candles",   CHART_URL.format(code=code, start=start,   end=end, tf="day"),   True),
        ("candles_w", CHART_URL.format(code=code, start=start_w, end=end, tf="week"),  True),
        ("candles_m", CHART_URL.format(code=code, start=start_m, end=end, tf="month"), True),
        ("integ",   INTEG_URL.format(code=code), False),
        ("fin_a",   FIN_A_URL.format(code=code), False),
        ("fin_q",   FIN_Q_URL.format(code=code), False),
        ("disc",    DISC_URL.format(code=code), False),
        ("news",    NEWS_URL.format(code=code), False),
    ]
    out = {}
    for key, url, as_text in spec:
        try:
            r = requests.get(url, headers=nhdr(code), timeout=10)
            out[key] = r.text if as_text else r.json()
        except Exception as e:
            out[key] = None
            out[key + "_err"] = str(e)
    # 토스 투자자별 상세
    try:
        tr = requests.get(TOSS_URL.format(code=code), headers=HDR_TOSS, timeout=10)
        out["toss"] = tr.json().get("result", {}).get("body", [])
    except Exception as e:
        out["toss"] = None
        out["toss_err"] = str(e)
    return out

def write_report_file(code, name, market, supply, raw, price_block,
                      week_block=None, month_block=None, supply_detail=None):
    """리포트용 원본 풀데이터 → report-data/{code}.json.
    [방어] 어느 한 필드(밸류/컨센/실적/뉴스 등)가 깨져도 파일 전체 저장은 실패하지 않는다.
           실패한 필드와 사유는 _errors에 남겨 화면/로그에서 바로 보이게 한다.
    [원자적] 임시파일에 쓴 뒤 교체 → 부분쓰기로 인한 손상/스테일 방지."""
    errs = {k: v for k, v in raw.items() if k.endswith("_err")}   # fetch 단계 네트워크 에러

    def g(label, fn):
        """필드 추출 안전화: 예외 나면 None + _errors에 기록(어느 필드가 왜 깨졌는지 가시화)."""
        try:
            return fn()
        except Exception as e:
            errs[label] = f"{type(e).__name__}: {e}"
            return None

    integ = raw.get("integ") or {}
    payload = {
        "code": code, "name": name, "market": market,
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "supply_10d": supply,                                  # 외인/연기금 최근10일 (토스)
        "supply_detail": supply_detail,                        # 투자자별 세부 + 외인보유율 추이
        "price_daily": price_block,                            # 일봉
        "price_weekly": week_block,                            # 주봉
        "price_monthly": month_block,                          # 월봉
        "valuation":          g("valuation",          lambda: integ.get("totalInfos")),
        "consensus":          g("consensus",          lambda: integ.get("consensusInfo")),
        "researches":         g("researches",         lambda: integ.get("researches")),
        "industry_peers":     g("industry_peers",     lambda: integ.get("industryCompareInfo")),
        "financials_annual":  g("financials_annual",  lambda: (raw.get("fin_a") or {}).get("financeInfo")),
        "financials_quarter": g("financials_quarter", lambda: (raw.get("fin_q") or {}).get("financeInfo")),
        "disclosures":        raw.get("disc"),                 # 공시 (원본)
        "news":               g("news",               lambda: parse_news(raw["news"]) if raw.get("news") else None),
        "_errors": errs,
    }

    os.makedirs(REPORT_DIR, exist_ok=True)
    final = os.path.join(REPORT_DIR, f"{code}.json")
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)   # default=str: 직렬화 불가 값도 안전
    os.replace(tmp, final)   # 원자적 교체(부분쓰기 방지)

def enrich_stock(code, name="", market="", supply=None):
    """원본 풀데이터(일/주/월봉 + 투자자별 세부 포함)를 종목별 파일로 저장, 화면용 요약 반환."""
    raw = fetch_all(code)
    # 일봉 가격블록
    price_block = None
    if raw.get("candles"):
        try:
            price_block = compute_price_block(parse_candles(raw["candles"]))
        except Exception:
            price_block = None
    # 주봉/월봉 블록
    week_block = month_block = None
    try:
        if raw.get("candles_w"):
            week_block = compute_tf(parse_candles(raw["candles_w"]), [5, 10, 20, 60], 26)
    except Exception:
        week_block = None
    try:
        if raw.get("candles_m"):
            month_block = compute_tf(parse_candles(raw["candles_m"]), [6, 12, 24], 24)
    except Exception:
        month_block = None
    # 투자자별 세부 수급
    supply_detail = None
    try:
        if raw.get("toss"):
            supply_detail = build_supply_detail(raw["toss"])
    except Exception:
        supply_detail = None
    # 리포트 파일 저장
    try:
        write_report_file(code, name, market, supply or {}, raw, price_block,
                          week_block, month_block, supply_detail)
    except Exception as e:
        print(f"  report 파일 저장 실패 {code}: {e}", flush=True)
    # 화면용 요약(기존과 동일)
    out = {}
    now_price = None
    if price_block:
        out.update(price_block); now_price = price_block["now"]
    if raw.get("integ"):
        try:
            out.update(parse_integration(raw["integ"], now_price))
        except Exception as e:
            out["_integErr"] = str(e)
    if raw.get("news"):
        try:
            out["news"] = parse_news(raw["news"])
        except Exception as e:
            out["_newsErr"] = str(e)
    return out

def main():
    print("스캔 시작:", datetime.now().strftime("%Y-%m-%d %H:%M"), flush=True)

    # [#3 우선 처리] 내 종목(포트폴리오)을 유니버스 스캔보다 '먼저' 돌린다.
    #   → 뒤의 대량 스캔이 느려지거나 중간에 멈춰도(취소/스로틀) 보유종목 리포트는 항상 최신.
    #   → 일시적 실패엔 1회 재시도해서 보유종목이 누락되지 않게.
    print(f"포트폴리오 {len(PORTFOLIO)}개 처리(우선)…", flush=True)
    portfolio = []
    for it in PORTFOLIO:
        code, name, ptype = it["code"], it["name"], it["ptype"]
        entry = {"code": code, "name": name, "market": it.get("market", ""), "ptype": ptype}
        if "basis" in it:
            entry["basis"] = it["basis"]
        ok = False
        for attempt in (1, 2):
            try:
                if ptype == "single_lev":
                    # 리포트는 원종목 파일 재사용(중복 스크랩 안 함). 카드 수급은 원종목 기준.
                    entry["underlying"] = it["underlying"]
                    entry["under_name"] = it["under_name"]
                    entry["supply"] = port_supply(it["underlying"])
                else:
                    # stock / index_lev / sector_lev: 직접 수급 + enrich(원본 리포트 파일 생성)
                    sup = port_supply(code)
                    entry["supply"] = sup
                    entry["enrich"] = enrich_stock(code, name, it.get("market", ""), sup or {})
                ok = True
                break
            except Exception as e:
                print(f"  포트 처리 실패 {code} (시도 {attempt}/2): {e}", flush=True)
                time.sleep(1.0)
        if not ok and ptype != "single_lev":
            entry["enrich"] = None
        time.sleep(ENRICH_DELAY)
        portfolio.append(entry)

    # [유니버스 스캔] 외국인·연기금 수급 필터
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

    # [동시(both) 종목에만 분석 데이터 부착 + 원본 풀데이터 파일 저장]
    print(f"동시 {len(both)}개 분석 데이터 수집…", flush=True)
    for b in both:
        try:
            b["enrich"] = enrich_stock(
                b["code"], b["name"], b["market"],
                {"foreign_net_10d": b["f_net"], "foreign_buydays": b["f_buydays"],
                 "pension_net_10d": b["p_net"], "pension_buydays": b["p_buydays"]})
        except Exception as e:
            b["enrich"] = None
            print(f"  enrich 실패 {b['code']}: {e}", flush=True)
        time.sleep(ENRICH_DELAY)

    # [#2 정리] report-data 는 '현재 0순위(both) + 보유종목'만 유지.
    #   → 0순위에서 탈락한 종목의 옛 파일은 삭제(스테일 스냅샷 제거). .tmp 잔해도 제거.
    keep = {b["code"] for b in both}
    for it in PORTFOLIO:
        keep.add(it["underlying"] if it["ptype"] == "single_lev" else it["code"])
    removed = 0
    if os.path.isdir(REPORT_DIR):
        for fn in os.listdir(REPORT_DIR):
            path = os.path.join(REPORT_DIR, fn)
            if fn.endswith(".json.tmp"):
                try: os.remove(path)
                except Exception: pass
            elif fn.endswith(".json") and fn[:-5] not in keep:
                try:
                    os.remove(path); removed += 1
                    print(f"  report 정리 삭제(탈락): {fn[:-5]}", flush=True)
                except Exception as e:
                    print(f"  report 삭제 실패 {fn}: {e}", flush=True)
    print(f"report 정리: {removed}개 삭제, 유지 {len(keep)}개", flush=True)

    result = {
        "updated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "window": WINDOW, "buy_ratio": int(BUY_RATIO_MIN * 100),
        "scanned": len(kept),
        "foreign": foreign_pass,
        "pension": pension_pass,
        "both": both,
        "portfolio": portfolio,
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"완료. 외국인 {len(foreign_pass)} / 연기금 {len(pension_pass)} / 동시 {len(both)} / 포트 {len(portfolio)} → data.json", flush=True)

if __name__ == "__main__":
    main()
