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
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
try:
    import screener                      # 기술적 필터(저가매수형·모멘텀형). 없으면 스킵.
except Exception as _e:
    screener = None
    print(f"[알림] screener 모듈 로드 실패 — 기술적 필터 건너뜀: {_e}", flush=True)
KST = timezone(timedelta(hours=9))           # 한국 표준시(UTC+9)
def now_kst(): return datetime.now(KST)       # 사이트 표시용 타임스탬프
import requests

# ===== 설정 =====
KOSPI_N  = 500
KOSDAQ_N = 300
WINDOW   = 10
BUY_RATIO_MIN = 0.70
REQ_DELAY = 0.25          # (구) 순차 스캔용 — 병렬 전환 후 미사용
SCAN_WORKERS = 12         # 수급 스캔 병렬 스레드 (실측: 200종목 7.4초·실패0, 순차 대비 5.6배)
ENRICH_WORKERS = 4        # enrich 병렬 (종목당 API 6개 호출 → 워커 낮게. 파일쓰기는 원자적이라 안전)
ENRICH_DELAY = 0.4        # enrich: 종목당 간격(초, both만이라 소수)
CANDLE_DAYS = 220         # 일봉 조회 일수(이평120 + 여유)
POCKET_BINS = 20          # 매물대 가격대 구간 수
POCKET_TOP  = 3           # 매물대 상위 N구간
HIST_DAYS   = 60          # 수급·외인소진율 일별 이력 보관 일수(토스 size=60 → "외국인 비중 길게")

# 내 보유 종목(포트폴리오) — 수급 필터와 무관하게 항상 처리/표시
#  ptype: stock=일반주 표준 / single_lev=단일종목 레버리지(원종목 리포트 재사용)
#         index_lev=지수 레버리지(기초 수급·추세) / sector_lev=섹터 레버리지(기초+업황)
# [설계] 파생(레버·커버드콜)은 전부 '기초자산으로 치환'해 분석한다.
#   ref: 이 종목이 재사용할 '기초자산' 코드. ref가 있으면 수급·리포트를 ref 종목 것으로 씀
#        (자기 코드로는 스크랩 안 함). ref가 없으면 자기 자신이 본체.
#   ptype: 프롬프트 문구 선택용. stock=개별주 / index=지수(코스피200 등)
#   → 삼전·닉스 레버는 본주와 100% 동일(ref만 지정, ptype=stock).
#   → 200위클리커버드콜은 코스피200 데이터(122630) 재사용(카드는 별도, 데이터 1개).
PORTFOLIO = [
    {"code": "005930", "name": "삼성전자",   "market": "KOSPI",  "ptype": "stock"},
    {"code": "000660", "name": "SK하이닉스", "market": "KOSPI",  "ptype": "stock"},
    {"code": "440110", "name": "파두",       "market": "KOSDAQ", "ptype": "stock"},
    {"code": "402340", "name": "SK스퀘어",   "market": "KOSPI",  "ptype": "stock"},
    {"code": "009150", "name": "삼성전기",   "market": "KOSPI",  "ptype": "stock"},
    # 단일종목 레버 → 본주와 100% 동일(본주 수급·본주 리포트 재사용)
    {"code": "0193W0", "name": "KODEX 삼성전자단일종목레버리지",   "market": "KOSPI", "ptype": "stock", "ref": "005930"},
    {"code": "0193T0", "name": "KODEX SK하이닉스단일종목레버리지", "market": "KOSPI", "ptype": "stock", "ref": "000660"},
    # 지수 레버(코스피200) → 자기가 본체
    {"code": "122630", "name": "KODEX 레버리지",            "market": "KOSPI", "ptype": "index", "basis": "코스피"},
    # 코스피200 커버드콜 → 코스피200(122630) 데이터 재사용
    {"code": "498400", "name": "KODEX 200타겟위클리커버드콜", "market": "KOSPI", "ptype": "index", "basis": "코스피", "ref": "122630"},
    # 섹터 레버(ETN) → 구성종목 5개 수급을 묶어 '섹터 총괄' 분석. 데이터는 전용파일(sector_power) 1개.
    {"code": "760026", "name": "키움 레버리지 전력TOP5 ETN", "market": "KOSPI", "ptype": "sector",
     "basis": "전력설비 TOP5", "sector_key": "sector_power",
     "members": [
         {"code": "298040", "name": "효성중공업"},
         {"code": "267260", "name": "HD현대일렉트릭"},
         {"code": "010120", "name": "LS ELECTRIC"},
         {"code": "006260", "name": "LS"},
         {"code": "001440", "name": "대한전선"},
     ]},
    # 미국 종목 → 수급 데이터 원천 없음. 스캐너 미수집(카드만), 리포트는 본주를 웹 검색으로 분석.
    #   us_target: 분석 대상 본주(레버리지의 기초자산). us_ticker: 검색용 티커.
    {"code": "MVLL", "name": "마벨 2배 롱 (MVLL)", "market": "US", "ptype": "us",
     "us_target": "Marvell", "us_ticker": "MRVL", "basis": "Marvell(MRVL)"},
    {"code": "MUU", "name": "마이크론 2배 (MUU)", "market": "US", "ptype": "us",
     "us_target": "Micron Technology", "us_ticker": "MU", "basis": "Micron(MU)"},
    {"code": "SNXX", "name": "샌디스크 2배 (SNXX)", "market": "US", "ptype": "us",
     "us_target": "SanDisk", "us_ticker": "SNDK", "basis": "SanDisk(SNDK)"},
    {"code": "ACE500CC", "name": "ACE 미국500데일리타겟커버드콜", "market": "US", "ptype": "us",
     "us_target": "S&P500", "us_ticker": "^GSPC", "basis": "S&P500", "us_kind": "index"},
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
# 다음 재무(확정치 전용) — 네이버는 최신 분기를 컨센으로 채우므로 확정치는 여기서 받는다
DAUM_FIN_URL = "https://finance.daum.net/api/quote/A{code}/financials"
HDR_DAUM = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.daum.net/",
}
FIN_A_URL = "https://m.stock.naver.com/api/stock/{code}/finance/annual"
FIN_Q_URL = "https://m.stock.naver.com/api/stock/{code}/finance/quarter"
# ★공시는 다음에서 받는다 — 네이버는 10건뿐이고 날짜 필드가 비어 온다.
#   분기 단위 원인 추적(「매출액또는손익구조 변경」 등)이 안 돼 손익 어긋남을 못 밝힌다.
# ★일봉도 다음에서. 네이버 차트는 egress 차단(403)이라 캔들이 통째로 비어 온다.
DAUM_DAYS_URL = "https://finance.daum.net/api/quote/A{code}/days?perPage=500&page=1"
DISC_URL  = "https://finance.daum.net/api/disclosures?symbolCode=A{code}&perPage=40&page=1"
REPORT_DIR = "report-data"

# ── [추적] 예측 성적 추적 ────────────────────────────────────────────
#   리포트는 채팅에서 나오므로 자동 저장이 안 된다. 사람이 reports/{code}/{YYYY-MM-DD}.md 로 올리면
#   여기서 읽어 ① 다음 회차 리포트에 직전 판단을 실어주고(연속성) ② 목표 도달률을 매일 갱신한다.
#   대상은 관심종목 개별만 — 지수·섹터는 매크로 판단이라 예측 검증 대상이 아니다.
REPORTS_DIR = "reports"
TRACK_CODES = {"005930", "000660", "440110", "402340", "009150",   # 국내 개별
               "MRVL", "MU", "SNDK"}                                # 미국 개별(기초자산)

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

def _norm_d(v):
    """날짜를 YYYY-MM-DD로. 네이버는 20260814, 다음은 2026-08-14로 준다.
    ★이 차이 때문에 strptime이 예외를 던져 idxRel이 통째로 null이 됐던 적이 있다."""
    t = str(v)[:10].replace("/", "-").replace(".", "-")
    if len(t) == 8 and t.isdigit():
        return t[:4] + "-" + t[4:6] + "-" + t[6:]
    return t


def _resample(day, unit):
    """일봉 [[date,o,h,l,c,v],...] → 주봉('W')·월봉('M'). 기간 첫 시가, 최고/최저, 마지막 종가, 거래량 합."""
    if not day:
        return []
    buckets, order = {}, []
    for r in day:
        d = _norm_d(r[0])
        try:
            dt = datetime.strptime(d, "%Y-%m-%d")
        except Exception:
            continue
        key = dt.strftime("%Y-%m") if unit == "M" else "%d-W%02d" % dt.isocalendar()[:2]
        if key not in buckets:
            buckets[key] = [d, r[1], r[2], r[3], r[4], r[5] or 0]
            order.append(key)
        else:
            b = buckets[key]
            if r[2] is not None and (b[2] is None or r[2] > b[2]): b[2] = r[2]
            if r[3] is not None and (b[3] is None or r[3] < b[3]): b[3] = r[3]
            b[4] = r[4]
            b[5] = (b[5] or 0) + (r[5] or 0)
    return [buckets[k] for k in order]


def parse_candles(raw):
    # ★다음에서 받으면 이미 [[date,o,h,l,c,v],...] 형태다. 그대로 통과.
    if isinstance(raw, list):
        return [r for r in raw if isinstance(r, list) and len(r) >= 6]
    """일봉 텍스트배열 → [[date,o,h,l,c,v,foreignRate], ...] (오래된→최신)"""
    s = raw.strip()
    i, j = s.find("["), s.rfind("]")
    if i < 0 or j < 0:
        return []
    arr = ast.literal_eval(s[i:j + 1])
    rows = arr[1:] if arr and isinstance(arr[0], list) and "날짜" in str(arr[0][0]) else arr
    return [r for r in rows if isinstance(r, list) and len(r) >= 6]

_IDX_CACHE = {}

def _index_days(market="KOSPI"):
    """코스피·코스닥 일봉 {날짜: 종가}. 프로세스당 1회만 받는다."""
    mk = "KOSDAQ" if str(market).upper() == "KOSDAQ" else "KOSPI"
    if mk in _IDX_CACHE:
        return _IDX_CACHE[mk]
    # ★screener가 이미 받아뒀으면 재사용(중복 호출 제거)
    try:
        import screener as _scr
        if getattr(_scr, "_IDX_MEMO", None):
            got = _scr._IDX_MEMO.get(mk)
            if got:
                _IDX_CACHE[mk] = got
                return got
    except Exception:
        pass
    out = {}
    try:
        r = requests.get(f"https://finance.daum.net/api/market_index/days"
                         f"?page=1&perPage=320&market={mk}&pagination=true",
                         headers={"User-Agent": UA, "Referer": "https://finance.daum.net/"}, timeout=20)
        for k in (r.json().get("data") or []):
            if k.get("tradePrice"):
                out[(k.get("date") or "")[:10]] = k["tradePrice"]
    except Exception as e:
        print(f"  [경고] 지수 일봉 실패 {mk}: {e}", flush=True)
    _IDX_CACHE[mk] = out
    return out


def _index_at(mk, date):
    ks = _index_days(mk)
    if not ks:
        return None
    if date in ks:
        return ks[date]
    prev = [d for d in ks if d <= date]
    return ks[max(prev)] if prev else None


def compute_index_relative(candles, market="KOSPI"):
    """★주가 위치를 지수와 나란히 — 혼자 빠진 건지 시장 따라간 건지 구분하려면 필수.
    candles: [[date, o, h, l, c, v], ...] 오래된순
    """
    try:
        rows = [(_norm_d(r[0]), float(r[4])) for r in candles if r and r[4]]
        if len(rows) < 30:
            return None
        base_d, now = rows[-1]
        cut = (datetime.strptime(base_d, "%Y-%m-%d") - timedelta(days=365)).strftime("%Y-%m-%d")
        yr = [x for x in rows if x[0] >= cut] or rows
        pk_d, pk_c = max(yr, key=lambda x: x[1])
        dd = (now / pk_c - 1) * 100
        i_now, i_pk = _index_at(market, base_d), _index_at(market, pk_d)
        out = {"peakClose": round(pk_c), "peakDate": pk_d, "ddFromPeak": round(dd, 1)}
        if i_now and i_pk:
            ic = (i_now / i_pk - 1) * 100
            out["idxSincePeak"] = round(ic, 1)
            out["excessDd"] = round(dd - ic, 1)
        # 1개월·6개월 종목 vs 지수
        for lab, n in (("m1", 21), ("m6", 126)):
            if len(rows) > n:
                d0, c0 = rows[-1 - n]
                st = (now / c0 - 1) * 100
                i0 = _index_at(market, d0)
                out[lab] = round(st, 1)
                if i_now and i0:
                    out[lab + "Idx"] = round((i_now / i0 - 1) * 100, 1)
                    out[lab + "Excess"] = round(st - (i_now / i0 - 1) * 100, 1)
        return out
    except Exception:
        return None


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
            "marketCap": parse_num(ti.get("marketValue")),   # ★문자열이면 시총이 0으로 잡힌다(실측 7종목)
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

# ───────────────────── v3 파생 계산 (리포트 정확도용) ─────────────────────
EARN_PAT = re.compile(r"영업\s*\(?잠정\)?\s*실적|잠정실적|매출액또는손익구조")

def _daum_fin(d):
    """다음 financials 응답을 정리. 확정치만 들어있고 미공시 분기는 없다.
    반환: {"quarter":[{date,sales,operatingProfit,netIncome,eps,roe,debtRatio,dps,opm}...], "annual":[...], "source":"daum"}
    """
    if not d:
        return None
    def rows(lst):
        out = []
        for r in (lst or []):
            try:
                sales = r.get("sales")
                op = r.get("operatingProfit")
                out.append({
                    "date": r.get("date"),
                    "sales": sales,
                    "operatingProfit": op,
                    "netIncome": r.get("netIncome"),
                    "eps": r.get("eps"),
                    "roe": r.get("roe"),
                    "debtRatio": r.get("debtRatio"),
                    "dps": r.get("dividendPerShare"),
                    # 영업이익률(%) — 마진 추이 판단용
                    "opm": (round(op / sales * 100, 2) if (sales and op is not None) else None),
                })
            except Exception:
                continue
        return out
    q, y = rows(d.get("QUARTER")), rows(d.get("YEAR"))
    if not q and not y:
        return None
    return {"quarter": q, "annual": y, "source": "daum(확정치)",
            "note": "미공시 분기는 없음. 네이버 financials는 최신 분기를 컨센으로 채우므로 실적 판단은 이 필드 기준."}


def detect_earnings_alert(disc, fin_q):
    """[핵심] 공시에 '잠정실적'이 떴는데 financials_quarter는 아직 컨센(isConsensus=Y)인
    상태를 탐지한다. 이걸 놓치면 컨센을 실적으로 오독한다(풍산 +50.9%, 파두 +89.2% 사례).
    반환: {disclosed: bool, items: [...], consensus_cols: [...], mismatch: bool}
    """
    out = {"disclosed": False, "items": [], "consensus_cols": [], "mismatch": False}
    try:
        for d in (disc or []):
            t = d.get("title") or ""
            if EARN_PAT.search(t):
                out["items"].append({"title": t, "datetime": d.get("datetime")})
        out["disclosed"] = bool(out["items"])
        for c in ((fin_q or {}).get("trTitleList") or []):
            if c.get("isConsensus") == "Y":
                out["consensus_cols"].append(c.get("title"))
        out["mismatch"] = out["disclosed"] and bool(out["consensus_cols"])
    except Exception as e:
        out["_err"] = f"{type(e).__name__}: {e}"
    return out

def enhance_supply(sd):
    """수급 파생지표. 21~60일 역산 / 매집 패턴 / 외국인 평균단가 / 소진율 정합성."""
    if not sd:
        return None
    try:
        c5, c20, c60 = sd.get("cum_5d") or {}, sd.get("cum_20d") or {}, sd.get("cum_60d") or {}
        c2160 = {k: (c60.get(k, 0) - c20.get(k, 0)) for k in c60}

        def pattern(lab):
            a, b, c = c5.get(lab, 0), c20.get(lab, 0), c60.get(lab, 0)
            d = c2160.get(lab, 0)
            if a > 0 and b > 0 and c > 0 and d > 0: return "꾸준매집"
            if b > 0 and c < 0:                     return "반전(복원중)"
            if a > 0 and b < 0 and c > 0:           return "U자형"
            if a < 0 and b < 0 and c < 0:           return "일관매도"
            if a < 0 and b > 0:                     return "당일이탈"
            return "혼조"

        daily = sd.get("recent_daily") or []
        # 외국인 평균단가 역산(일별 종가 가중) — '저가매집'인지 '고가에 물린 것'인지 판별
        bq = ba = sq = sa = 0
        for r in daily:
            q, p = r.get("외국인", 0), r.get("close", 0)
            if not p: continue
            if q > 0: bq += q; ba += q * p
            elif q < 0: sq += -q; sa += -q * p
        net = bq - sq
        cur = daily[0].get("close") if daily else None
        closes = [r.get("close") for r in daily if r.get("close")]
        lo_c, hi_c = (min(closes), max(closes)) if closes else (None, None)
        # [가드] 순매수가 총매수 대비 너무 작으면 가중평균이 폭발한다(카카오 2.6% 사례).
        #        결과가 기간 종가 범위를 벗어나도 무효 처리한다.
        net_ratio = round(abs(net) / bq * 100, 1) if bq else None
        favg = None; favg_note = None
        if net:
            cand = round((ba - sa) / net)
            if lo_c and hi_c and lo_c <= cand <= hi_c and (net_ratio or 0) >= 10:
                favg = cand
            else:
                favg_note = ("순매수 비중 %.1f%% (과소)" % net_ratio) if (net_ratio is not None and net_ratio < 10) \
                            else "산출값이 기간 종가 범위 밖 → 무효"

        fr = sd.get("foreign_ratio_trend") or []
        vals = [x.get("ratio") for x in fr if x.get("ratio") is not None]
        fr_delta = round(vals[0] - vals[-1], 2) if len(vals) >= 2 else None

        return {
            "cum_21_60d": c2160,
            "pattern": {lab: pattern(lab) for lab in ("외국인", "연기금", "기관계", "개인", "사모", "금융투자")},
            "foreign_avg_price": favg,
            "foreign_avg_note": favg_note,
            "foreign_avg_vs_now_pct": (round((cur / favg - 1) * 100, 1) if (favg and cur) else None),
            "foreign_buy_qty": bq, "foreign_sell_qty": sq, "foreign_net_qty": net,
            "foreign_net_ratio_pct": net_ratio,
            "close_range": {"low": lo_c, "high": hi_c},
            "foreign_ratio_delta_60d": fr_delta,
            "foreign_ratio_max": (max(vals) if vals else None),
            "foreign_ratio_min": (min(vals) if vals else None),
            "foreign_ratio_is_60d_high": (bool(vals) and vals[0] >= max(vals)),
        }
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e}"}

def compute_derived_valuation(total_infos, fin_a):
    """이론 PBR = (ROE-g)/(COE-g). PBR 절대값이 아니라 '이론값 대비 %'로 판단하기 위함.
    (삼양식품 PBR 6.34/ROE 37.6% → 이론 10.9 = -42% 할인. 절대값만 보면 오판)"""
    try:
        ti = {d.get("key"): d.get("value") for d in (total_infos or [])}
        def num(x):
            try: return float(str(x).replace(",", "").replace("%", "").replace("배", "").strip())
            except Exception: return None
        pbr = num(ti.get("PBR"))
        # [중요] columns dict의 키 순서는 신뢰할 수 없다. trTitleList 순서를 기준으로 잡고
        #        실적(isConsensus != Y)과 컨센(Y)을 분리해서 각각 확보한다.
        titles = (fin_a or {}).get("trTitleList") or []
        order  = [(c.get("key"), c.get("title"), c.get("isConsensus")) for c in titles]
        roe_act = roe_est = None; roe_act_yr = roe_est_yr = None
        for row in ((fin_a or {}).get("rowList") or []):
            if "ROE" not in (row.get("title") or ""): continue
            cols = row.get("columns") or {}
            for k, t, isc in order:
                v = num((cols.get(k) or {}).get("value"))
                if v is None: continue
                if isc == "Y": roe_est, roe_est_yr = v / 100.0, t
                else:          roe_act, roe_act_yr = v / 100.0, t
            break
        roe = roe_est if roe_est is not None else roe_act      # 포워드 우선
        if pbr is None or roe is None:
            return {"pbr": pbr, "roe_pct": None, "note": "산출 불가"}
        # [주의] 영구성장 모형은 ROE가 높을수록 폭발한다(하이닉스 101% → 이론 PBR 32배).
        #        사이클 정점의 ROE는 영속 가정이 성립하지 않으므로 지속가능 ROE로 캡을 씌운다.
        ROE_CAP = 0.25
        roe_used = min(roe, ROE_CAP)
        capped = roe > ROE_CAP
        out = {"pbr": pbr, "roe_pct": round(roe * 100, 2),
               "roe_used_pct": round(roe_used * 100, 2), "roe_capped": capped,
               "roe_basis": (roe_est_yr + "(E)") if roe_est is not None else roe_act_yr,
               "roe_actual_pct": (round(roe_act * 100, 2) if roe_act is not None else None),
               "roe_actual_basis": roe_act_yr,
               "roe_est_pct": (round(roe_est * 100, 2) if roe_est is not None else None)}
        # 기준: COE 8% / g 3% (보수). 민감도로 g 5%, COE 9% 병기.
        for coe, g, tag in ((0.08, 0.03, "base"), (0.08, 0.05, "g5"), (0.09, 0.03, "coe9")):
            if roe_used > g and coe > g:
                t = (roe_used - g) / (coe - g)
                out[tag] = {"coe": coe, "g": g, "theo_pbr": round(t, 2),
                            "premium_pct": round((pbr / t - 1) * 100, 1)}
        # [자본훼손 종목] PBR이 극단이면 분모(BPS)가 무너진 것. 이론 PBR 프레임 자체가 무효다.
        if pbr > 15:
            out["verdict"] = "PBR 무효(자본훼손)"
            out["note"] = "BPS가 훼손돼 PBR이 의미 없다. 포워드 PER·PEG로 판단하라."
            return out
        # [밴드] 고ROE의 지속가능성은 판단이 갈린다. 단일값 대신 밴드로 제시하고
        #        현재 PBR이 밴드 어디에 있는지로 말한다.
        theos = [v["theo_pbr"] for k, v in out.items() if isinstance(v, dict) and "theo_pbr" in v]
        if capped and roe > ROE_CAP:
            try:
                theos.append(round((roe - 0.05) / (0.08 - 0.05), 2))   # 무캡 상단(낙관)
            except Exception:
                pass
        if theos:
            lo, hi = min(theos), max(theos)
            out["theo_band"] = {"low": lo, "high": hi}
            if pbr < lo:      pos, vd = round((pbr / lo - 1) * 100, 1), "저평가"
            elif pbr > hi:    pos, vd = round((pbr / hi - 1) * 100, 1), "고평가"
            else:             pos, vd = round((pbr - lo) / (hi - lo) * 100, 1), "밴드 내"
            out["band_pos"] = pos
            out["verdict"] = vd
            if vd == "밴드 내":
                out["note"] = ("이론 PBR %.2f~%.2f 밴드의 %.0f%% 지점. ROE 지속가능성이 판단을 가른다."
                               % (lo, hi, pos))
            elif capped:
                out["note"] = "ROE %.1f%% → 지속가능 25%%로 캡 적용" % (roe * 100)
        elif roe_used <= 0.03:
            out["verdict"] = "산출불가(ROE≤g)"
            out["note"] = "ROE가 영구성장률 이하 → 이론 PBR 1배 미만이 정상"
        return out
    except Exception as e:
        return {"_err": f"{type(e).__name__}: {e}"}

def compute_volume_stats(candles):
    """거래량 에너지. 20일/60일 비율 + 당일 배수. 얇은 거래량 급등=매물소진 판별용."""
    try:
        vols = [float(r[5]) for r in candles]
        if len(vols) < 61: return None
        v20 = sum(vols[-21:-1]) / 20
        v60 = sum(vols[-61:-1]) / 60
        today = vols[-1]
        return {"avg20": round(v20), "avg60": round(v60),
                "ratio_20_60": round(v20 / v60, 2) if v60 else None,
                "today": round(today),
                "today_vs_60": round(today / v60, 2) if v60 else None}
    except Exception:
        return None

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

def supply_cum_summary(sd):
    """[C-A49b] data.json 목록용 구간별 수급 요약.
    report-data 를 열지 않고도 5/20/60일 방향이 갈리는지 판정할 수 있게, 외국인·연기금만 압축해 싣는다.
    (전 주체 11개와 일별 60일치는 종전대로 report-data 에만 둔다 — 목록이 비대해지면 안 되므로.)"""
    if not sd:
        return None
    out = {}
    for win in ("5d", "20d", "60d"):
        c = sd.get("cum_" + win) or {}
        out["f" + win] = c.get("외국인")
        out["p" + win] = c.get("연기금")
    f5, f20, f60 = out.get("f5d"), out.get("f20d"), out.get("f60d")
    # 방향 판정: 세 구간이 같은 방향인가, 전환 중인가, 차익실현 국면인가.
    if None in (f5, f20, f60):
        out["fdir"] = None
    elif f5 >= 0 and f20 >= 0 and f60 >= 0:
        out["fdir"] = "매수 지속"
    elif f5 <= 0 and f20 <= 0 and f60 <= 0:
        out["fdir"] = "매도 지속"
    elif f60 < 0 and (f20 > 0 or f5 > 0):
        out["fdir"] = "매도→매수 전환 중"
    elif f60 > 0 and (f20 < 0 or f5 < 0):
        out["fdir"] = "매수 후 차익실현"
    else:
        out["fdir"] = "혼조"
    return out

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
        ("integ",   INTEG_URL.format(code=code), False),
        ("fin_a",   FIN_A_URL.format(code=code), False),
        ("fin_q",   FIN_Q_URL.format(code=code), False),
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
    # ★일/주/월봉 — 다음에서 받는다. 네이버 차트는 차단(403)이라 idxRel·주봉·월봉이 통째로 죽는다.
    #   형식은 기존과 동일하게 [[date, o, h, l, c, v], ...] 오래된순으로 맞춘다.
    try:
        dr = requests.get(DAUM_DAYS_URL.format(code=code), headers=HDR_DAUM, timeout=15)
        rows = (dr.json() or {}).get("data") or []
        day = [[(x.get("date") or "")[:10], x.get("openingPrice"), x.get("highPrice"),
                x.get("lowPrice"), x.get("tradePrice"), x.get("accTradeVolume")]
               for x in reversed(rows) if x.get("tradePrice")]
        out["candles"]   = day
        out["candles_w"] = _resample(day, "W")
        out["candles_m"] = _resample(day, "M")
    except Exception as e:
        out["candles"] = None
        out["candles_err"] = str(e)

    # ★공시 — 다음 40건(날짜 포함). 네이버는 10건뿐이고 날짜가 비어 와서 원인 추적이 안 된다.
    try:
        dd = requests.get(DISC_URL.format(code=code), headers=HDR_DAUM, timeout=10)
        rows = (dd.json() or {}).get("data") or []
        out["disc"] = [{"title": r.get("title"),
                        "datetime": r.get("createdAt"),      # ★날짜 — 재료일 대조의 근거
                        "author": r.get("author"),
                        "id": r.get("disclosureId")} for r in rows]
    except Exception as e:
        out["disc"] = None
        out["disc_err"] = str(e)

    # 다음 재무(확정치) — 컨센 오염 없는 실적. 실패해도 무시(네이버 것으로 폴백)
    try:
        dr = requests.get(DAUM_FIN_URL.format(code=code), headers=HDR_DAUM, timeout=10)
        out["daum_fin"] = (dr.json() or {}).get("data") or {}
    except Exception as e:
        out["daum_fin"] = None
        out["daum_fin_err"] = str(e)
    # 토스 투자자별 상세
    try:
        tr = requests.get(TOSS_URL.format(code=code), headers=HDR_TOSS, timeout=10)
        out["toss"] = tr.json().get("result", {}).get("body", [])
    except Exception as e:
        out["toss"] = None
        out["toss_err"] = str(e)
    return out

def _parse_report_header(text):
    """리포트 .md 맨 앞 YAML 헤더(--- ... ---)를 dict로. 형식이 어긋나면 None."""
    if not text or not text.lstrip().startswith("---"):
        return None
    body = text.lstrip()[3:]
    end = body.find("\n---")
    if end < 0:
        return None
    head, rest = body[:end], body[end + 4:]
    out = {}
    for line in head.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        out[k.strip()] = v.strip()
    for k in ("price", "target"):
        try:
            out[k] = float(str(out.get(k, "")).replace(",", "")) if str(out.get(k, "")).strip() else None
        except Exception:
            out[k] = None
    out["_body"] = rest.strip()
    return out if out.get("code") else None


def load_prev_report(code):
    """reports/{code}/ 에서 가장 최근 .md 하나를 읽어 (헤더, 본문). 없으면 (None, None)."""
    d = os.path.join(REPORTS_DIR, str(code))
    if not os.path.isdir(d):
        return None, None
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    if not files:
        return None, None
    try:
        with open(os.path.join(d, files[-1]), "r", encoding="utf-8") as f:
            txt = f.read()
    except Exception:
        return None, None
    h = _parse_report_header(txt)
    if not h:
        return None, None
    body = h.pop("_body", "")
    return h, body


def track_progress(hdr, now_price):
    """직전 목표 대비 진척. price→target 구간에서 지금 몇 %를 갔는가.
    ★도달률은 방향을 보존한다 — 매도 예측(목표<현재)이면 하락이 진척이다."""
    if not hdr or now_price is None:
        return None
    p0, tgt = hdr.get("price"), hdr.get("target")
    if not p0 or not tgt:
        return None
    span = tgt - p0
    if span == 0:
        return None
    moved = now_price - p0
    return {
        "prevDate":   hdr.get("date"),
        "prevPrice":  p0,
        "prevTarget": tgt,
        "prevThesis": hdr.get("thesis"),
        "prevGrade":  hdr.get("grade"),
        "horizon":    hdr.get("horizon"),
        "now":        now_price,
        "reachedPct": round(moved / span * 100, 1),          # 목표까지 몇 % 갔나(음수=반대로 감)
        "gapPct":     round((tgt / now_price - 1) * 100, 1), # 지금부터 목표까지 남은 폭
        "expired":    bool(hdr.get("horizon") and
                           str(hdr["horizon"]) < now_kst().strftime("%Y-%m-%d")),
    }


def write_report_file(code, name, market, supply, raw, price_block,
                      week_block=None, month_block=None, supply_detail=None,
                      idx_rel=None):
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
        "updated": now_kst().strftime("%Y-%m-%d %H:%M"),
        "supply_10d": supply,                                  # 외인/연기금 최근10일 (토스)
        "supply_detail": supply_detail,                        # 투자자별 세부 + 외인보유율 추이
        "price_daily": price_block,                            # 일봉
        # ★지수 대비 — 변화점검·정밀분석이 "수급 파일 idxRel에 있다"고 지시하므로 여기 실어야 한다
        "idxRel": idx_rel,
        "price_weekly": week_block,                            # 주봉
        "price_monthly": month_block,                          # 월봉
        "valuation":          g("valuation",          lambda: integ.get("totalInfos")),
        "consensus":          g("consensus",          lambda: integ.get("consensusInfo")),
        "researches":         g("researches",         lambda: integ.get("researches")),
        "industry_peers":     g("industry_peers",     lambda: integ.get("industryCompareInfo")),
        "financials_annual":  g("financials_annual",  lambda: (raw.get("fin_a") or {}).get("financeInfo")),
        "financials_quarter": g("financials_quarter", lambda: (raw.get("fin_q") or {}).get("financeInfo")),
        # ★확정 재무(다음) — 네이버는 최신 분기를 컨센(isConsensus=Y)으로 채우므로 실적 판단은 이걸 기준으로.
        #   미공시 분기는 아예 없다(= 아직 확정 안 됨). 공시에 「영업(잠정)실적」이 있는데 여기 없으면 DART 원문 확인.
        "financials_confirmed": g("financials_confirmed", lambda: _daum_fin(raw.get("daum_fin"))),
        "disclosures":        raw.get("disc"),                 # 공시 (원본)
        # ── v3 파생 필드 ─────────────────────────────────────────────
        "earnings_alert":     g("earnings_alert", lambda: detect_earnings_alert(
                                  raw.get("disc"), (raw.get("fin_q") or {}).get("financeInfo"))),
        "supply_derived":     g("supply_derived", lambda: enhance_supply(supply_detail)),
        "valuation_derived":  g("valuation_derived", lambda: compute_derived_valuation(
                                  integ.get("totalInfos"), (raw.get("fin_a") or {}).get("financeInfo"))),
        "volume_stats":       g("volume_stats", lambda: compute_volume_stats(
                                  parse_candles(raw["candles"])) if raw.get("candles") else None),
        "news":               g("news",               lambda: parse_news(raw["news"]) if raw.get("news") else None),
        "_errors": errs,
    }

    # [추적] 직전 리포트를 실어 다음 회차가 백지에서 시작하지 않게 한다.
    if str(code) in TRACK_CODES:
        try:
            hdr, body = load_prev_report(code)
            if hdr:
                payload["prev_report"] = {
                    "header": {k: v for k, v in hdr.items()},
                    "body": body,
                }
                payload["prev_track"] = track_progress(
                    hdr, (price_block or {}).get("now"))
        except Exception as e:
            errs["prev_report"] = f"{type(e).__name__}: {e}"

    os.makedirs(REPORT_DIR, exist_ok=True)
    final = os.path.join(REPORT_DIR, f"{code}.json")
    tmp = final + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, default=str)   # default=str: 직렬화 불가 값도 안전
    os.replace(tmp, final)   # 원자적 교체(부분쓰기 방지)

def enrich_sector(sector_key, sector_name, members):
    """섹터 구성종목 여러 개의 수급·상세를 긁어 전용파일 1개(report-data/{sector_key}.json)로 합친다.
    반환: 화면 카드용 요약(구성종목 수급 합산 + 통과 여부)."""
    import json as _json
    comps = []
    agg_f_cum = agg_p_cum = 0            # 외국인·연기금 누적 합산(섹터 총괄 표기용)
    for m in members:
        c, nm = m["code"], m["name"]
        one = {"code": c, "name": nm}
        try:
            sup = port_supply(c)          # 최근 WINDOW일 누적·매수일·통과여부
            one["supply"] = sup
            if sup:
                agg_f_cum += sup.get("foreign_net_10d", 0)
                agg_p_cum += sup.get("pension_net_10d", 0)
        except Exception as e:
            print(f"  섹터 구성종목 수급 실패 {c}: {e}", flush=True)
            one["supply"] = None
        # 상세(일별 수급·가격) 수집 — 총괄 분석용 원본
        try:
            raw = fetch_all(c)
            detail = {}
            if raw.get("candles"):
                try:
                    _c2 = parse_candles(raw["candles"])
                    detail["price"] = compute_price_block(_c2)
                    detail["idxRel"] = compute_index_relative(_c2, member.get("market") if isinstance(member, dict) else "KOSPI")
                except Exception:
                    pass
            if raw.get("toss"):
                try:
                    detail["supply_detail"] = build_supply_detail(raw["toss"])
                except Exception:
                    pass
            one["detail"] = detail
        except Exception as e:
            print(f"  섹터 구성종목 상세 실패 {c}: {e}", flush=True)
            one["detail"] = {}
        comps.append(one)
        time.sleep(0.3)                   # 토스 API 부담 완화

    payload = {
        "sector_key": sector_key,
        "sector_name": sector_name,
        "generated_at": now_kst().strftime("%Y-%m-%d %H:%M"),
        "members": comps,
        "aggregate": {"foreign_net_sum": int(agg_f_cum), "pension_net_sum": int(agg_p_cum)},
    }
    try:
        os.makedirs(REPORT_DIR, exist_ok=True)
        with open(os.path.join(REPORT_DIR, f"{sector_key}.json"), "w", encoding="utf-8") as f:
            _json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
    except Exception as e:
        print(f"  섹터 파일 저장 실패 {sector_key}: {e}", flush=True)
    # 화면 카드용: 섹터 합산 수급을 supply 형태로 반환(통과여부는 합산 기준)
    return {
        "foreign_net_10d": int(agg_f_cum), "foreign_buydays": "",
        "foreign_pass": agg_f_cum > 0,
        "pension_net_10d": int(agg_p_cum), "pension_buydays": "",
        "pension_pass": agg_p_cum > 0,
        "sector_members": [{"code": c["code"], "name": c["name"]} for c in comps],
    }


def enrich_stock(code, name="", market="", supply=None):
    """원본 풀데이터(일/주/월봉 + 투자자별 세부 포함)를 종목별 파일로 저장, 화면용 요약 반환."""
    raw = fetch_all(code)
    # 일봉 가격블록
    price_block = None
    idx_rel = None
    if raw.get("candles"):
        try:
            _c = parse_candles(raw["candles"])
            price_block = compute_price_block(_c)
            idx_rel = compute_index_relative(_c, market)      # ★지수 대비 위치
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
                          week_block, month_block, supply_detail, idx_rel)
    except Exception as e:
        print(f"  report 파일 저장 실패 {code}: {e}", flush=True)
    # 화면용 요약(기존과 동일)
    out = {}
    now_price = None
    if price_block:
        out.update(price_block); now_price = price_block["now"]
    if idx_rel:
        out["idxRel"] = idx_rel                                # ★지수 대비 위치
    _cum = supply_cum_summary(supply_detail)                   # [C-A49b] 구간별 수급 요약
    if _cum:
        out["supplyCum"] = _cum
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

def fetch_us_quote(ticker):
    """[추적] 미국 개별 종가. 수급은 원천이 없지만 ★가격은 받을 수 있다.
    목표 도달률을 대조하려면 종가가 매일 쌓여야 하므로 여기서만 받는다.
    나스닥 API 1순위, 실패 시 야후(지수·특수티커 대비)."""
    t = str(ticker or "").strip()
    if not t or t.startswith("^"):
        return None                     # 지수는 추적 대상이 아니다
    try:
        r = requests.get(
            f"https://api.nasdaq.com/api/quote/{t}/info?assetclass=stocks",
            headers={"User-Agent": UA}, timeout=10)
        d = (r.json() or {}).get("data") or {}
        p = ((d.get("primaryData") or {}).get("lastSalePrice") or "").replace("$", "").replace(",", "")
        chg = ((d.get("primaryData") or {}).get("percentageChange") or "").replace("%", "")
        if p:
            return {"now": float(p),
                    "changePct": (float(chg) if chg not in ("", None) else None),
                    "src": "nasdaq"}
    except Exception:
        pass
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=5d&interval=1d",
            headers={"User-Agent": UA}, timeout=10)
        res = (((r.json() or {}).get("chart") or {}).get("result") or [None])[0] or {}
        cl = [x for x in ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close", []) if x]
        if cl:
            return {"now": float(cl[-1]), "changePct": None, "src": "yahoo"}
    except Exception:
        pass
    return None


def latest_trade_date():
    """기준 종목(삼성전자) 토스 최신 baseDate = 최신 거래일. 실패 시 None."""
    try:
        r = requests.get(TOSS_URL.format(code="005930"), headers=HDR_TOSS, timeout=10)
        body = r.json().get("result", {}).get("body", [])
        return body[0].get("baseDate") if body else None
    except Exception:
        return None


def main():
    print("스캔 시작:", now_kst().strftime("%Y-%m-%d %H:%M"), flush=True)

    # [장 안 열린 날 가드] 예약(cron)이 주말·공휴일에 헛돌지 않게 막는다.
    #   ★장중 시간대(09:30~15:30)는 같은 날 반복 실행을 허용한다 — 가격·수급이 계속 변하므로.
    #     (하루 1회 예약이던 시절엔 '같은 날이면 스킵'이었으나, 장중 모니터링용 다회 예약으로 바뀌어 조건을 분리했다.)
    #   수동 실행(workflow_dispatch)·로컬 실행은 언제나 갱신.
    new_date = latest_trade_date()
    if new_date and os.environ.get("RUN_TRIGGER") == "schedule":
        today_kst = now_kst().strftime("%Y-%m-%d")
        if new_date != today_kst:
            # 소스의 최신 거래일이 오늘이 아니다 = 오늘은 장이 안 열렸다(주말·공휴일).
            #   단, 장 시작 직후엔 소스가 아직 전일자를 줄 수 있으므로 09:00~09:40 사이는 통과시킨다.
            hm = now_kst().strftime("%H:%M")
            if not ("09:00" <= hm <= "09:40"):
                print(f"[스킵] 오늘({today_kst})은 거래일이 아님 (소스 최신 {new_date}). 예약 실행 생략.", flush=True)
                return

    # [#3 우선 처리] 내 종목(포트폴리오)을 유니버스 스캔보다 '먼저' 돌린다.
    #   → 뒤의 대량 스캔이 느려지거나 중간에 멈춰도(취소/스로틀) 보유종목 리포트는 항상 최신.
    #   → 일시적 실패엔 1회 재시도해서 보유종목이 누락되지 않게.
    print(f"포트폴리오 {len(PORTFOLIO)}개 처리(우선)…", flush=True)
    portfolio = []
    for it in PORTFOLIO:
        code, name, ptype = it["code"], it["name"], it["ptype"]
        ref = it.get("ref")   # 재사용 대상(기초자산) 코드. 없으면 자기가 본체.
        entry = {"code": code, "name": name, "market": it.get("market", ""), "ptype": ptype}
        if "basis" in it:
            entry["basis"] = it["basis"]
        if ref:
            entry["ref"] = ref   # 프론트가 리포트 데이터를 ref 종목에서 읽도록
            # 본주 이름도 담아 프롬프트 제목을 본주로 통일 (삼전레버→'삼성전자')
            ref_name = next((x["name"] for x in PORTFOLIO if x["code"] == ref), "")
            if ref_name:
                entry["ref_name"] = ref_name
        ok = False
        for attempt in (1, 2):
            try:
                if ptype == "us":
                    # 미국 종목: 수급 원천 없음 → 스캐너 미수집. 카드 메타만 담고 리포트는 웹 검색 기반.
                    entry["us_target"] = it.get("us_target", "")
                    entry["us_ticker"] = it.get("us_ticker", "")
                    entry["us_kind"] = it.get("us_kind", "stock")   # index면 지수처럼 분석
                    entry["supply"] = None
                    # [추적] 기초자산 종가만 받아 카드·도달률에 쓴다(수급은 여전히 없음).
                    tk = it.get("us_ticker", "")
                    if str(tk) in TRACK_CODES:
                        q = fetch_us_quote(tk)
                        if q:
                            entry["enrich"] = dict(q)
                            try:
                                hdr, body = load_prev_report(tk)
                                if hdr:
                                    entry["enrich"]["prevTrack"] = track_progress(hdr, q["now"])
                            except Exception:
                                pass
                    ok = True
                    break
                if ptype == "sector":
                    # 섹터 ETN → 구성종목 여러 개 수급을 묶어 전용파일 1개로. 카드엔 합산 수급.
                    entry["sector_key"] = it["sector_key"]
                    entry["supply"] = enrich_sector(it["sector_key"], it.get("basis", name), it["members"])
                elif ref:
                    # 파생 → 기초자산 재사용: 수급은 ref 종목 기준. 리포트 파일은 ref가 본체로서 생성함(중복 스크랩 안 함).
                    entry["supply"] = port_supply(ref)
                else:
                    # 본체(개별주·지수): 직접 수급 + enrich(원본 리포트 파일 생성)
                    sup = port_supply(code)
                    entry["supply"] = sup
                    entry["enrich"] = enrich_stock(code, name, it.get("market", ""), sup or {})
                ok = True
                break
            except Exception as e:
                print(f"  포트 처리 실패 {code} (시도 {attempt}/2): {e}", flush=True)
                time.sleep(1.0)
        if not ok and not ref and ptype != "sector":
            entry["enrich"] = None
        portfolio.append(entry)

    # [유니버스 스캔] 외국인·연기금 수급 필터
    kept  = [(c, n, "KOSPI")  for c, n in get_list(0, KOSPI_N)]
    kept += [(c, n, "KOSDAQ") for c, n in get_list(1, KOSDAQ_N)]
    print("스캔 대상(보통주):", len(kept), flush=True)

    # [병렬 스캔] 종목별 수급 조회는 서로 독립이라 병렬 안전.
    #   실측: 200종목 12스레드 7.4초·실패 0 (순차 대비 5.6배). 800종목 ≈ 30초.
    #   일시 실패는 1회 재시도 → 누락 방지.
    def scan_one(item):
        code, name, mkt = item
        for attempt in (1, 2):
            tr = get_trend(code)
            if tr:
                return (code, name, mkt, tr)
            if attempt == 1:
                time.sleep(0.5)
        return (code, name, mkt, None)

    foreign_pass, pension_pass = [], []
    done = 0
    failed = []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as ex:
        for code, name, mkt, tr in ex.map(scan_one, kept):
            done += 1
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
            else:
                failed.append(code)
            if done % 100 == 0:
                print(f"{done}/{len(kept)}", flush=True)
    if failed:
        print(f"  [경고] 수급 조회 실패 {len(failed)}종목: {failed[:10]}{'...' if len(failed)>10 else ''}", flush=True)

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
    # [병렬 enrich] 종목별 독립 + 파일쓰기 원자적(tmp→replace)이라 병렬 안전.
    #   종목당 API 6개를 부르므로 워커를 낮게(4) 잡아 소스 부하를 억제한다.
    print(f"동시 {len(both)}개 분석 데이터 수집…", flush=True)
    def enrich_one(b):
        try:
            return b, enrich_stock(
                b["code"], b["name"], b["market"],
                {"foreign_net_10d": b["f_net"], "foreign_buydays": b["f_buydays"],
                 "pension_net_10d": b["p_net"], "pension_buydays": b["p_buydays"]}), None
        except Exception as e:
            return b, None, e
    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as ex:
        for b, res, err in ex.map(enrich_one, both):
            b["enrich"] = res
            if err:
                print(f"  enrich 실패 {b['code']}: {err}", flush=True)

    # [#2 정리] report-data 는 '현재 0순위(both) + 보유종목'만 유지.
    #   → 0순위에서 탈락한 종목의 옛 파일은 삭제(스테일 스냅샷 제거). .tmp 잔해도 제거.
    # [기술적 필터] 저가매수형·모멘텀형 — 수급과 별개로 동작. 수급은 '라벨'로만 붙인다.
    #   0순위 통과 여부를 supply_map으로 넘겨 라벨에 반영.
    screen = {"value_pick": [], "momentum": [], "stats": {}}
    if screener:
        try:
            print("기술적 필터(저가매수형·모멘텀형) 실행…", flush=True)
            supply_map = {}
            for x in foreign_pass:
                supply_map.setdefault(x["code"], {}).update(
                    {"f_net": x["net"], "f_buydays": x["buydays"]})
            for x in pension_pass:
                supply_map.setdefault(x["code"], {}).update(
                    {"p_net": x["net"], "p_buydays": x["buydays"]})
            for c in supply_map:
                supply_map[c]["is_zero_rank"] = c in both_codes
            screen = screener.run_screeners(supply_map)
        except Exception as e:
            print(f"  [경고] 기술적 필터 실패: {e}", flush=True)

    keep = {b["code"] for b in both}
    for it in PORTFOLIO:
        if it["ptype"] == "us":
            continue                              # 미국: 데이터 파일 없음 → keep 불필요
        if it["ptype"] == "sector":
            keep.add(it["sector_key"])           # 섹터 전용파일 보호
        else:
            # 파생(ref 있음)은 기초자산 파일을 유지, 본체는 자기 파일을 유지
            keep.add(it.get("ref") or it["code"])
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
        "updated": now_kst().strftime("%Y-%m-%d %H:%M"),
        "market_date": new_date,
        "window": WINDOW, "buy_ratio": int(BUY_RATIO_MIN * 100),
        "scanned": len(kept),
        "foreign": foreign_pass,
        "pension": pension_pass,
        "both": both,
        "portfolio": portfolio,
        # 기술적 필터 — 수급과 독립. 수급은 각 항목의 labels.supply에 라벨로만 들어간다.
        "value_pick": screen.get("value_pick", []),
        "momentum": screen.get("momentum", []),
        "screen_stats": screen.get("stats", {}),
    }
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"완료. 외국인 {len(foreign_pass)} / 연기금 {len(pension_pass)} / 동시 {len(both)} / 포트 {len(portfolio)}"
          f" / 저가매수 {len(screen.get('value_pick', []))} / 모멘텀 {len(screen.get('momentum', []))} → data.json", flush=True)

if __name__ == "__main__":
    main()
