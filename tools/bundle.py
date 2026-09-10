#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kfilter 번들 — 리포트 자료를 명령 한 번으로 받는다.

리포트 방은 이 명령 하나만 실행하고, 출력만 보고 판단한다.
  국장 1종목(깊게)    : curl -s https://raw.githubusercontent.com/dodary-kfilter/kfilter/main/tools/bundle.py | python3 - kr 440110
  국장 여러 종목(얕게): ... | python3 - kr 005930 000660 042700
  미장 1종목         : ... | python3 - us MU

깊게 = 시세 · 수급파일(없으면 다음에서 직접 계산) · 공시 목록 · 주요공시 본문 · 최신 정기보고서 발췌 · 뉴스 · 시장
얕게 = 시세 · 수급파일 요약 · 공시 제목 10건 (본문·정기보고서·뉴스 생략)
표준 라이브러리만 쓴다. 조회 하나가 실패해도 멈추지 않고 #오류에 적는다.
"""
import sys
import re
import json
import html
import time
import threading
import os
from concurrent.futures import TimeoutError as FutTimeout
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/124.0 Safari/537.36')
DAUM = 'https://finance.daum.net'
DART = 'https://dart.fss.or.kr'
RAW = 'https://raw.githubusercontent.com/dodary-kfilter/kfilter/main'
ERR = []
T_START = time.time()
DEADLINE = T_START + float(os.environ.get('BUNDLE_DEADLINE', '15'))   # 수집 마감 — 리포트 5분 예산에서 수집 몫


def wait(fu, what, default=None):
    """마감까지만 기다린다. 늦은 조회는 버리고 #오류에 적는다"""
    try:
        return fu.result(timeout=max(0.1, DEADLINE - time.time()))
    except FutTimeout:
        ERR.append('%s — 수집 마감(%.0f초) 초과로 생략' % (what, DEADLINE - T_START))
        return default
EX = ThreadPoolExecutor(max_workers=16)      # 개별 조회
DART_GATE = threading.BoundedSemaphore(3)    # DART 동시 접속 상한 — 몰아치면 접속 제한에 걸린다


# ───────────────────────────── 조회 공통
def decode(b, ct=''):
    m = re.search(r'charset=([\w-]+)', ct or '', re.I)
    if not m:
        m = re.search(r'charset=["\']?([\w-]+)', b[:4000].decode('ascii', 'ignore'), re.I)
    enc = (m.group(1) if m else 'utf-8').lower()
    if enc in ('euc-kr', 'ks_c_5601-1987', 'x-windows-949', 'ms949'):
        enc = 'cp949'
    try:
        return b.decode(enc)
    except (UnicodeDecodeError, LookupError):
        return b.decode('cp949' if enc.startswith('utf') else 'utf-8', 'ignore')


def fetch(url, ref=None, form=None, timeout=25, tries=2, quiet404=False, extra=None):
    hd = {'User-Agent': UA}
    hd.update(extra or {})
    if ref:
        hd['Referer'] = ref
    data = urllib.parse.urlencode(form).encode() if form else None
    gate = DART_GATE if url.startswith(DART) else None
    if gate:
        tries = max(tries, 3)
    last = None
    for i in range(tries):
        try:
            if gate:
                gate.acquire()
            try:
                req = urllib.request.Request(url, data=data, headers=hd)
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    return decode(r.read(), r.headers.get('Content-Type', ''))
            finally:
                if gate:
                    gate.release()
        except Exception as e:  # 네트워크·HTTP 오류는 기록만 하고 계속
            last = e
            if getattr(e, 'code', None) == 404:
                if quiet404:
                    return None
                break
            time.sleep((1.5 if gate else 0.8) * (i + 1))
    ERR.append('%s → %s' % (url.split('?')[0][-60:], str(last)[:80]))
    return None


def jget(url, ref=None, quiet404=False):
    s = fetch(url, ref, quiet404=quiet404)
    if s is None:
        return None
    try:
        return json.loads(s)
    except ValueError:
        ERR.append('JSON 해석 실패 ' + url.split('?')[0][-50:])
        return None


def daum(path, ref=DAUM + '/'):
    return jget(DAUM + path, ref)


def J(o):
    return json.dumps(o, ensure_ascii=False, separators=(',', ':'))


def n(x):
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def eok(x):
    """원 → 억원(정수)"""
    return None if x is None else round(x / 1e8)


def signed(v, change):
    if v is None:
        return None
    v = abs(n(v))
    return -v if change in ('FALL', 'LOWER_LIMIT') else v


def signed_rate(rate, change):
    """다음 changeRate(0~1, 부호 없이 오는 곳이 있음) → 부호 붙은 %"""
    if rate is None:
        return None
    v = round(abs(float(rate)) * 100, 2)
    return -v if change in ('FALL', 'LOWER_LIMIT') else v


def strip_tags(s):
    return re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', s or ''))).strip()


def name_in(name, title):
    return bool(re.search(re.escape(name) + r'(?![가-힣])', title or ''))


def cap(t, k):
    t = (t or '').strip()
    if len(t) <= k:
        return t
    cut = t[:k]
    i = cut.rfind('\n')
    return (cut[:i] if i > k * 0.6 else cut) + ' …(생략)'


# ───────────────────────────── 시세·시장
STATUS = {'PRE_MARKET': '장전 — 시세는 전일 종가, 오늘 등락 아님',
          'REGULAR_HOURS': '장중 — 확정 종가 아님', 'TRADING': '장중 — 확정 종가 아님',
          'CLOSING': '장 마감 — 확정 종가', 'AFTER_HOURS': '장 마감 — 확정 종가',
          'CLOSED': '장 마감 — 확정 종가'}


def quote_block(q):
    ch = q.get('change')
    st = q.get('marketStatus')
    warn = {k: v for k, v in (q.get('stockState') or {}).items()
            if v not in (None, False, '', 'NONE', 'NORMAL', 0)}
    return {
        '장상태': '%s (%s)' % (st, STATUS.get(st, '장 상태 확인 불가')),
        '시세시각': ('%s %s' % (q.get('tradeDate') or '', q.get('tradeTime') or '')).strip(),
        '현재가': n(q.get('tradePrice')), '전일대비': signed(q.get('changePrice'), ch),
        '등락률%': signed_rate(q.get('changeRate'), ch),
        '시가': n(q.get('openingPrice')), '고가': n(q.get('highPrice')), '저가': n(q.get('lowPrice')),
        '거래량': n(q.get('accTradeVolume')), '거래대금억': eok(q.get('accTradePrice')),
        '52주고가': [n(q.get('high52wPrice')), (q.get('high52wDate') or '')[:10]],
        '52주저가': [n(q.get('low52wPrice')), (q.get('low52wDate') or '')[:10]],
        '시총억': eok(q.get('marketCap')), '시총순위': q.get('marketCapRank'),
        '상장주식수': n(q.get('listedShareCount')),
        'PER': q.get('per'), 'PBR': q.get('pbr'), 'EPS': n(q.get('eps')), 'BPS': n(q.get('bps')),
        'DPS': n(q.get('dps')), '밸류주의': 'PER·PBR·EPS·BPS는 갱신이 늦거나 전년 확정 기준일 수 있다',
        '업종': q.get('wicsSectorName'), '업종PER': q.get('sectorPer'),
        '외국인지분%': None if q.get('foreignRatio') is None else round(q['foreignRatio'] * 100, 2),
        '외국인보유주식': n(q.get('foreignOwnShares')),
        '최근분기억': {'매출': eok(q.get('sales')), '영업이익': eok(q.get('operatingProfit')),
                   '순이익': eok(q.get('netIncome'))},
        '부채비율%': None if q.get('debtRatio') is None else round(q['debtRatio'] * 100, 1),
        '시장경보': warn or '없음',
        '회사개요': (q.get('companySummary') or '')[:280],
    }


def market_block(mkt):
    d = daum('/api/market_index/days?page=1&perPage=61&market=%s&pagination=true' % mkt)
    rows = (d or {}).get('data') or []
    if not rows:
        return {}
    c = [r.get('tradePrice') for r in rows]

    def ch(k):
        return round((c[0] / c[k] - 1) * 100, 2) if len(c) > k and c[k] else None
    r0 = rows[0]
    return {mkt: {'날짜': (r0.get('date') or '')[:10], '지수': r0.get('tradePrice'),
                  '1일%': ch(1), '5일%': ch(5), '20일%': ch(20), '60일%': ch(60),
                  '당일순매수억_개인·외국인·기관': [eok(r0.get('individualStraightPurchasePrice')),
                                          eok(r0.get('foreignStraightPurchasePrice')),
                                          eok(r0.get('institutionStraightPurchasePrice'))]}}


GL_KEEP = re.compile(r'USD/KRW|다우|나스닥|S&P|필라델피아|반도체|원/달러|달러/원|미국 USD|WTI|국채|VIX|닛케이|니케이|상해|항셍|대만|가권')


def walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from walk(v)


def global_block():
    got = {}
    for path in ('/api/global/today', '/api/global/exchanges'):
        for it in walk(daum(path, DAUM + '/global')):
            nm = it.get('name') or it.get('symbolName')
            if nm and 'tradePrice' in it and GL_KEEP.search(nm) and nm not in got:
                got[nm] = [n(it.get('tradePrice')), signed_rate(it.get('changeRate'), it.get('change')),
                           (it.get('date') or '')[:10]]
    return got


def market_all():
    a, b, g = EX.submit(market_block, 'KOSPI'), EX.submit(market_block, 'KOSDAQ'), EX.submit(global_block)
    m = {}
    m.update(a.result())
    m.update(b.result())
    return m, g.result()


# ───────────────────────────── 수급파일이 없을 때 직접 계산
def price_block(d, nd=0):
    rnd = (lambda v: round(v, nd)) if nd else round            # 미국 주가는 소수점이 있다
    rows = [r for r in ((d or {}).get('data') or []) if r.get('accTradeVolume')]  # 장전 더미행(거래량 0) 제거
    if len(rows) < 5:
        return None
    c = [r['tradePrice'] for r in rows]
    v = [r['accTradeVolume'] for r in rows]
    out = {'기준일': (rows[0].get('date') or '')[:10], '종가': n(c[0])}
    for k in (5, 20, 60, 120):
        if len(c) >= k:
            m = sum(c[:k]) / k
            out['이평%d_이격%%' % k] = [rnd(m), round((c[0] / m - 1) * 100, 1)]
    for k in (5, 20, 60, 120, 240):
        if len(c) > k:
            out['수익률%d일%%' % k] = round((c[0] / c[k] - 1) * 100, 1)
    hi, lo = max(c[:250]), min(c[:250])
    out['기간고저'] = [n(hi), n(lo), '%d거래일' % min(len(c), 250)]
    out['고점대비%'] = round((c[0] / hi - 1) * 100, 1)
    if len(v) >= 60:
        a60 = sum(v[:60]) / 60
        out['거래량배수_20대60·당일대60'] = [round(sum(v[:20]) / 20 / a60, 2), round(v[0] / a60, 2)]
    pts = [(r['tradePrice'], r['accTradeVolume']) for r in rows[:120]
           if 0.4 * c[0] <= r['tradePrice'] <= 2.5 * c[0]]
    if len(pts) >= 20:
        lo2, hi2 = min(p for p, _ in pts), max(p for p, _ in pts)
        if hi2 > lo2:
            w, vol = (hi2 - lo2) / 10, [0] * 10
            for p, q in pts:
                vol[min(int((p - lo2) / w), 9)] += q
            tot = sum(vol) or 1
            top = sorted(range(10), key=lambda i: -vol[i])[:4]
            out['매물대120일_하단·상단·비중%'] = [[rnd(lo2 + i * w), rnd(lo2 + (i + 1) * w),
                                           round(vol[i] * 100 / tot)] for i in sorted(top)]
    return out


def pc(x):
    return None if x is None else round(x * 100, 2)


def supply_block(d):
    rows = (d or {}).get('data') or []
    if not rows:
        return None
    fv = [r.get('foreignStraightPurchaseVolume') or 0 for r in rows]
    iv = [r.get('institutionStraightPurchaseVolume') or 0 for r in rows]

    def cum(a, k):
        return int(sum(a[:k]))
    rate = [r.get('foreignOwnSharesRate') for r in rows]
    return {'기준일': (rows[0].get('date') or '')[:10],
            '외국인순매수주_5·20·60일': [cum(fv, 5), cum(fv, 20), cum(fv, len(fv))],
            '기관순매수주_5·20·60일': [cum(iv, 5), cum(iv, 20), cum(iv, len(iv))],
            '외국인지분율%_오늘·20일전·60일전': [pc(rate[0]), pc(rate[min(20, len(rate) - 1)]), pc(rate[-1])],
            '주의': '장내 순매수만 집계 — 지분율 변화와 크게 어긋나면 장외 이동, 개인·연기금 없음'}


def fin_block(d):
    data = (d or {}).get('data') or {}
    out = {'열': '기간,매출억,영업이익억,순이익억,EPS,ROE,부채비율'}
    for key, k in (('QUARTER', 6), ('YEAR', 4)):
        out[key] = [[(r.get('date') or '')[:7], eok(r.get('sales')), eok(r.get('operatingProfit')),
                     eok(r.get('netIncome')), n(r.get('eps')), r.get('roe'), r.get('debtRatio')]
                    for r in (data.get(key) or [])[:k]]
    return out


# ───────────────────────────── 수급파일 정리
def mark_consensus(fin):
    """네이버 재무의 컨센 열(isConsensus=Y) 이름 끝에 E를 붙인다 — 추정치를 확정치로 오독하지 않게."""
    try:
        cons = {t['key'] for t in fin['trTitleList'] if t.get('isConsensus') == 'Y'}
        rows = []
        for r in fin['rowList']:
            r = dict(r)
            for ck in ('columns', 'cols', 'values'):
                if isinstance(r.get(ck), dict):
                    r[ck] = {(k + 'E' if k in cons else k): v for k, v in r[ck].items()}
            rows.append(r)
        out = {k: v for k, v in fin.items() if k not in ('trTitleList', 'rowList', 'itemCode')}
        out['rowList'] = rows
        out['표기'] = '열 이름 끝 E = 컨센서스 추정치'
        return out
    except (KeyError, TypeError):
        return fin


FILE_MAP = '''#수급파일 키 지도 — 대부분 이미 계산돼 있다
price_daily: ma 이평 · ma.vs 이격 · pocket 매물대 구간별 거래비중 · trend20 | price_weekly·price_monthly: ma · posPct · rangeHigh/rangeLow · recent
volume_stats: ratio_20_60 · today_vs_60 거래량 배수 | idxRel: ddFromPeak · idxSincePeak · excessDd · m1Excess · m6Excess 지수·고점 대비
supply_detail: cum_5d·cum_20d·cum_60d 11개 주체 누적(연기금·개인 포함) · recent_daily {cols,rows} 최신순 60행, foreignRatio는 이미 %
supply_derived: cum_21_60d 21~60일 구간(60일에서 20일을 빼지 말 것) · pattern 주체별 판정 · foreign_avg_price · foreign_avg_vs_now_pct | supply_10d: 최근 10거래일 외국인·연기금
valuation_derived: theo_pbr · premium_pct · verdict (roe_capped=true면 ROE 상한 적용 — roe_used_pct) | valuation: 네이버 밸류 원본
financials_confirmed: 확정 실적 분기·연간(실적 판단 기준) | financials_annual·financials_quarter: 네이버 원본, 열 이름 끝 E = 컨센서스 추정치, 값은 문자열
earnings_alert: 잠정실적 공시 유무 | consensus·researches: 컨센서스 목표가·증권사 리포트 제목 | industry_peers: 동종 시총·현재가·등락률 | news: 제목에 종목명 있는 것만
target_context: 이 종목이 같은 기간에 실제로 오른 폭의 분포 | prev_report·prev_track: 직전 리포트 전문·그 목표의 진척 | splits: 권리락 이력(파일 안 주가는 이미 보정)'''

HEAVY_SHALLOW = ('prev_report', 'price_weekly', 'price_monthly', 'financials_annual', 'financials_quarter',
                 'industry_peers', 'news', 'researches', 'target_context', 'valuation')


def slim_file(f, name, deep):
    f = dict(f)
    for k in ('disclosures', 'disclosures_author', 'code', 'name'):
        f.pop(k, None)                         # 공시는 #공시목록이 대신한다
    if not f.get('_errors'):
        f.pop('_errors', None)
    for key in ('financials_annual', 'financials_quarter'):
        if f.get(key):
            f[key] = mark_consensus(f[key])
    if isinstance(f.get('news'), list):
        f['news'] = [x for x in f['news'] if name_in(name, x.get('title', ''))]
    if not deep:
        for k in HEAVY_SHALLOW:
            f.pop(k, None)
        sd = f.get('supply_detail')
        if isinstance(sd, dict):
            f['supply_detail'] = {k: v for k, v in sd.items() if k != 'recent_daily'}
    return f


# ───────────────────────────── 공시
def dart_list(code, name, days=180):
    form = {'currentPage': 1, 'maxResults': 100, 'maxLinks': 10, 'sort': 'date', 'series': 'desc',
            'textCrpNm': code, 'startDate': (NOW - timedelta(days=days)).strftime('%Y%m%d'),
            'endDate': NOW.strftime('%Y%m%d'), 'option': 'corp', 'finalReport': 'recent',
            'businessCode': 'all', 'corporationType': 'all', 'closingAccountsMonth': 'all'}
    s = fetch(DART + '/dsab007/detailSearch.ax', form=form) or ''
    rows, seen = [], set()                     # 종목코드로 찾으면 정식 회사명(현대차→현대자동차)으로 정확히 잡힌다
    for tr in re.findall(r'(?s)<tr.*?</tr>', s):
        m = re.search(r'rcpNo=(\d+)', tr)
        if not m:
            continue
        raw = re.findall(r'(?s)<td[^>]*>(.*?)</td>', tr)
        cells = [strip_tags(c) for c in raw]
        if len(cells) < 5:
            continue
        cc = re.search(r"openCorpInfoNew\(\s*'(\d+)'", raw[1])      # DART 고유번호 — 회사명 칸엔 IR 배지 글자가 섞인다
        corp = cc.group(1) if cc else re.sub(r'^[유코넥기]\s+', '', cells[1]).replace(' ', '')
        key = (cells[4], cells[2])
        if key in seen:
            continue
        seen.add(key)
        rows.append((corp, {'rcp': m.group(1), 'date': cells[4].replace('.', '-'),
                            'title': re.sub(r'\s+', ' ', cells[2]), 'by': cells[3],
                            'corp': re.sub(r'^[유코넥기]\s+|\s*IR$', '', cells[1])}))
    if rows:
        names = [c for c, _ in rows]
        main = max(set(names), key=names.count)
        keep = [it for c, it in rows if c == main]
        return keep, 'DART(%s)' % keep[0]['corp']
    items = []
    d = daum('/api/disclosures?symbolCode=A%s&perPage=40&page=1' % code)     # DART 검색 실패 시 거래소 공시만
    for it in (d or {}).get('data') or []:
        items.append({'rcp': None, 'date': (it.get('createdAt') or '')[:10],
                      'title': re.sub(r'^\(주\)\S+\s', '', it.get('title') or ''), 'by': 'KRX'})
    return items, '다음(거래소 공시만 — DART 검색 실패)'


SKIP = re.compile(r'기업설명회|추가상장|주식매수선택권|투자주의|투자경고|지정예고|지정해제|거래정지\s*예고|반기보고서|분기보고서|'
                  r'사업보고서|주주총회소집|결산실적공시\s*예고|기업지배구조|증권발행실적|투자설명서')
PRIO = [(0, re.compile(r'대량보유|최대주주|특수관계|전환사채|신주인수권|교환사채|유상증자|무상증자|감자|자기주식|주식소각|'
                       r'합병|분할|양수|양도|회생|부도|횡령|배임|상장폐지|관리종목|감사보고서|감사의견')),
        (1, re.compile(r'잠정|손익구조|파생상품|손실발생|소송|공급계약|판매ㆍ공급|수주|타법인|유형자산|시설투자|차입|'
                       r'채무보증|투자판단|배당|불성실|거래정지')),
        (2, re.compile(r'소유상황|소유주식|주요주주|임원'))]
PER_GROUP = [(re.compile(r'공급계약|수주'), 4), (re.compile(r'대량보유'), 3)]


def pick_docs(items, k=10, days=150):
    cutoff = (NOW - timedelta(days=days)).strftime('%Y-%m-%d')
    count, cand = {}, []
    for it in items:                                   # 최신순으로 들어온다
        t = it['title']
        if not it.get('rcp') or it['date'] < cutoff or SKIP.search(t):
            continue
        pr = next((p for p, rx in PRIO if rx.search(t)), None)
        if pr is None:
            continue
        fix = '정정' in t
        base = re.sub(r'\[[^\]]*\]|\([^)]*\)|\s', '', t) + ('#정정' if fix else '')
        lim = 2 if fix else next((c for rx, c in PER_GROUP if rx.search(t)), 1)
        if count.get(base, 0) >= lim:
            continue
        count[base] = count.get(base, 0) + 1
        cand.append((pr + (1 if fix else 0), -int(it['date'].replace('-', '')), it))
    cand.sort(key=lambda x: (x[0], x[1]))
    chosen = [c[2] for c in cand[:k]]
    return sorted(chosen, key=lambda it: it['date'], reverse=True)


BOILER = re.compile(r'^※|귀중$|허위기재|기재누락|정확하게 작성|^\(약식서식|^\(일반서식|^확인서$|^위와 같이|생년월일|전화번호|팩스번호|이메일|읍ㆍ면ㆍ동|업무상 연락처')


def html_text(h):
    h = re.sub(r'(?is)<(script|style|head).*?</\1>', ' ', h or '')
    h = re.sub(r'\s+', ' ', h)          # 원문 줄바꿈은 뜻이 없다 — 그대로 두면 표 칸이 줄마다 갈라진다
    h = re.sub(r'(?i)<br\s*/?>|</p>|</tr>|</h\d>|</div>|</table>|</li>|</title>', '\n', h)
    h = re.sub(r'(?i)</t[dh]>', ' | ', h)
    h = html.unescape(re.sub(r'<[^>]+>', ' ', h))
    out, prev = [], None
    for line in h.split('\n'):
        line = re.sub(r'[ \t\xa0\u3000]+', ' ', line)
        line = re.sub(r'(\s*\|\s*)+', ' | ', line).strip(' |')
        if not line or line == prev or BOILER.search(line):
            continue
        out.append(line)
        prev = line
    return '\n'.join(out)


def dart_nodes(rcp):
    h = fetch('%s/dsaf001/main.do?rcpNo=%s' % (DART, rcp))
    if not h:
        return []
    nodes, cur = [], None
    for _v, k, val in re.findall(r"(node\d+)\['(\w+)'\]\s*=\s*[\"'](.*?)[\"'];", h):
        if k == 'text':
            cur = {'text': html.unescape(val)}
            nodes.append(cur)
        elif cur is not None:
            cur[k] = val
    nodes = [x for x in nodes if x.get('dcmNo')]
    if nodes:
        return nodes
    m = re.search(r"viewDoc\(\s*['\"](\d+)['\"]\s*,\s*['\"](\d+)['\"]\s*,\s*([^,]*),\s*([^,]*),\s*([^,]*),"
                  r"\s*['\"]([^'\"]*)['\"]", h)
    if m:
        return [{'text': '', 'dcmNo': m.group(2), 'eleId': '0', 'offset': '0', 'length': '0', 'dtd': m.group(6)}]
    m = re.search(r'dcmNo\W{1,8}(\d{5,})', h)
    if m:
        d = re.search(r'([\w.-]+\.xsd)', h)
        return [{'text': '', 'dcmNo': m.group(1), 'eleId': '0', 'offset': '0', 'length': '0',
                 'dtd': d.group(1) if d else 'dart3.xsd'}]
    ERR.append('DART 목차 해석 실패 %s' % rcp)
    return []


def dart_view(rcp, nd):
    url = ('%s/report/viewer.do?rcpNo=%s&dcmNo=%s&eleId=%s&offset=%s&length=%s&dtd=%s'
           % (DART, rcp, nd['dcmNo'], nd.get('eleId', '0'), nd.get('offset', '0'), nd.get('length', '0'),
              nd.get('dtd', 'dart3.xsd')))
    return html_text(fetch(url) or '')


SKIP_NODE = re.compile(r'확인서|대표이사\s*등의\s*확인|전문가의\s*확인')


def doc_text(it, k=900):
    nodes = dart_nodes(it['rcp'])
    buf = []
    for nd in [x for x in nodes if not SKIP_NODE.search(x.get('text', ''))][:3]:
        buf.append(dart_view(it['rcp'], nd))
        if sum(len(b) for b in buf) >= k:
            break
    t = '\n'.join(b for b in buf if b)
    i = t.find('\n제1부')
    if i > 0 and '요약정보' in t[:i]:          # 지분 보고서는 앞의 요약정보가 핵심이다
        t = t[:i]
    return cap(t, k)


SECTIONS = [
    ('매출·수주', [r'매출\s*및\s*수주', r'영업의\s*현황'], 1800, None),          # 금융업 서식은 '영업의 현황'
    ('재무상태표(발췌)', [r'연결\s*재무상태표', r'^\s*\d-\d\.\s*재무상태표'], 1000,
     r'단위|현금|재고|차입|사채|전환|파생|총계|잉여금|결손|자본금'),
    ('손익계산서(발췌)', [r'연결\s*포괄손익계산서', r'연결\s*손익계산서', r'^\s*\d-\d\.\s*포괄손익계산서', r'^\s*\d-\d\.\s*손익계산서'], 900,
     r'단위|매출|영업수익|영업비용|영업이익|영업손실|금융수익|금융비용|금융원가|파생|지분법|법인세|당기순|반기순|분기순|주당'),
    ('기타 재무', [r'기타\s*재무에\s*관한'], 900, None),
    ('재무건전성', [r'재무건전성'], 1000, None),                                   # 금융업 서식에만 있다
    ('주주', [r'주주에\s*관한\s*사항'], 1000, None),
    ('우발부채·소송', [r'우발부채'], 900, None),
    ('작성기준일 이후', [r'작성기준일\s*이후'], 1000, None),
]


def section_text(rcp, nd, k, rowf):
    t = dart_view(rcp, nd)
    if rowf:
        lines = t.split('\n')
        t = '\n'.join(lines[:2] + [ln for ln in lines[2:] if re.search(rowf, ln.replace(' ', ''))])  # 재무표는 '영 업 이 익'처럼 띄어 쓰는 회사가 있다
    return cap(t, k)


def periodic(items):
    rep = next((it for it in items if it.get('rcp') and re.search(r'사업보고서|반기보고서|분기보고서', it['title'])), None)
    if not rep:
        return None, []
    nodes = dart_nodes(rep['rcp'])
    jobs = []
    for label, pats, k, rowf in SECTIONS:
        for p in pats:
            hit = next((nd for nd in nodes if re.search(p, nd.get('text', ''))), None)
            if hit:
                jobs.append((label, hit.get('text', ''), EX.submit(section_text, rep['rcp'], hit, k, rowf)))
                break
    return rep, [(label, title, wait(fu, '정기보고서 %s' % label, '(수집 마감 초과로 생략)')) for label, title, fu in jobs]


# ───────────────────────────── 뉴스
def gnews(name, k=8):
    url = ('https://news.google.com/rss/search?q=%s+when:14d&hl=ko&gl=KR&ceid=KR:ko'
           % urllib.parse.quote('"%s"' % name))
    s = fetch(url) or ''
    out, seen = [], set()
    for it in re.findall(r'(?s)<item>(.*?)</item>', s):
        tm = re.search(r'(?s)<title>(.*?)</title>', it)
        dm = re.search(r'<pubDate>(.*?)</pubDate>', it)
        title = strip_tags(html.unescape(tm.group(1))) if tm else ''
        if not name_in(name, title):
            continue
        try:
            dt = datetime.strptime(dm.group(1)[:25], '%a, %d %b %Y %H:%M:%S').replace(tzinfo=timezone.utc).astimezone(KST)
        except (AttributeError, ValueError):
            continue
        key = re.sub(r'\s+-\s+[^-]+$', '', title)
        if key in seen:
            continue
        seen.add(key)
        out.append((dt, title))
    out.sort(key=lambda x: x[0], reverse=True)
    return ['%s %s' % (dt.strftime('%m-%d %H:%M'), t) for dt, t in out[:k]]


# ───────────────────────────── 종목 하나
def kr_bundle(code, deep):
    code = re.sub(r'^A', '', code.strip().upper())
    fq = EX.submit(daum, '/api/quotes/A%s?summary=false&changeStatistics=true' % code)
    ff = EX.submit(jget, '%s/report-data/%s.json' % (RAW, code), None, True)
    q = fq.result() or {}
    name = q.get('name') or code
    fl = EX.submit(dart_list, code, name)
    fg = EX.submit(gnews, name) if deep else None
    out = ['', '==== %s(%s) %s ====' % (name, code, q.get('market') or '')]
    ctx = {'q': q, 'name': name, 'code': code}
    fn_all = EX.submit(daum, '/api/quote/A%s/financials' % code) if deep else None
    out.append('#시세 ' + (J(quote_block(q)) if q else '조회 실패'))
    f = ff.result()
    ctx['file'] = f
    if f:
        out.append('#수급파일 kfilter report-data · 갱신 %s · 스캔 시점 값(실시간 아님)' % f.get('updated'))
        if deep:
            out.append(FILE_MAP)
        fj = J(slim_file(f, name, deep))
        ctx['file_chars'] = len(fj)
        out.append(fj)
    else:
        fd = EX.submit(daum, '/api/quote/A%s/days?perPage=250&page=1' % code)
        fi = EX.submit(daum, '/api/investor/days?symbolCode=A%s&perPage=60&page=1' % code)
        fn = fn_all or EX.submit(daum, '/api/quote/A%s/financials' % code)
        out.append('#수급파일 없음 — 아래 셋은 다음에서 직접 계산했다')
        ctx['pb'], ctx['sb'], ctx['fin'] = price_block(fd.result()), supply_block(fi.result()), fn.result()
        out.append('#가격 ' + J(ctx['pb']))
        out.append('#수급 ' + J(ctx['sb']))
        out.append('#재무확정 ' + J(fin_block(ctx['fin'])))
    items, src = fl.result()
    ctx['items'], ctx['src'] = items, src
    if fn_all is not None and 'fin' not in ctx:
        ctx['fin'] = fn_all.result()
    shown = items if deep else items[:10]
    out.append('#공시목록 출처 %s · 최근 180일 · 최신순 · 날짜|제목|제출인 (%d건 중 %d건)'
               % (src, len(items), min(len(shown), 60)))
    out.extend('%s|%s|%s' % (it['date'], it['title'], it['by']) for it in shown[:60])
    if deep:
        docs = pick_docs(items)
        ctx['docs'] = docs
        futs = [(it, EX.submit(doc_text, it)) for it in docs]
        fp = EX.submit(periodic, items)
        out.append('#공시본문 주요공시 %d건 · 건당 앞부분만' % len(docs))
        for it, fu in futs:
            out.append('▶ %s %s · %s' % (it['date'], it['title'], it['by']))
            out.append(wait(fu, '공시 본문 %s %s' % (it['date'], it['title'][:20]), '(수집 마감 초과로 생략)') or '(본문 없음)')
        rep, secs = wait(fp, '정기보고서 발췌', (None, []))
        ctx['rep'], ctx['secs'] = rep, secs
        if rep:
            out.append('#정기보고서 %s · 접수 %s · 절별 발췌' % (rep['title'], rep['date']))
            for label, title, txt in secs:
                out.append('▷ %s — %s' % (label, title))
                out.append(txt or '(내용 없음)')
        else:
            out.append('#정기보고서 최근 180일 안에 없음')
        news = wait(fg, '뉴스', [])
        ctx['news'] = news
        out.append('#뉴스 최근 14일 · 제목에 종목명 있는 것만 · %d건' % len(news))
        out.extend(news)
    return out, ctx


# ───────────────────────────── 미국 (나스닥 = 시세·재무·실적·수급 대용, 야후 = 시계열만)
NASDAQ = 'https://api.nasdaq.com/api'
NQ_HEAD = {'Accept': 'application/json, text/plain, */*', 'Origin': 'https://www.nasdaq.com'}


def nq(path):
    s = fetch(NASDAQ + path, 'https://www.nasdaq.com/', extra=NQ_HEAD)
    if not s:
        return None
    try:
        j = json.loads(s)
    except ValueError:
        ERR.append('나스닥 JSON 해석 실패 ' + path.split('?')[0])
        return None
    return j.get('data') if isinstance(j, dict) else None


def ychart(sym, rng='2y'):
    d = jget('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=1d&events=div,splits'
             % (urllib.parse.quote(sym), rng))
    try:
        r = d['chart']['result'][0]
        q = r['indicators']['quote'][0]
    except (TypeError, KeyError, IndexError):
        ERR.append('야후 시계열 없음 ' + sym)
        return None
    rows = []
    for i, t in enumerate(r.get('timestamp') or []):
        c = q['close'][i]
        if c is None:
            continue
        rows.append({'date': datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d'),
                     'tradePrice': round(c, 4), 'accTradeVolume': q['volume'][i] or 0})
    rows.reverse()
    return rows, (r.get('events') or {})


def idx_series(sym):
    y = ychart(sym, '6mo')
    if not y or not y[0]:
        return None
    c = [r['tradePrice'] for r in y[0]]

    def ch(k):
        return round((c[0] / c[k] - 1) * 100, 2) if len(c) > k and c[k] else None
    return [round(c[0], 2), ch(5), ch(20), ch(60)]


US_IDX = [('S&P500', '^GSPC', '미국 S&P 500'), ('나스닥', '^IXIC', '미국 나스닥 종합'),
          ('필라델피아반도체', '^SOX', '미국 필라델피아 반도체'), ('VIX', '^VIX', None),
          ('미국10년물금리', '^TNX', None), ('달러인덱스', 'DX-Y.NYB', None)]


def us_market_all():
    fut = [(nm, EX.submit(idx_series, sym), gk) for nm, sym, gk in US_IDX]
    g = global_block()
    mk = {}
    for nm, fu, gk in fut:
        v = fu.result()
        if not v:
            continue
        d = {'수준': v[0], '5일%': v[1], '20일%': v[2], '60일%': v[3]}
        if gk and gk in g:
            d['1일%'] = g[gk][1]
            d['기준일'] = g[gk][2]
        mk[nm] = d
    return mk, {k: v for k, v in g.items() if re.search(r'USD/KRW|WTI', k)}


US_STATUS = {'Open': '정규장 중 — 확정 종가 아님', 'Pre-Market': '프리마켓 — 정규장 시세는 전일 종가',
             'After-Hours': '애프터마켓 — 정규장 종가 확정, 장외 시세는 장외 항목', 'Closed': '장 마감 — 확정 종가'}


def clean_name(nm):
    nm = re.sub(r'\s+(Common Stock|Class [A-C].*|Ordinary Shares.*|American Depositary.*|ADS.*)$', '', nm or '')
    return re.sub(r',?\s+(Inc\.?|Incorporated|Corporation|Corp\.?|Co\.?|Ltd\.?|Limited|plc|PLC|N\.V\.|S\.A\.|Holdings?)$', '',
                  nm).strip()


def us_quote(info, summ):
    p = info.get('primaryData') or {}
    sd = {k: (v or {}).get('value') for k, v in ((summ or {}).get('summaryData') or {}).items()}
    st = info.get('marketStatus')
    try:
        cap_ = round(int(str(sd.get('MarketCap')).replace(',', '')) / 1e8)
    except (TypeError, ValueError):
        cap_ = None
    vol = re.sub(r'\.\d+$', '', str(p.get('volume') or ''))
    out = {'종목': info.get('companyName'), '거래소': info.get('exchange'),
           '장상태': '%s (%s)' % (st, US_STATUS.get(st, '장 상태 확인 불가')),
           '시각ET': p.get('lastTradeTimestamp'), '실시간': p.get('isRealTime'),
           '현재가': p.get('lastSalePrice'), '전일대비': p.get('netChange'), '등락률': p.get('percentageChange'),
           '거래량': vol, '평균거래량': sd.get('AverageVolume'), '전일종가': sd.get('PreviousClose'),
           '당일고저': sd.get('TodayHighLow'), '52주고저': sd.get('FiftTwoWeekHighLow'), '시총억달러': cap_,
           '업종': sd.get('Sector'), '산업': sd.get('Industry'), '나스닥1년목표': sd.get('OneYrTarget'),
           '배당_연간·수익률·배당락일': [sd.get('AnnualizedDividend'), sd.get('Yield'), sd.get('ExDividendDate')]}
    s2 = info.get('secondaryData')
    if isinstance(s2, dict):
        out['장외'] = {k: s2.get(k) for k in ('lastSalePrice', 'netChange', 'percentageChange', 'lastTradeTimestamp')
                     if s2.get(k)}
    return out


FIN_ROWS = [('incomeStatementTable', '손익', r'^(Total Revenue|Gross Profit|Research and Development|Operating Income|Net Income)$'),
            ('balanceSheetTable', '재무상태', r'^(Cash and Cash Equivalents|Short-Term Investments|Inventory|Total Assets|'
                                          r'Short-Term Debt / Current Portion of Long-Term Debt|Long-Term Debt|Total Liabilities|Total Equity)$'),
            ('cashFlowTable', '현금흐름', r'^(Net Cash Flow-Operating|Capital Expenditures|Sale and Purchase of Stock|Net Borrowings)$'),
            ('financialRatiosTable', '비율', r'^(Gross Margin|Operating Margin|Profit Margin|After Tax ROE|Current Ratio)$')]


def fin_tables(f, only=None):
    out = {}
    for key, label, rx in FIN_ROWS:
        if only and label not in only:
            continue
        tb = (f or {}).get(key) or {}
        hd = tb.get('headers') or {}
        cols = [hd[k] for k in sorted(hd, key=lambda x: int(re.sub(r'\D', '', x) or 0)) if k != 'value1']
        rows = {}
        for r in tb.get('rows') or []:
            if re.search(rx, r.get('value1') or ''):
                rows[r['value1']] = [r.get('value%d' % i) for i in range(2, 2 + len(cols))]
        if rows:
            out[label] = dict([('기간', cols)] + list(rows.items()))
    return out


def us_earn(su, fo, tg):
    out = {'EPS서프라이즈_분기·발표일·실제·컨센·서프%': [
        [r.get('fiscalQtrEnd'), r.get('dateReported'), r.get('eps'), r.get('consensusForecast'), r.get('percentageSurprise')]
        for r in (((su or {}).get('earningsSurpriseTable') or {}).get('rows') or [])[:4]]}
    for key, lab in (('quarterlyForecast', '컨센EPS_분기'), ('yearlyForecast', '컨센EPS_연도')):
        out[lab + '·평균·최고·최저·추정수·상향·하향'] = [
            [r.get('fiscalEnd'), r.get('consensusEPSForecast'), r.get('highEPSForecast'), r.get('lowEPSForecast'),
             r.get('noOfEstimates'), r.get('up'), r.get('down')] for r in (((fo or {}).get(key) or {}).get('rows') or [])[:3]]
    co = (tg or {}).get('consensusOverview') or {}
    out['목표가'] = {'평균': co.get('priceTarget'), '최저': co.get('lowPriceTarget'), '최고': co.get('highPriceTarget'),
                  '매수·보유·매도': [co.get('buy'), co.get('hold'), co.get('sell')],
                  '의견추이_날짜·매수·보유·매도': [[(h.get('z') or {}).get('date'), (h.get('z') or {}).get('buy'),
                                          (h.get('z') or {}).get('hold'), (h.get('z') or {}).get('sell')]
                                         for h in ((tg or {}).get('historicalConsensus') or [])[-3:]]}
    return out


def us_flow(ins, sh, it):
    ins, sh, it = ins or {}, sh or {}, it or {}
    out = {'기관보유': {v.get('label'): v.get('value') for v in (ins.get('ownershipSummary') or {}).values() if isinstance(v, dict)}}
    pos = {}
    for key in ('activePositions', 'newSoldOutPositions'):
        for r in ((ins.get(key) or {}).get('rows') or []):
            pos[r.get('positions')] = [r.get('holders'), r.get('shares')]
    out['기관포지션_기관수·주식수'] = pos
    out['공매도_결제일·잔고·일평균거래량·커버일수'] = [
        [r.get('settlementDate'), r.get('interest'), r.get('avgDailyShareVolume'), r.get('daysToCover')]
        for r in ((sh.get('shortInterestTable') or {}).get('rows') or [])[:3]]
    for key, lab in (('numberOfTrades', '내부자거래건수'), ('numberOfSharesTraded', '내부자거래주식수')):
        out[lab + '_3개월·12개월'] = {r.get('insiderTrade'): [r.get('months3'), r.get('months12')]
                                    for r in ((it.get(key) or {}).get('rows') or [])}
    tt = it.get('transactionTable') or {}
    out['내부자최근_이름·관계·날짜·유형·주식수·가격'] = [
        [r.get('insider'), r.get('relation'), r.get('lastDate'), r.get('transactionType'), r.get('sharesTraded'), r.get('lastPrice')]
        for r in (((tt.get('table') or {}).get('rows')) or tt.get('rows') or [])[:6]]
    return out


SEC_SKIP = {'3', '4', '5', '144', '3/A', '4/A', '5/A', '144/A'}


def rss_titles(url, ok, k=8):
    s = fetch(url) or ''
    out, seen = [], set()
    for it in re.findall(r'(?s)<item>(.*?)</item>', s):
        tm = re.search(r'(?s)<title>(.*?)</title>', it)
        dm = re.search(r'<pubDate>(.*?)</pubDate>', it)
        title = strip_tags(html.unescape(tm.group(1))) if tm else ''
        if not ok(title):
            continue
        try:
            dt = datetime.strptime(dm.group(1)[:25], '%a, %d %b %Y %H:%M:%S').replace(tzinfo=timezone.utc).astimezone(KST)
        except (AttributeError, ValueError):
            continue
        key = re.sub(r'\s+-\s+[^-]+$', '', title)
        if key in seen:
            continue
        seen.add(key)
        out.append((dt, title))
    out.sort(key=lambda x: x[0], reverse=True)
    return ['%s %s' % (dt.strftime('%m-%d %H:%M'), t) for dt, t in out[:k]]


GENERIC = {'Advanced', 'American', 'Applied', 'United', 'General', 'First', 'International', 'Global', 'National', 'The',
           'Taiwan', 'Texas', 'Southern', 'Western', 'Eastern', 'Northern', 'Bank', 'Royal', 'Marvell', 'Palo'}


def gnews_en(tk, name):
    w = clean_name(name).split()
    key = ' '.join(w[:2]) if w and w[0] in GENERIC else (w[0] if w else tk)
    url = ('https://news.google.com/rss/search?q=%s&hl=en-US&gl=US&ceid=US:en'
           % urllib.parse.quote('"%s" OR %s when:14d' % (key, tk)))
    return rss_titles(url, lambda t: bool(re.search(r'\b%s\b' % re.escape(tk), t)) or key.lower() in t.lower())


def us_bundle(tk):
    tk = tk.strip().upper()
    paths = {'info': '/quote/%s/info?assetclass=stocks', 'summ': '/quote/%s/summary?assetclass=stocks',
             'finq': '/company/%s/financials?frequency=2', 'finy': '/company/%s/financials?frequency=1',
             'sur': '/company/%s/earnings-surprise', 'fore': '/analyst/%s/earnings-forecast', 'tgt': '/analyst/%s/targetprice',
             'ins': '/company/%s/institutional-holdings?limit=5&type=TOTAL', 'sh': '/quote/%s/short-interest?assetClass=stocks',
             'it': '/company/%s/insider-trades?limit=6&type=ALL', 'sec': '/company/%s/sec-filings?limit=40&sortColumn=filed&sortOrder=desc'}
    f = {k: EX.submit(nq, v % tk) for k, v in paths.items()}
    fy = EX.submit(ychart, tk.replace('.', '-'), '2y')     # 야후는 클래스 주식을 BRK-B처럼 하이픈으로 받는다(지수 심볼은 그대로)
    ff = EX.submit(jget, '%s/report-data/%s.json' % (RAW, tk), None, True)
    info = f['info'].result() or {}
    fn = EX.submit(gnews_en, tk, info.get('companyName') or tk)
    out = ['', '==== %s %s ====' % (tk, clean_name(info.get('companyName')))]
    out.append('#시세 ' + (J(us_quote(info, f['summ'].result())) if info else '조회 실패'))
    y = fy.result()
    if y:
        ev = y[1]
        spl = sorted([datetime.fromtimestamp(int(v['date']), timezone.utc).strftime('%Y-%m-%d'), v.get('splitRatio')]
                     for v in (ev.get('splits') or {}).values())
        div = sorted([datetime.fromtimestamp(int(v['date']), timezone.utc).strftime('%Y-%m-%d'), v.get('amount')]
                     for v in (ev.get('dividends') or {}).values())[-4:]
        out.append('#가격 야후 일봉 2년으로 계산 · 오늘 등락은 #시세만 ' + J(price_block({'data': y[0]}, nd=2)))
        out.append('#분할·배당 ' + J({'분할': spl or '없음', '최근배당': div or '없음'}))
    fo = ff.result()
    if fo:
        out.append('#수급파일 kfilter report-data(미국 — 국내와 구조가 다르다) ' + J(fo))
    out.append('#재무 분기 · 단위 천 달러 ' + J(fin_tables(f['finq'].result())))
    out.append('#연간손익 · 단위 천 달러 ' + J(fin_tables(f['finy'].result(), only=('손익',))))
    out.append('#실적·컨센·목표가 ' + J(us_earn(f['sur'].result(), f['fore'].result(), f['tgt'].result())))
    out.append('#기관·공매도·내부자 ' + J(us_flow(f['ins'].result(), f['sh'].result(), f['it'].result())))
    sec = [r for r in ((f['sec'].result() or {}).get('rows') or []) if (r.get('formType') or '') not in SEC_SKIP][:15]
    out.append('#SEC 최근 공시 %d건 · 제출일|양식|기간 (내부자 소유 보고 Form 3·4·5·144는 뺐다)' % len(sec))
    out.extend('%s|%s|%s' % (r.get('filed'), r.get('formType'), r.get('period')) for r in sec)
    news = fn.result()
    out.append('#뉴스 최근 14일 · 영문 제목 · %d건' % len(news))
    out.extend(news)
    return out


def safe_us(tk):
    try:
        return us_bundle(tk)
    except Exception as e:
        ERR.append('%s 처리 실패 — %s: %s' % (tk, type(e).__name__, str(e)[:80]))
        return ['', '==== %s ====' % tk, '#처리 실패 — #오류 참조']


# ───────────────────────────── 목차·속독층 (책 읽기: 목차로 1차 이해 → 속독으로 2차 이해 → 원문은 발췌 정독)
def _md(d):
    d = str(d or '')[:10].replace('.', '-')
    if len(d) == 8 and d.isdigit():
        d = '%s-%s-%s' % (d[:4], d[4:6], d[6:])
    try:
        return '%d월 %d일' % (int(d[5:7]), int(d[8:10]))
    except ValueError:
        return d


def _sd(d):
    d = str(d or '')[:10]
    try:
        return '%d/%d' % (int(d[5:7]), int(d[8:10]))
    except ValueError:
        return d


def _c(n):
    return '{:,}'.format(int(round(n))) if isinstance(n, (int, float)) else str(n)


def _pct(v, nd=1, sign=True):
    return ('%+.*f%%' if sign else '%.*f%%') % (nd, v) if isinstance(v, (int, float)) else '확인 불가'


def _chg(cur, prev):
    """두 값의 변화를 말로 — 흑자·적자 전환은 %로 쓰지 않는다"""
    if cur is None or prev is None:
        return '비교 불가'
    if prev <= 0 < cur:
        return '흑자전환'
    if cur <= 0 < prev:
        return '적자전환'
    if cur < 0 and prev < 0:
        return '적자 지속(폭 %s)' % ('축소' if cur > prev else '확대')
    return _pct((cur / prev - 1) * 100, 0) if prev else '비교 불가'


def _streak(rows, col):
    """최근 같은 부호가 이어진 일수와 그 첫날 — 방향이 꺾인 날"""
    if not rows:
        return None
    v0 = rows[0][col]
    if not v0:
        return None
    n = 0
    for r in rows:
        if r[col] and (r[col] > 0) == (v0 > 0):
            n += 1
        else:
            break
    return ('순매수' if v0 > 0 else '순매도'), n, rows[n - 1][0]


DOC_CAT = [('지분', r'대량보유|최대주주|특수관계|소유상황|소유주식|주요주주|임원'),
           ('자금조달', r'전환사채|신주인수권|교환사채|유상증자|무상증자|감자|자기주식|주식소각'),
           ('실적', r'잠정|손익구조|파생상품|손실발생'), ('계약', r'공급계약|판매ㆍ공급|수주'),
           ('소송·제재', r'소송|불성실|횡령|배임|제재'), ('시장조치', r'투자경고|투자주의|투자위험|거래정지|관리종목|단기과열|공매도')]


def _cat(title):
    return next((c for c, rx in DOC_CAT if re.search(rx, title)), '기타')


def digest_kr(x, mk):
    q, f, name = x.get('q') or {}, x.get('file'), x.get('name')
    L = []
    price = q.get('tradePrice')
    st = q.get('marketStatus')
    L.append('기준: %s %s · 현재가 %s원(%s)' % (_md(q.get('tradeDate')), STATUS.get(st, '장 상태 확인 불가').split(' — ')[0],
                                         _c(price), _pct(signed_rate(q.get('changeRate'), q.get('change')), 2)))
    # 시장 대비 — 20일 기준 판정은 모든 종목에 같은 기준
    m = (mk or {}).get(q.get('market') or '') or {}
    s5 = s20 = None
    if f:
        rows = ((f.get('supply_detail') or {}).get('recent_daily') or {}).get('rows') or []
        closes = [r[1] for r in rows]
        if closes and price:
            closes[0] = price
            s5 = (closes[0] / closes[5] - 1) * 100 if len(closes) > 5 else None
            s20 = (closes[0] / closes[20] - 1) * 100 if len(closes) > 20 else None
    elif x.get('pb'):
        s5, s20 = x['pb'].get('수익률5일%'), x['pb'].get('수익률20일%')
    i20 = m.get('20일%')
    if isinstance(s20, (int, float)) and isinstance(i20, (int, float)):
        gap = s20 - i20
        if i20 <= -2 and s20 >= 2:
            v = '시장이 빠질 때 올랐다 — 자기 재료가 있다'
        elif i20 >= 2 and s20 <= -2:
            v = '시장이 오를 때 혼자 빠졌다 — 이 종목만의 사유가 있다'
        elif abs(gap) < 3:
            v = '시장과 같이 움직였다'
        else:
            v = '시장보다 %s' % ('강했다' if gap > 0 else '약했다')
        line = '시장 대비: %s 20일 %s · 이 종목 20일 %s → %s' % (q.get('market'), _pct(i20), _pct(s20), v)
        ir = (f or {}).get('idxRel') or {}
        if ir.get('m6Excess') is not None:
            line += ' · 지수 대비 초과 1개월 %s%%p · 6개월 %s%%p' % (('%+.1f' % ir['m1Excess']), ('%+.1f' % ir['m6Excess']))
        L.append(line)
    else:
        L.append('시장 대비: 확인 불가')
    # 가격 위치
    parts = []
    if f and f.get('price_daily'):
        vs = ((f['price_daily'].get('ma') or {}).get('vs')) or {}
        for k, lab in (('ma20', '20일선'), ('ma60', '60일선'), ('ma120', '120일선')):
            if vs.get(k) is not None:
                parts.append('%s보다 %s' % (lab, ('%.0f%% 위' % vs[k]) if vs[k] >= 0 else ('%.0f%% 아래' % -vs[k])))
        ir = f.get('idxRel') or {}
        if ir.get('peakClose'):
            parts.append('고점(%s 종가 %s원) 대비 %s' % (_md(ir.get('peakDate')), _c(ir['peakClose']), _pct(ir.get('ddFromPeak'), 0)))
    elif x.get('pb'):
        pb = x['pb']
        for k, lab in (('이평20_이격%', '20일선'), ('이평60_이격%', '60일선'), ('이평120_이격%', '120일선')):
            if pb.get(k):
                g = pb[k][1]
                parts.append('%s보다 %s' % (lab, ('%.0f%% 위' % g) if g >= 0 else ('%.0f%% 아래' % -g)))
        if pb.get('고점대비%') is not None:
            parts.append('1년 종가 고점 대비 %s' % _pct(pb['고점대비%'], 0))
    hi, lo = q.get('high52wPrice'), q.get('low52wPrice')
    if price and hi and lo and hi > lo:
        parts.append('52주 최고·최저 사이 %d%% 지점' % round((price - lo) / (hi - lo) * 100))
    L.append('가격 위치: ' + (' · '.join(parts) if parts else '확인 불가'))
    # 거래량
    vsx = (f or {}).get('volume_stats') or {}
    if vsx.get('ratio_20_60') is not None:
        L.append('거래량: 최근 20일 평균이 60일 평균의 %.2f배 · 오늘은 60일 평균의 %.1f배%s' % (
            vsx['ratio_20_60'], vsx.get('today_vs_60') or 0, ' (장중이라 오늘 값은 덜 찼다)' if STATUS.get(st, '').startswith('장중') else ''))
    elif x.get('pb') and x['pb'].get('거래량배수_20대60·당일대60'):
        a, b = x['pb']['거래량배수_20대60·당일대60']
        L.append('거래량: 최근 20일 평균이 60일 평균의 %.2f배 · 오늘은 60일 평균의 %.1f배' % (a, b))
    # 수급
    listed = q.get('listedShareCount')
    if f and f.get('supply_detail'):
        sd, sdv = f['supply_detail'], f.get('supply_derived') or {}
        pat = sdv.get('pattern') or {}
        seg = ['%s %s' % (k, pat[k]) for k in ('외국인', '기관계', '연기금', '개인') if pat.get(k)]
        c20 = sd.get('cum_20d') or {}
        rd = sd.get('recent_daily') or {}
        cols, rows = rd.get('cols') or [], rd.get('rows') or []
        extra = []
        for who in ('외국인', '기관계'):
            if who in c20:
                extra.append('%s 20일 %s %s주' % (who, '순매수' if c20[who] >= 0 else '순매도', _c(abs(c20[who]))))
            if who in cols:
                ci = cols.index(who)
                sk = _streak(rows, ci)
                if sk and sk[1] == 1:
                    if len(rows) > 1 and rows[1][ci]:
                        extra.append('%s %s %s로 돌아섰다' % (who, _md(sk[2]), sk[0]))
                elif sk:
                    extra.append('%s 최근 %d거래일 연속 %s(%s부터)' % (who, sk[1], sk[0], _md(sk[2])))
        fa = sdv.get('foreign_avg_price')
        if fa and price:
            g = (price / fa - 1) * 100
            extra.append('외국인 평균 매수단가 %s원 — 현재가가 %s' % (_c(fa), ('%.0f%% 위' % g) if g >= 0 else ('%.0f%% 아래' % -g)))
        L.append('수급(11개 주체, 수급파일 %s 기준): %s · %s' % (_sd(f.get('updated')), ' · '.join(seg) or '판정 없음', ' · '.join(extra)))
        if 'foreignRatio' in cols and '외국인' in cols and len(rows) > 20 and listed:
            fr = cols.index('foreignRatio')
            dshare = (rows[0][fr] - rows[20][fr]) / 100 * listed
            onm = sum(r[cols.index('외국인')] for r in rows[:20])
            if abs(dshare - onm) > max(0.15 * max(abs(dshare), abs(onm)), 0.001 * listed):
                L.append('지분율·순매수 어긋남: 외국인 지분율이 20거래일간 %+.2f%%p(약 %s주) 변했는데 장내 순매수 합은 %s주 — 차이 약 %s주는 시간외·블록딜 등 장외 이동 가능, 사유 확인 불가' % (
                    rows[0][fr] - rows[20][fr], _c(dshare), _c(onm), _c(dshare - onm)))
    elif x.get('sb'):
        sb = x['sb']
        fv, iv = sb.get('외국인순매수주_5·20·60일') or [], sb.get('기관순매수주_5·20·60일') or []
        seg = []
        if len(fv) > 1:
            seg.append('외국인 20일 %s %s주' % ('순매수' if fv[1] >= 0 else '순매도', _c(abs(fv[1]))))
        if len(iv) > 1:
            seg.append('기관 20일 %s %s주' % ('순매수' if iv[1] >= 0 else '순매도', _c(abs(iv[1]))))
        L.append('수급(장내 외국인·기관만, 개인·연기금 없음): ' + (' · '.join(seg) or '확인 불가'))
        fr = sb.get('외국인지분율%_오늘·20일전·60일전') or []
        if len(fr) > 1 and None not in fr[:2] and listed and len(fv) > 1:
            dshare = (fr[0] - fr[1]) / 100 * listed
            if abs(dshare - fv[1]) > max(0.15 * max(abs(dshare), abs(fv[1])), 0.001 * listed):
                L.append('지분율·순매수 어긋남: 외국인 지분율 20거래일 %+.2f%%p(약 %s주) vs 장내 순매수 %s주 — 차이 약 %s주는 시간외·블록딜 등 장외 이동 가능, 사유 확인 불가' % (
                    fr[0] - fr[1], _c(dshare), _c(fv[1]), _c(dshare - fv[1])))
    else:
        L.append('수급: 확인 불가')
    # 실적 — 다음 확정 분기(연결)
    Q = ((x.get('fin') or {}).get('data') or {}).get('QUARTER') or []
    if Q:
        q0 = Q[0]
        q1 = Q[1] if len(Q) > 1 else {}
        yoy = next((r for r in Q[1:] if (r.get('date') or '')[:7] == '%d%s' % (int(q0['date'][:4]) - 1, q0['date'][4:7])), None)
        def one(k, lab):
            v = q0.get(k)
            t = '%s %s억' % (lab, _c((v or 0) / 1e8)) if v is not None else '%s 확인 불가' % lab
            t += '(전분기 %s' % _chg(v, q1.get(k))
            t += ', 전년 동기 %s)' % _chg(v, yoy.get(k)) if yoy else ')'
            return t
        fin_co = bool(re.search(r'은행|보험|증권|금융|카드|캐피탈', q.get('wicsSectorName') or ''))
        L.append('실적(확정, %s년 %d월 분기): %s · %s · %s' % (q0['date'][:4], int(q0['date'][5:7]), one('sales', '영업수익' if fin_co else '매출'),
                                                    one('operatingProfit', '영업이익'), one('netIncome', '순이익')))
        flags = []
        op, ni, sa = q0.get('operatingProfit'), q0.get('netIncome'), q0.get('sales')
        if op is not None and ni is not None and op > 0 > ni:
            flags.append('영업이익은 흑자인데 순손실 — 영업외 손익이 갈랐다')
        if q1 and sa and q1.get('sales') and op and q1.get('operatingProfit') and op > 0 and q1['operatingProfit'] > 0:
            if sa > q1['sales'] * 1.05 and op < q1['operatingProfit'] * 0.95:
                flags.append('매출은 늘었는데 영업이익이 줄었다')
        if q1 and op and ni and q1.get('operatingProfit') and q1.get('netIncome') and min(op, ni, q1['operatingProfit'], q1['netIncome']) > 0:
            if op > q1['operatingProfit'] * 1.05 and ni < q1['netIncome'] * 0.95:
                flags.append('영업이익은 늘었는데 순이익이 줄었다')
        if flags:
            L.append('손익 어긋남: ' + ' · '.join(flags))
    else:
        L.append('실적: 확인 불가')
    pre = next((it for it in x.get('items') or [] if '잠정' in it['title']), None)
    if pre:
        L.append('잠정실적 공시: %s%s' % (_md(pre['date']), ' — 확정 재무보다 새로우니 공시 본문으로 본다' if Q and pre['date'][:7] > (Q[0]['date'][:4] + '-' + '%02d' % min(12, int(Q[0]['date'][5:7]) + 1)) else ''))
    # 밸류
    eps = [r.get('eps') for r in Q[:4]]
    vparts = []
    if len(eps) == 4 and None not in eps and price:
        ttm = sum(eps)
        vparts.append(('최근 4분기 PER %.1f배' % (price / ttm)) if ttm > 0 else ('최근 4분기 EPS 합 %s원(적자)이라 PER 없음' % _c(ttm)))
        a2 = (eps[0] + eps[1]) * 2
        vparts.append(('최근 2분기 연환산 PER %.1f배' % (price / a2)) if a2 > 0 else '최근 2분기 연환산도 적자')
        if ttm <= 0 < eps[0]:
            vparts.append('최근 분기는 흑자, 4분기 합은 적자')
    sp = q.get('sectorPer')
    if isinstance(sp, (int, float)):
        vparts.append(('업종 PER %.1f배' % sp) if 0 < sp <= 200 else ('업종 PER %.1f배 — 비교 무효' % sp))
    vd = (f or {}).get('valuation_derived') or {}
    if vd.get('verdict'):
        vparts.append('PBR 판정: %s%s' % (vd['verdict'], ' (ROE 상한 적용 %s%%)' % vd.get('roe_used_pct') if vd.get('roe_capped') else ''))
    L.append('밸류: ' + (' · '.join(vparts) if vparts else '확인 불가'))
    # 목표를 부른 뒤에만 볼 줄 둘
    con = (f or {}).get('consensus') or {}
    try:
        tgt = float(str(con.get('priceTargetMean')).replace(',', ''))
    except ValueError:
        tgt = None
    if tgt and price:
        L.append('컨센(목표를 부른 뒤에만 본다): 목표가 평균 %s원 — 현재가 대비 %s · 투자의견 %s (%s)' % (
            _c(tgt), _pct((tgt / price - 1) * 100), con.get('recommMean'), _md(con.get('createDate'))))
    tc = (f or {}).get('target_context') or {}
    if tc.get('n'):
        L.append('과거 상승폭(목표를 부른 뒤에만 본다): %s거래일 창 %s개 표본 — 중앙 %s · 상위 25%% %s · 상위 10%% %s · 최대 %s' % (
            tc.get('holdDays'), tc.get('n'), _pct(tc.get('median')), _pct(tc.get('p75')), _pct(tc.get('p90')), _pct(tc.get('max'))))
    # 직전 판단
    pt = (f or {}).get('prev_track') or {}
    if pt.get('prevDate'):
        path = pt.get('path') or {}
        cur, mx = path.get('curReached', pt.get('reachedPct')), path.get('maxReached')
        if path.get('hitTarget'):
            w = '목표를 %s거래일 만에 찍었고 지금은 목표폭의 %.0f%% 지점 — 방향은 맞았다' % (path.get('daysToTarget'), cur or 0)
        elif isinstance(cur, (int, float)) and cur >= 100:
            w = '목표를 넘어섰다'
        elif isinstance(cur, (int, float)) and cur < 0:
            w = '반대로 갔다' + ('' if cur < -100 else '(목표폭의 %.0f%%)' % cur)
        elif isinstance(cur, (int, float)):
            w = '목표폭의 %.0f%% 지점%s' % (cur, '(장중 최대 %.0f%%)' % mx if isinstance(mx, (int, float)) else '')
        else:
            w = '진척 확인 불가'
        try:
            left = (datetime.strptime(pt['horizon'][:10], '%Y-%m-%d').date() - NOW.date()).days
            lt = ('판정일 %s(%d일 남음)' % (_md(pt['horizon']), left)) if left >= 0 else ('판정일 %s 지남' % _md(pt['horizon']))
        except (KeyError, ValueError, TypeError):
            lt = '판정일 확인 불가'
        L.append('직전 판단: %s %s · 목표 %s원(당시 %s원) · %s · %s · 판정 %s · 그때 근거 "%s"' % (
            _md(pt['prevDate']), pt.get('prevGrade'), _c(pt.get('prevTarget')), _c(pt.get('prevPrice')), lt, w,
            path.get('verdict') or '-', pt.get('prevThesis')))
    else:
        L.append('직전 판단: 없음')
    # 변화 신호 — 최근 30일 공시
    cut = (NOW - timedelta(days=30)).strftime('%Y-%m-%d')
    got = {}
    for it in x.get('items') or []:
        if it['date'] >= cut:
            c = _cat(it['title'])
            if c != '기타':
                got.setdefault(c, []).append(_sd(it['date']))
    sig = ['%s %d건(%s)' % (c, len(v), ', '.join(sorted(set(v), key=lambda d: v.index(d))[:3])) for c, v in got.items()]
    warn = (q.get('stockState') or {}).get('marketWarning')
    if warn and warn != 'NONE':
        sig.append('시장경보 표시(%s)' % warn)
    far = ['%s %s' % (_sd(it['date']), '손익구조 변경' if '손익구조' in it['title'] else '파생상품 손실')
           for it in x.get('items') or [] if re.search(r'손익구조|파생상품거래손실', it['title'].replace(' ', ''))]
    L.append('변화 신호(최근 30일 공시): ' + (' · '.join(sig) if sig else '주요 공시 없음') +
             (' · 180일 안 손익구조 변경·파생손실 공시: ' + ', '.join(far[:4]) if far else ''))
    spl = (f or {}).get('splits')
    if spl:
        L.append('권리락: 있음 — 파일 안 주가는 보정됐고 공시·뉴스 속 과거 주가는 보정 전이다 · ' + J(spl)[:160])
    gaps = []
    if not f:
        gaps.append('수급파일 없음(11개 주체 수급·컨센·직전 판단 없음)')
    if not str(x.get('src', '')).startswith('DART'):
        gaps.append('DART 목록 실패(거래소 공시만)')
    if not x.get('rep'):
        gaps.append('정기보고서 없음')
    if ERR:
        gaps.append('조회 실패 %d건(#오류)' % len(ERR))
    L.append('빈칸: ' + (' · '.join(gaps) if gaps else '없음'))
    return L


def toc_kr(x):
    docs = x.get('docs') or []
    cnt = {}
    for it in docs:
        c = _cat(it['title'])
        cnt[c] = cnt.get(c, 0) + 1
    return ('#목차 ①속독 %d줄 ②시장·해외 ③시세 ④수급파일 %s ⑤공시목록 %d건 ⑥공시본문 %d건(%s) ⑦정기보고서 %s ⑧뉴스 %d건 ⑨오류 %d건'
            % (x.get('digest_n', 0), ('%s자(키 지도 포함)' % _c(x.get('file_chars', 0))) if x.get('file') else '없음 — 가격·수급·재무확정으로 대신',
               len(x.get('items') or []), len(docs), ' · '.join('%s %d' % kv for kv in cnt.items()) or '없음',
               ('%s 절 %d개' % (x['rep']['title'], len(x.get('secs') or []))) if x.get('rep') else '없음',
               len(x.get('news') or []), len(ERR)))


def safe_bundle(code, deep):
    try:
        return kr_bundle(code, deep)
    except Exception as e:      # 한 종목이 깨져도 나머지는 낸다
        ERR.append('%s 처리 실패 — %s: %s' % (code, type(e).__name__, str(e)[:80]))
        return ['', '==== %s ====' % code, '#처리 실패 — #오류 참조'], {}


def main(argv):
    if len(argv) < 2 or argv[0] not in ('kr', 'us'):
        print(__doc__)
        return 1
    mode, codes = argv[0], argv[1:]
    deep = len(codes) == 1
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as top:
        if mode == 'kr':
            fm = top.submit(market_all)
            parts, ctxs = zip(*top.map(lambda c: safe_bundle(c, deep), codes))
        else:
            fm = top.submit(us_market_all)
            parts, ctxs = list(top.map(safe_us, codes)), []
        mk, gl = fm.result()
    head = '깊게' if deep else '얕게 %d종목' % len(codes)
    print('#번들 kfilter %s · 수집 %s KST · %s · 이 출력이 자료의 전부다'
          % ('국장' if mode == 'kr' else '미장', NOW.strftime('%Y-%m-%d %H:%M'), head))
    if mode == 'kr' and deep and ctxs and ctxs[0]:
        try:
            dg = digest_kr(ctxs[0], mk)
            ctxs[0]['digest_n'] = len(dg)
            print(toc_kr(ctxs[0]))
            print('#속독 — 코드가 원자료에서 계산했다. 판단의 출발점이고, 원문은 이 줄들을 뒤집을 신호를 확인할 때만 연다')
            print('\n'.join(dg))
        except Exception as e:
            ERR.append('속독층 계산 실패 — %s: %s' % (type(e).__name__, str(e)[:100]))
            print('#속독 계산 실패 — 원문 블록으로 판단한다')
    if mode == 'kr':
        print('#시장 ' + J(mk))
        print('#해외·환율 ' + J(gl))
    else:
        print('#미국시장 ' + J(mk))
        print('#환율·유가 ' + J(gl))
    for part in parts:
        print('\n'.join(part))
    print('#오류 ' + (J(ERR) if ERR else '없음'))
    print('#수집시간 %.1f초' % (time.time() - t0))
    sys.stdout.flush()
    os._exit(0)          # 마감으로 버린 조회가 종료를 붙잡지 않게

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
