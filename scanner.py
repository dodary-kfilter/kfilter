# -*- coding: utf-8 -*-
"""
외국인·연기금 수급 스캐너 (토스 API 기반)
---------------------------------------------------------------
- 종목 명단: 네이버 시총순 (ETF·우선주·스팩 제외, 코스피 500 + 코스닥 300)
- 수급 데이터: 토스 trading-trend API (외국인·연기금 일별 순매수)
- 판정(각각 독립):
    외국인: 최근 WINDOW 거래일 누적 순매수>0 AND 순매수일 ≥ 70%
    연기금: 동일 기준
- 0순위 = 외국인 AND 연기금 둘 다 통과
- 결과를 data.json 으로 저장
실행: python scanner.py
---------------------------------------------------------------
"""
import re, time, json
from datetime import datetime
import requests

# ===== 설정 =====
KOSPI_N  = 500
KOSDAQ_N = 300
WINDOW   = 10
BUY_RATIO_MIN = 0.70
REQ_DELAY = 0.25     # 종목당 간격(초)
# ===============

HDR_NAVER = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Referer": "https://finance.naver.com/",
}
HDR_TOSS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.tossinvest.com/",
}
TOSS_URL = ("https://wts-info-api.tossinvest.com/api/v1/stock-infos/trade/trend/"
            "trading-trend?productCode=A{code}&size=60")

EXCLUDE_KEYWORDS = ["스팩", "KODEX", "TIGER", "PLUS", "ACE", "RISE", "SOL",
                    "KOSEF", "ARIRANG", "HANARO", "TIMEFOLIO", "KBSTAR",
                    "ETN", "선물", "레버리지", "인버스"]

def is_excluded(name):
    n = str(name)
    if re.search(r"우[A-C]?$", n):
        return True
    return any(kw in n for kw in EXCLUDE_KEYWORDS)

def get_list(sosok, want):
    """네이버 시총순. 제외 거른 보통주로 want개 채워 (code, name) 반환"""
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
    """토스에서 외국인·연기금 일별 순매수 시계열(최근→과거) 반환"""
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
    # 0순위 = 교집합
    f_codes = {x["code"] for x in foreign_pass}
    p_codes = {x["code"] for x in pension_pass}
    both_codes = f_codes & p_codes
    both = [x for x in foreign_pass if x["code"] in both_codes]

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
