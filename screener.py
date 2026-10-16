# -*- coding: utf-8 -*-
"""
kfilter 확장 — 기술적 스크리닝 필터 (저가매수형 / 모멘텀형)

[설계 원칙]
· 수급은 필터가 아니라 라벨. 필터로 쓰면 "수급 없이 기술만 좋은 종목"을 못 본다.
· 【저가매수형 v2 — 2026-10-17】 컨셉 = "돈 잘 버는데 주가가 너무 낮은 종목".
    ① 돈 번다   : 최근 4분기 합 영업흑자 (적자 분기가 있어도 1년이 흑자면 통과 — 계절·일회성·R&D 비용 분기로 빼지 않는다)
    ② 잘 번다   : 1년 영업이익률 ≥ 업종 중앙값
    ③ 실적 유지 : 최근 반기 매출이 직전 반기 대비 -10% 이상 (분기 비교는 계절·수주 시점에 흔들린다)
    ④ 눌림      : 자기 52주 고점(종가) 대비 -20% 이하 — ★지수 대비가 아니다
    ⑤ 순위      : 기업가치(시가총액 + 차입금 - 현금) ÷ 최근 4분기 영업이익이 업종 중앙값보다 낮은 순 → 상위 20
    ★ 제외: 우선주·금융·지주사, 액면변동(주식수 ±50%)·장기 거래정지·권리락 — 없으면 배수·낙폭이 가짜가 된다
  · v1(초과낙폭 순)을 버린 이유(실측 2026-10-16): 삼전+하이닉스가 코스피 시총의 47%라 '지수 대비'는 자기 자신과
    비교하는 꼴이었고(삼전 초과낙폭 -2.7%p, 자격 154종목 중 154위), 고점이 1년 전인 소외주가 상위를 채워
    20종목 중 9개가 연율 PER 20배 이상이었다. 순위가 '싼 순'이 아니라 '오래 소외된 순'이었다.
  · 순이익(PER)이 아니라 영업이익으로 본다. 순이익엔 일회성 이익·지분법 이익이 섞인다(KCC PER 0.6배).
  · 시가총액이 아니라 기업가치로 본다. 빚이 큰 회사(한전·가스공사)가 싸 보이는 착시를 막는다.
  · ★다음 일봉은 수정주가가 아니다. 액면분할 종목은 분할 전 고점과 분할 후 현재가를 비교해
    -80%대 가짜 낙폭이 만들어진다(실측: 동국홀딩스·인화정공·INVENI). listedSharesCount로 잡는다.
· 【모멘텀형】 현행 유지. 시장 국면에 따라 개수가 고무줄인 게 정상이다. 0개면 0개.
· 필터는 판단하지 않는다. 매수 우선순위는 일괄 분석 리포트에서 정한다.

[호출 최적화 — 실측 기준]
· 유니버스: sectors 2회(4.6초). ★page 파라미터 무의미 — 1페이지에 전체가 온다.
· 시세    : quotesv4 벌크. ★한계 700개(800은 503). 2735종목 → 7회 3.0초.
· 펀더멘털: 분기 캐시(494KB). 매일 호출 0회. PER은 당일가÷캐시EPS로 계산.
· 일봉    : 시총 상위 → 흑자 게이트 통과분만. ★순서를 바꾸지 마라
            (흑자를 먼저 걸러 800을 채우면 소형주가 들어와 시총 컷이 낮아진다).
"""

import os, re, json, time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from statistics import median

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

# 【저가매수형】 자격
VP_SALES_MIN = -10.0   # 매출 증감 하한(%). 직전 분기 대비
VP_OPM_MIN   = -10.0   # 영업이익률 증감 하한(%). 직전 분기 대비 상대변화
VP_SHARE_LO  = 0.67    # 상장주식수 변동 허용 하한(배). 벗어나면 액면분할·병합·대규모 증자
VP_SHARE_HI  = 1.50    # 상장주식수 변동 허용 상한(배)
VP_HALT_MAX  = 5       # 거래정지 허용일(52주 내). 초과하면 가격이 멈춰 낙폭 계산이 무의미
# ★권리락 탐지 — 무상·유상증자 권리락은 주가만 기계적으로 떨어지고 상장주식수는 그대로다.
#   신주가 나중에 상장되므로 listedSharesCount 비교로는 절대 못 잡는다.
#   실측: 저가매수 20종목 중 3개(티앤엘·RF머트리얼즈·HLB제약)가 이 경로로 가짜 낙폭 1위권에 올랐다.
VP_GAP_DROP  = -25.0   # 하루 낙폭이 이보다 크면 권리락 의심(정상 급락도 포함되므로 공시로 확정)
VP_GAP_DAYS  = 60      # 의심 구간(최근 N거래일)
VP_TOP_N     = 20      # ★상위 N개 컷(초과낙폭 큰 순)
FIN_CACHE    = os.path.join(CACHE_DIR, "financials.json")   # 분기 재무 캐시(분기 갱신)
# 【저가매수형 v2】
VP_DD_MAX      = -20.0   # 자기 52주 고점(종가) 대비 이보다 더 내려와야 '눌림'
VP_SALES_H_MIN = -10.0   # 최근 반기 매출이 직전 반기 대비 이보다 덜 줄어야 '실적 유지'
VP_EXCL_SECTORS = {"증권", "은행", "손해보험", "생명보험", "기타금융", "창업투자", "카드"}   # 영업이익·부채 구조가 달라 배수 비교가 무의미
VP_HOLDCO_NAMES = {"SK", "LG", "GS", "LS", "DL", "CJ", "한화", "두산", "효성", "코오롱", "HD현대",
                   "SK스퀘어", "한진칼", "한국앤컴퍼니", "SK디스커버리"}   # 회사 개요로 못 잡을 때의 이름 목록
VAL_CACHE    = os.path.join(CACHE_DIR, "valuation.json")    # 차입금·현금(야후)·지주사 여부(다음 회사개요) — 분기성 값
VP_FINLIKE_NAMES = {"카카오페이", "헥토파이낸셜", "KG이니시스", "NHN KCP", "다날", "나이스정보통신", "한국정보통신",
                    "KG모빌리언스", "갤럭시아머니트리", "세틀뱅크"}   # 회사 개요로 못 잡을 때의 결제·핀테크 이름 목록
VAL_SCHEMA   = 2         # 기업가치 캐시 구조 — 바뀌면 전부 다시 받는다
# 결제·핀테크 판별 — 현금에 고객 예치금(선불충전금·정산대금)이 섞여 순현금이 부풀고 기업가치가 0 아래로 내려간다(실측: 카카오페이)
_FINLIKE_RE = re.compile(r"전자지급결제|지급결제|결제대행|전자금융|간편결제|선불|종합결제|결제\s*플랫폼|핀테크|PG\s*\(|PG사|PG\s*사업|PG업|부가통신업|금융\s*VAN|VAN사|VAN\s*사업")
# 지주사 판별 — 회사 개요가 '자기 자신이 지주회사'라고 말할 때만. ("지주회사인 OO의 자회사"는 걸리지 않게)
_HOLDCO_RE = re.compile(r"(지주회사|지주사)\s*(로\s*)?전환|지주회사(임|이다|로서|로,|로 |\s*체제)|순수\s*지주|된 지주회사|그룹 지주회사")

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


# [C6] 실적시즌 캐시 갱신 주기(일).
#   종전 조건은 "그 달에 한 번 받으면 그 달엔 끝"(upd.month != now.month)이었다.
#   그런데 분기 실적은 ★한 달에 걸쳐 흩어져 나온다(7/23 IPARK, 7/30 삼성전기, 8/7 씨에스윈드, 8/12 동원산업).
#   8월 1일에 캐시를 채우면 그 뒤에 나온 실적을 9월까지 못 잡아,
#   구 분기 데이터로 필터를 통과하는 종목이 생긴다(실측). → 시즌 중엔 주 1회로 좁힌다.
EARNINGS_REFRESH_DAYS = 7


def _cache_stale(upd, days=None):
    """캐시 갱신 필요 여부. 평시 100일, 실적시즌(2·5·8·11월)엔 days(기본 7일).
    ★두 캐시(펀더멘털·분기재무)가 ★같은 실행에서 동시에 갱신되면 800x2=1,600 호출이 된다.
      주기를 어긋나게 줘서 한 실행에 하나만 갱신되도록 한다."""
    age = datetime.now() - upd
    if age > timedelta(days=100):
        return True
    return _is_earnings_season() and age > timedelta(days=(days or EARNINGS_REFRESH_DAYS))


def fetch_fundamentals(symbol_codes, force=False):
    """quotes에서 ★분기성 필드만 캐시. 매일은 캐시에서 읽어 호출 0회.
    ★주가에 비례하는 값(시총·PER·PBR·52주고저)은 캐시 금지 — 매일 시세로 재계산한다.
       상장주식수는 분할·증자 때만 바뀌므로 캐시 대상이고, 시총은 이것 × 당일가로 만든다.
    캐시: {symbolCode: {op, ni, eps, bps, shares, sectorPer, dps, name, market, sector}}"""
    cache = _load(FUND_CACHE)
    if cache and not force:
        try:
            upd = datetime.fromisoformat(cache["updated"])
            # 실적 시즌이고 이번 달에 아직 안 받았으면 갱신
            need = _cache_stale(upd, 7)       # [C6] 펀더멘털: 실적시즌 7일 주기
            # ★스키마 변경 감지 — shares/bps 없는 구버전 캐시면 무조건 다시 받는다.
            #   (이걸 빼면 시총 계산이 통째로 비어 스크리너 결과가 0이 된다)
            _d = cache.get("data") or {}
            if _d:
                _s = next(iter(_d.values())) or {}
                if "shares" not in _s or "bps" not in _s:
                    need = True
                    print("  [캐시] 스키마 변경 감지 → 강제 갱신", flush=True)
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
                "eps": d.get("eps"), "bps": d.get("bps"),
                "shares": d.get("listedShareCount"),   # ★분할·증자 때만 변함 → 캐시 가능
                "sectorPer": d.get("sectorPer"), "dps": d.get("dps"),
                "name": d.get("name"), "market": d.get("market"),
                "sector": d.get("wicsSectorName"),
            }
            # ★제거: mcap / per / pbr / high52w / low52w — 주가에 비례해 매일 바뀐다.
            #   캐시하면 급등 종목에서 시총이 25%까지 어긋난다(실측: 한국콜마 -25.3%).
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
    파두는 30건이었다. 제거 후 MIN_ROWS 미만이면 None.
    ★반환: (rows, halt_days) — halt_days는 거래정지 판정에 쓴다."""
    try:
        d = _get(f"{DAUM}/quote/{sc}/days?perPage={DAYS_N}&page=1", timeout=15)
        raw = d.get("data") or []
        rows = [r for r in raw if (r.get("accTradeVolume") or 0) > 0]
        halt_days = len(raw) - len(rows)          # ★거래정지·장전 더미 일수
        return (rows, halt_days) if len(rows) >= MIN_ROWS else (None, 0)
    except Exception:
        return None, 0


def detect_gap_drop(rows, n_days=None, thr=None):
    """★하루 -25% 이상 급락 탐지 → 권리락 의심 신호.
    반환: (있으면 {"date","before","after","pct"}, 없으면 None)
    정상 급락(악재)도 걸리므로 확정은 공시로 한다."""
    n_days = n_days or VP_GAP_DAYS
    thr = thr if thr is not None else VP_GAP_DROP
    for i in range(min(n_days, len(rows) - 1)):
        a = rows[i].get("tradePrice")
        b = rows[i + 1].get("tradePrice")
        if not a or not b or b <= 0:
            continue
        pct = (a / b - 1) * 100
        if pct <= thr:
            return {"date": (rows[i].get("date") or "")[:10], "before": b,
                    "after": a, "pct": pct}
    return None


def is_rights_offering(sc):
    """★공시로 권리락 확정. 급락이 감지된 종목에만 부른다(전 종목 호출 금지).
    무상증자·유상증자·액면분할·주식배당 전부 주가를 기계적으로 떨어뜨린다."""
    try:
        d = _get(f"{DAUM}/disclosures?symbolCode={sc}&perPage=20&page=1", timeout=12)
        kws = ("권리락", "무상증자", "액면분할", "주식분할", "주식배당", "감자")
        for r in (d.get("data") or []):
            t = str(r.get("title") or "")
            if any(k in t for k in kws):
                return t[:60]
    except Exception:
        pass
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

    # ★상장주식수 변동 — 액면분할·병합·대규모 증자 탐지용
    ls = [r.get("listedSharesCount") for r in rows if r.get("listedSharesCount")]
    share_ratio = (ls[0] / ls[-1]) if (len(ls) >= 2 and ls[-1]) else None

    v20 = sum(vo[:20]) / 20 if len(vo) >= 20 else 0
    v60 = sum(vo[:60]) / 60 if len(vo) >= 60 else 0

    return {
        "cur": cur, "ma5": ma5, "ma20": ma20, "ma60": ma60,
        "daily_aligned": ma5 > ma20 > ma60,
        "ma5_above": cur > ma5,
        "peak_close": peak_close, "peak_date": peak_date, "peak_days": peak_days,
        "share_ratio": share_ratio,
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
_IDX_MEMO = {}

def fetch_index_days():
    """코스피·코스닥 일봉 → {market: {date: close}}.
    ★프로세스당 1회만 받는다. scanner도 이 결과를 재사용해 중복 호출을 없앤다.
    ★저가매수형 ④축(지수 대비 초과낙폭) 계산용. 절대 낙폭만 쓰면 하락장에서 전부 통과한다."""
    if _IDX_MEMO:
        return _IDX_MEMO
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
    _IDX_MEMO.update(out)
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
def _latest_quarter(data):
    """캐시 전체에서 가장 앞선 분기(YYYY-MM). 이보다 뒤처진 종목이 갱신 대상이다."""
    ds = [(v[0].get("date") or "")[:7] for v in data.values() if v]
    return max(ds) if ds else ""


def fetch_financials(symbol_codes, force=False):
    """QUARTER 4건을 캐시. ★매일 부르면 613회다 — 분기 캐시로 흡수해 호출 0회.
    캐시: {symbolCode: [{date, eps, op, sales, roe}, ...4건]}
    ROE는 분기 단독값(소수). 연환산은 최근 2분기 합 x 2로 계산한다."""
    cache = _load(FIN_CACHE)
    if cache and not force:
        try:
            upd = datetime.fromisoformat(cache["updated"])
            need = _cache_stale(upd, 9)       # [C6] 분기재무: 9일 주기 — 펀더멘털(7일)과 어긋내 동시 갱신을 피한다
            # ★★실적 시즌 보정 — 분기 확정치는 종목마다 공시 시점이 다르다.
            #   캐시를 만든 날 아직 안 올라온 종목은 ★직전 분기가 최신으로 남고, 9일간 그대로 쓰인다.
            #   실측 2026-09-09 — 798종목 중 160개(20%)가 2026-06 대신 2026-03을 최신으로 갖고 있었고,
            #   솔브레인홀딩스는 영업이익률이 "0.8%"(1Q)로 나가 실제 2Q 15.3%와 ★방향이 반대였다.
            #   → 캐시 전체가 아니라 ★뒤처진 종목만 다시 받는다. 호출은 그만큼만 는다.
            _lag = [k for k, v in (cache.get("data") or {}).items()
                    if v and (v[0].get("date") or "")[:7] < _latest_quarter(cache["data"])]
            if _lag and not need:
                print(f"  [캐시] 분기재무 뒤처진 {len(_lag)}종목만 갱신", flush=True)
                _fresh = {}
                def _one(sc):
                    try:
                        q = (_get(f"{DAUM}/quote/{sc}/financials", timeout=12).get("data") or {}).get("QUARTER") or []
                        return sc, [{"date": r.get("date"), "eps": r.get("eps"),
                                     "op": r.get("operatingProfit"), "sales": r.get("sales"),
                                     "roe": r.get("roe")} for r in q[:4]]
                    except Exception:
                        return sc, None
                with ThreadPoolExecutor(max_workers=WORKERS) as ex:
                    for sc, v in ex.map(_one, _lag):
                        if v: _fresh[sc] = v
                if _fresh:
                    cache["data"].update(_fresh)
                    _save(FIN_CACHE, {"updated": cache.get("updated"), "data": cache["data"]})
                return cache["data"]
            # ★스키마 변경 감지 — 이 캐시의 값은 ★리스트다([{date,eps,op,sales,roe} x4]).
            #   [버그수정] 종전엔 fundamentals용 검사("shares" not in _s)가 그대로 복사돼 있었는데,
            #   리스트에 그 검사를 하면 ★항상 True가 되어 캐시가 무력화됐다(매 실행 800종목 재수집).
            #   리스트이고 첫 원소에 date/eps가 있으면 정상 스키마로 본다.
            _d = cache.get("data") or {}
            if _d:
                _s = next(iter(_d.values()))
                bad = not isinstance(_s, list) or (_s and not isinstance(_s[0], dict)) \
                      or (_s and "date" not in _s[0])
                if bad:
                    need = True
                    print("  [캐시] 분기재무 스키마 변경 감지 → 강제 갱신", flush=True)
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
    """분기 재무 → 저가매수형 원재료.

    ★핵심은 매출·영업이익률의 직전 분기 대비 변화다. EPS는 영업외·세금·주식수가 섞여
      본업의 방향을 흐린다. "돈을 비슷하게 벌거나 더 잘 버는가"는 매출과 마진으로 본다.
    ·영업이익률(OPM) = 영업이익 ÷ 매출 x 100
    ·직전 OPM이 0 이하면 비율 변화가 무의미하므로 절대 개선(m0 > m1)으로 판정한다.
    ·연환산 EPS = 최근 반기 x 2 — 이익수익률·연율PER 표시용(필터 조건 아님)
    """
    if not q or len(q) < 2:
        return None
    s0, s1 = (q[0].get("sales") or 0), (q[1].get("sales") or 0)
    o0, o1 = (q[0].get("op") or 0), (q[1].get("op") or 0)
    if s0 <= 0 or s1 <= 0:
        return None

    m0, m1 = o0 / s0 * 100, o1 / s1 * 100
    sales_g = (s0 / s1 - 1) * 100
    if m1 > 0:
        opm_g = (m0 / m1 - 1) * 100
        opm_ok = opm_g >= VP_OPM_MIN
    else:
        opm_g = None                      # 직전 분기 적자 — 비율 변화 산출 불가
        opm_ok = m0 > m1                  # 절대 개선이면 통과

    e = [(r.get("eps") or 0) for r in q[:4]]
    ro = [(r.get("roe") or 0) for r in q[:4]]
    hn = (e[0] + e[1]) if len(e) >= 2 else 0
    ho = (e[2] + e[3]) if len(e) >= 4 else 0

    # ★흑자전환 — 직전 분기 적자면 증가율(e0/e1)이 무의미하다. "—" 대신 명시해야 리포트가 읽는다.
    eps_turn = (len(e) >= 2 and (e[1] or 0) <= 0 and (e[0] or 0) > 0)
    eps_still_loss = (len(e) >= 2 and (e[1] or 0) <= 0 and (e[0] or 0) <= 0)

    return {
        "sales_growth": sales_g,
        "eps_turnaround_q": eps_turn,          # 적자 → 흑자
        "eps_still_loss": eps_still_loss,      # 적자 지속
        "opm_recent": m0, "opm_prev": m1, "opm_growth": opm_g,
        "profit_ok": (sales_g >= VP_SALES_MIN) and opm_ok,     # ②비슷하거나 개선
        "op_2q_pos": o0 > 0 and o1 > 0,
        "eps_ann": hn * 2,
        # ★"돈 버는가"의 최소 정의 — 연환산 EPS가 음수면 이익수익률·연율PER이 산출조차 안 된다.
        #   영업이익 흑자여도 직전 분기 대규모 적자가 반기 합을 음수로 만드는 경우가 있다(실측 2/20종목).
        "eps_ann_pos": (hn * 2) > 0,
        "roe_ann": ((ro[0] + ro[1]) * 2 * 100) if len(ro) >= 2 else None,
        "eps_growth": ((hn / ho - 1) * 100) if ho > 0 else None,
        "eps_turnaround": (ho <= 0 and hn > 0),
        "eps_q_recent": e[0] if e else None,
        "eps_q_prev": e[1] if len(e) > 1 else None,
    }


# ───────────────────── 6-b. 기업가치 자료 (저가매수형 v2) ─────────────────────
def _yahoo_session():
    """야후 quoteSummary는 쿠키+crumb가 있어야 열린다."""
    s = requests.Session()
    s.headers.update({"User-Agent": HDR["User-Agent"]})
    s.get("https://fc.yahoo.com", timeout=15)
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text.strip()
    if not crumb or len(crumb) > 40 or "<" in crumb:
        raise RuntimeError("crumb 받기 실패")
    return s, crumb


def fetch_valuation(meta, force=False):
    """{symbolCode: market} → {symbolCode: {debt, cash, holdco}}.
    ★다음 금융엔 차입금·현금이 없다(부채비율뿐 — 영업부채가 섞여 기업가치에 못 쓴다). 야후 financialData에서 받는다.
    ★지주사 여부는 다음 회사 개요 문장으로 가린다(이름만으로는 대덕·대웅·HDC 같은 지주사를 못 잡는다. 실측).
    ★분기에 한 번 바뀌는 값이라 캐시 — 평시 30일, 실적 시즌 11일(펀더멘털 7일·분기재무 9일과 어긋나게).
    ★못 받은 항목은 예전 값을 유지하고, 끝내 없으면 그 종목은 시가총액으로 대신 계산한다."""
    cache = _load(VAL_CACHE) or {}
    if cache.get("schema") != VAL_SCHEMA:     # 구조가 바뀌면 전부 다시 받는다
        cache = {}
    data = cache.get("data") or {}
    fresh = False
    try:
        age = datetime.now() - datetime.fromisoformat(cache["updated"])
        fresh = age < timedelta(days=30) and not (_is_earnings_season() and age > timedelta(days=11))
    except Exception:
        pass
    need = [sc for sc in meta if force or not fresh or sc not in data]
    if not need:
        return data
    print(f"  [캐시] 기업가치 자료 {len(need)}종목 받기…", flush=True)
    try:
        ys, crumb = _yahoo_session()
    except Exception as e:
        ys, crumb = None, None
        print(f"  [경고] 야후 접속 실패 — 이번엔 차입금·현금 없이 시가총액으로 계산: {e}", flush=True)

    def one(sc):
        out = {}
        try:
            d = _get(f"{DAUM}/quotes/{sc}?summary=false&changeStatistics=true", timeout=12)
            summ = d.get("companySummary") or ""
            out["holdco"] = bool(_HOLDCO_RE.search(summ))
            out["finlike"] = bool(_FINLIKE_RE.search(summ))
        except Exception:
            pass
        if ys:
            sym = sc[1:] + (".KQ" if meta.get(sc) == "KOSDAQ" else ".KS")
            for attempt in (1, 2, 3):
                try:
                    r = ys.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{sym}",
                               params={"modules": "financialData", "crumb": crumb}, timeout=15)
                    if r.status_code == 429:
                        time.sleep(2 * attempt)
                        continue
                    res = ((r.json() or {}).get("quoteSummary") or {}).get("result") or []
                    fdx = ((res[0] or {}).get("financialData") or {}) if res else {}
                    if fdx.get("financialCurrency") not in (None, "KRW"):
                        break
                    debt = (fdx.get("totalDebt") or {}).get("raw")
                    cash = (fdx.get("totalCash") or {}).get("raw")
                    if debt is not None or cash is not None:
                        out["debt"], out["cash"] = (debt or 0), (cash or 0)
                    break
                except Exception:
                    time.sleep(1)
        return sc, out

    with ThreadPoolExecutor(max_workers=6) as ex:
        got = list(ex.map(one, need))
    n_debt = 0
    for sc, v in got:
        if not v:
            continue
        old = data.get(sc) or {}
        old.update(v)                         # 이번에 못 받은 항목은 예전 값 유지
        data[sc] = old
        n_debt += ("debt" in v)
    # ★갱신 시도가 끝나면 날짜를 새로 찍는다 — 야후가 막혀도 매 실행 678종목을 다시 두드리지 않게.
    #   못 받은 차입금·현금은 예전 값을 그대로 쓰고(분기에 한 번 바뀌는 값), 다음 주기에 다시 시도한다.
    got_any = any(v for _, v in got)
    stamp = datetime.now().isoformat(timespec="seconds") if (got_any and not fresh) else cache.get("updated", "2000-01-01T00:00:00")
    _save(VAL_CACHE, {"schema": VAL_SCHEMA, "updated": stamp, "data": data})
    print(f"  [캐시] 기업가치 자료: 차입금·현금 {n_debt}/{len(need)} · 지주사 판별 {sum(1 for _, v in got if 'holdco' in v)}/{len(need)}", flush=True)
    return data


def _ttm(q):
    """최근 4분기 합 (영업이익, 매출). 4분기가 다 있어야 한다."""
    if not q or len(q) < 4:
        return 0, 0
    return (sum((r.get("op") or 0) for r in q[:4]), sum((r.get("sales") or 0) for r in q[:4]))


def _vp_excluded(x, v):
    """저가매수형에서 빼는 종목 — 배수 비교가 성립하지 않는 구조."""
    n, c = (x.get("name") or ""), (x.get("code") or "")
    if not c.endswith("0") or n.endswith("우") or "우B" in n:
        return "우선주"                       # 보통주 이익을 우선주 주가로 나누면 배수가 가짜가 된다
    if x.get("sector") in VP_EXCL_SECTORS or (v or {}).get("finlike") or n in VP_FINLIKE_NAMES:
        return "금융"                         # 결제·핀테크는 현금에 고객 예치금이 섞여 기업가치가 왜곡된다
    if (v or {}).get("holdco") or n in VP_HOLDCO_NAMES or n.endswith("홀딩스") or "지주" in n:
        return "지주사"                       # 자회사 이익이 연결로 잡혀 지주사 할인만큼 늘 싸 보인다
    return None


def pick_value(vitems, fin, val):
    """【저가매수형 v2】 돈 잘 버는데 주가가 너무 낮은 종목 — 업종 대비 기업가치 배수가 낮은 순.
    vitems: 일봉 지표가 붙은 종목(시총 상위 800 중 앞단 흑자 게이트 통과분 + 1년 흑자인데 적자 분기가 있던 종목)
    반환: (자격 종목 — 순위순, 집계)"""
    rows = []
    for x in vitems:
        sc = x.get("symbol") or ("A" + x["code"])
        v = val.get(sc) or {}
        if _vp_excluded(x, v):
            continue
        q = fin.get(sc) or []
        op_t, sa_t = _ttm(q)
        if len(q) < 4 or sa_t <= 0:
            continue
        x["ebit_ttm"], x["sales_ttm"] = op_t, sa_t
        x["opm_ttm"] = op_t / sa_t * 100
        s1 = (q[0].get("sales") or 0) + (q[1].get("sales") or 0)
        s0 = (q[2].get("sales") or 0) + (q[3].get("sales") or 0)
        x["sales_h_growth"] = ((s1 / s0 - 1) * 100) if s0 > 0 else None
        mcap = x.get("mcap")
        if mcap and v.get("debt") is not None and v.get("cash") is not None:
            x["net_debt"] = v["debt"] - v["cash"]
            x["ev"], x["ev_basis"] = mcap + x["net_debt"], "부채 반영"
        else:
            x["net_debt"], x["ev"], x["ev_basis"] = None, mcap, "시총"
        # 순현금이 시가총액보다 많으면 기업가치가 0 이하 — 배수 0으로 두고(가장 싼 쪽) 표시로 알린다
        x["ev_ebit"] = (max(x["ev"], 0) / op_t) if (op_t > 0 and x["ev"] is not None) else None
        rows.append(x)
    # 업종 중앙값 — 후보가 아니라 모집단 전체로 낸다(후보만으로 내면 기준이 후보에 끌려간다)
    sec_m, sec_o = {}, {}
    for x in rows:
        sec_o.setdefault(x.get("sector"), []).append(x["opm_ttm"])
        if x.get("ev_ebit"):
            sec_m.setdefault(x.get("sector"), []).append(x["ev_ebit"])
    all_m = [m for vs in sec_m.values() for m in vs]
    all_o = [o for vs in sec_o.values() for o in vs]
    med_m, med_o = (median(all_m) if all_m else None), (median(all_o) if all_o else None)
    sm = {k: median(vs) for k, vs in sec_m.items() if len(vs) >= 3}
    so = {k: median(vs) for k, vs in sec_o.items() if len(vs) >= 3}
    cand = []
    for x in rows:
        sec = x.get("sector")
        x["sector_opm"], x["sector_ev_ebit"] = so.get(sec, med_o), sm.get(sec, med_m)
        if sec not in so or sec not in sm:
            x["sector_note"] = "업종 표본 3개 미만 — 전체 중앙값으로 비교"
        if (x["ebit_ttm"] > 0                                                               # ①돈 번다
                and x["sector_opm"] is not None and x["opm_ttm"] >= x["sector_opm"]         # ②잘 번다
                and x.get("sales_h_growth") is not None and x["sales_h_growth"] >= VP_SALES_H_MIN   # ③실적 유지
                and x.get("dd_close") is not None and x["dd_close"] <= VP_DD_MAX            # ④눌림
                and x.get("share_ratio") is not None and VP_SHARE_LO < x["share_ratio"] < VP_SHARE_HI
                and (x.get("halt_days") or 0) <= VP_HALT_MAX
                and x.get("ev_ebit") is not None and x.get("sector_ev_ebit")):
            x["ev_rel"] = x["ev_ebit"] / x["sector_ev_ebit"]
            cand.append(x)
    cand.sort(key=lambda z: (z["ev_rel"], -(z.get("mcap") or 0)))      # ⑤업종 대비 배수 낮은 순
    return cand, {"universe": len(rows), "ev_mcap": sum(1 for x in rows if x.get("ev_basis") == "시총")}


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
    # ★시총 = 캐시된 상장주식수 × 당일가. 다음 marketCap과 산출 기준이 같다(실측 검증).
    #   시총을 캐시하면 급등 종목에서 최대 25% 어긋나 컷 자체가 틀어진다.
    mcap_now = {}
    for sc, f in fund.items():
        sh = f.get("shares")
        px = (prices.get(sc) or {}).get("price")
        if sh and px:
            mcap_now[sc] = px * sh
        elif f.get("mcap"):
            mcap_now[sc] = f["mcap"]      # ★폴백: 구버전 캐시. 전멸보다 낡은 값이 낫다

    ranked = sorted(
        [(sc, f) for sc, f in fund.items() if mcap_now.get(sc)],
        key=lambda z: mcap_now[z[0]], reverse=True)[:TOP_N]
    mcap_cut = mcap_now[ranked[-1][0]] if ranked else 0

    profit_ok = [(sc, f) for sc, f in ranked
                 if (f.get("op") or 0) > 0 and (f.get("ni") or 0) > 0]
    print(f"  시총상위 {len(ranked)} → 흑자 {len(profit_ok)}", flush=True)

    # 3-b) 분기재무 캐시 — 시총 상위 전체 기준(흑자 통과분은 매번 바뀌므로 모집단을 고정한다)
    fin = fetch_financials([sc for sc, _ in ranked])

    # 4) 일봉 (흑자 통과분만)
    def load(item):
        sc, f = item
        rows, halt_days = fetch_days(sc)
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
            # ★PBR도 PER과 같은 기준(전일종가 ÷ BPS). 캐시하면 급등분만큼 어긋난다.
            "pbr": (round((p.get("prevClose") or cur) / f["bps"], 2)
                    if (f.get("bps") and f["bps"] > 0) else f.get("pbr")),
            "dps": f.get("dps"),
            "sectorPer": f.get("sectorPer"), "mcap": mcap_now.get(sc),
            **ind,
        }
        out["halt_days"] = halt_days
        out["gap_drop"] = detect_gap_drop(rows)     # ★권리락 의심(공시 확정은 필터 단계에서)
        # 【저가매수형 원재료】
        d = derive_fundamentals(fin.get(sc))
        if d:
            ann = d["eps_ann"]
            out.update({
                "sales_growth": d["sales_growth"],                         # ②매출
                "eps_turnaround_q": d["eps_turnaround_q"],
                "eps_still_loss": d["eps_still_loss"],
                "eps_ann_pos": d["eps_ann_pos"],
                "opm_recent": d["opm_recent"], "opm_prev": d["opm_prev"],
                "opm_growth": d["opm_growth"],                             # ②영업이익률
                "profit_ok": d["profit_ok"],
                "op_2q_pos": d["op_2q_pos"],
                "roe_ann": d["roe_ann"],
                "eps_growth": d["eps_growth"], "eps_turnaround": d["eps_turnaround"],
                "eps_q_recent": d["eps_q_recent"], "eps_q_prev": d["eps_q_prev"],
                "eps_ann": ann,
                "earn_yield": (ann / cur * 100) if (ann > 0 and cur) else None,
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
        # ★1개월·6개월 지수 대비 — peak 유무와 무관하게 항상 계산한다.
        #   (블록 안에 두면 신고가 종목은 peak_date 조건에 막혀 모멘텀이 통째로 None이 된다)
        base_d = (rows[0].get("date") or "")[:10]
        i_now2 = index_at(idx, f.get("market"), base_d)
        for lab, n_, own in (("r1", 21, ind.get("r1")), ("r6", 126, ind.get("r6"))):
            if own is None or len(rows) <= n_ or not i_now2:
                continue
            i_past = index_at(idx, f.get("market"), (rows[n_].get("date") or "")[:10])
            if i_past:
                ix = (i_now2 / i_past - 1) * 100
                out[f"idx_{lab}"] = ix
                out[f"excess_{lab}"] = own - ix
        return out

    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        items = [x for x in ex.map(load, profit_ok) if x]
    print(f"  일봉 유효 {len(items)}", flush=True)
    if not items:
        return {"value_pick": [], "momentum": [], "stats": {"error": "일봉 수집 실패"}}

    # 4-b) 【저가매수형 전용】 1년(최근 4분기) 흑자인데 최근 분기 적자·순손실로 앞단 게이트에서 빠진 종목도 일봉을 받는다.
    #      ★모멘텀 모집단(items)은 그대로 둔다 — 모멘텀 판정이 바뀌지 않게.
    _gate = {sc for sc, _ in profit_ok}
    extra = [(sc, f) for sc, f in ranked if sc not in _gate and _ttm(fin.get(sc))[0] > 0]
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        extra_items = [x for x in ex.map(load, extra) if x]
    print(f"  저가매수형 추가 모집단 {len(extra_items)} (1년 흑자·최근 분기 적자 등)", flush=True)

    # 5) 백분위 — ★모멘텀형 1개월 순위에만 사용. 저가매수형은 백분위를 쓰지 않는다.
    p1 = percentile_map(items, "r1")

    # 6) 필터
    momentum = []
    for x in items:
        c = x["code"]
        # 【모멘텀형】 ★현행 유지 — 시장 국면에 따라 개수가 고무줄인 게 정상이다
        if (x["daily_aligned"]
                and x["entry_days"] <= 20
                and p1.get(c) is not None and p1[c] > 80
                and (x["v2060"] or 0) > 1.0):
            momentum.append(x)

    # 6-a) 【저가매수형 v2】 돈 잘 버는데 주가가 너무 낮은 종목 (모집단 = items + 추가 모집단)
    vitems = items + extra_items
    val = fetch_valuation({x["symbol"]: x.get("market") for x in vitems})
    value_cand, vp_meta = pick_value(vitems, fin, val)

    # 6-b) ★권리락 제외 — 급락 감지된 종목만 공시 조회(전 종목 호출 금지)
    #   무상·유상증자 권리락은 주가만 기계적으로 떨어지고 주식수는 그대로라
    #   listedSharesCount 필터로는 절대 못 잡는다. 낙폭이 통째로 가짜가 된다.
    susp = [x for x in value_cand if x.get("gap_drop")]
    if susp:
        print(f"  급락 감지 {len(susp)}종목 → 공시 확인", flush=True)
        with ThreadPoolExecutor(max_workers=min(8, len(susp))) as ex:
            marks = list(ex.map(lambda x: is_rights_offering(x["symbol"]), susp))
        drop = set()
        for x, mk in zip(susp, marks):
            if mk:
                drop.add(x["code"])
                g = x["gap_drop"]
                print(f"    ★제외 {x['name']}({x['code']}) {g['date']} {g['pct']:.1f}% — {mk}", flush=True)
        value_cand = [x for x in value_cand if x["code"] not in drop]

    # 7) 저가매수형 — 이미 업종 대비 배수 낮은 순 → 상위 N
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

    # ★저가매수형은 이미 업종 대비 기업가치 배수 순으로 정렬됨 — 다시 정렬하지 마라
    momentum.sort(key=lambda z: z.get("entry_days", 999))

    stats = {
        "universe": len(univ), "mcap_cut": mcap_cut,
        "top_n": len(ranked), "profit": len(profit_ok), "valid": len(items),
        "value_pick": len(value_pick), "vp_qualified": vp_qualified, "vp_cap": VP_TOP_N,
        "vp_universe": vp_meta["universe"], "vp_extra": len(extra_items), "vp_ev_mcap": vp_meta["ev_mcap"],
        "momentum": len(momentum),
        "elapsed": round(time.time() - t0, 1),
    }
    print(f"  → 저가매수 {len(value_pick)}(자격 {vp_qualified}) / 모멘텀 {len(momentum)}"
          f"  ({stats['elapsed']}초)", flush=True)
    return {"value_pick": value_pick, "momentum": momentum, "stats": stats}
