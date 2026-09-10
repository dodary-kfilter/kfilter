#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kfilter 번들 — 리포트 자료를 명령 한 번으로 받는다.

리포트 방은 이 명령 하나만 실행하고, 출력만 보고 판단한다.
  국장 1종목(깊게)    : curl -s https://raw.githubusercontent.com/dodary-kfilter/kfilter/main/tools/bundle.py | python3 - kr 440110
  국장 여러 종목(얕게): ... | python3 - kr 005930 000660 042700

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
EX = ThreadPoolExecutor(max_workers=16)      # 개별 조회
DART_GATE = threading.BoundedSemaphore(5)    # DART 동시 접속 상한


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


def fetch(url, ref=None, form=None, timeout=25, tries=2, quiet404=False):
    hd = {'User-Agent': UA}
    if ref:
        hd['Referer'] = ref
    data = urllib.parse.urlencode(form).encode() if form else None
    gate = DART_GATE if url.startswith(DART) else None
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
            time.sleep(0.8 * (i + 1))
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
def price_block(d):
    rows = [r for r in ((d or {}).get('data') or []) if r.get('accTradeVolume')]  # 장전 더미행(거래량 0) 제거
    if len(rows) < 5:
        return None
    c = [r['tradePrice'] for r in rows]
    v = [r['accTradeVolume'] for r in rows]
    out = {'기준일': (rows[0].get('date') or '')[:10], '종가': n(c[0])}
    for k in (5, 20, 60, 120):
        if len(c) >= k:
            m = sum(c[:k]) / k
            out['이평%d_이격%%' % k] = [round(m), round((c[0] / m - 1) * 100, 1)]
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
            out['매물대120일_하단·상단·비중%'] = [[round(lo2 + i * w), round(lo2 + (i + 1) * w),
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
        cells = [strip_tags(c) for c in re.findall(r'(?s)<td[^>]*>(.*?)</td>', tr)]
        if len(cells) < 5:
            continue
        corp = re.sub(r'^[유코넥기]\s+', '', cells[1]).replace(' ', '')
        key = (cells[4], cells[2])
        if key in seen:
            continue
        seen.add(key)
        rows.append((corp, {'rcp': m.group(1), 'date': cells[4].replace('.', '-'),
                            'title': re.sub(r'\s+', ' ', cells[2]), 'by': cells[3]}))
    if rows:
        names = [c for c, _ in rows]
        main = max(set(names), key=names.count)
        return [it for c, it in rows if c == main], 'DART(%s)' % main
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
    ('매출·수주', [r'매출\s*및\s*수주'], 1800, None),
    ('재무상태표(발췌)', [r'연결\s*재무상태표', r'^\s*\d-\d\.\s*재무상태표'], 1000,
     r'단위|현금|재고|차입|사채|전환|파생|총계|잉여금|결손|자본금'),
    ('손익계산서(발췌)', [r'연결\s*포괄손익계산서', r'연결\s*손익계산서', r'^\s*\d-\d\.\s*포괄손익계산서', r'^\s*\d-\d\.\s*손익계산서'], 900,
     r'단위|매출|영업이익|영업손실|금융수익|금융비용|금융원가|파생|법인세|당기순|반기순|분기순|주당'),
    ('기타 재무', [r'기타\s*재무에\s*관한'], 900, None),
    ('주주', [r'주주에\s*관한\s*사항'], 1000, None),
    ('우발부채·소송', [r'우발부채'], 900, None),
    ('작성기준일 이후', [r'작성기준일\s*이후'], 1000, None),
]


def section_text(rcp, nd, k, rowf):
    t = dart_view(rcp, nd)
    if rowf:
        lines = t.split('\n')
        t = '\n'.join(lines[:2] + [ln for ln in lines[2:] if re.search(rowf, ln)])
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
    return rep, [(label, title, fu.result()) for label, title, fu in jobs]


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
    out.append('#시세 ' + (J(quote_block(q)) if q else '조회 실패'))
    f = ff.result()
    if f:
        out.append('#수급파일 kfilter report-data · 갱신 %s · 스캔 시점 값(실시간 아님)' % f.get('updated'))
        if deep:
            out.append(FILE_MAP)
        out.append(J(slim_file(f, name, deep)))
    else:
        fd = EX.submit(daum, '/api/quote/A%s/days?perPage=250&page=1' % code)
        fi = EX.submit(daum, '/api/investor/days?symbolCode=A%s&perPage=60&page=1' % code)
        fn = EX.submit(daum, '/api/quote/A%s/financials' % code)
        out.append('#수급파일 없음 — 아래 셋은 다음에서 직접 계산했다')
        out.append('#가격 ' + J(price_block(fd.result())))
        out.append('#수급 ' + J(supply_block(fi.result())))
        out.append('#재무확정 ' + J(fin_block(fn.result())))
    items, src = fl.result()
    shown = items if deep else items[:10]
    out.append('#공시목록 출처 %s · 최근 180일 · 최신순 · 날짜|제목|제출인 (%d건 중 %d건)'
               % (src, len(items), min(len(shown), 60)))
    out.extend('%s|%s|%s' % (it['date'], it['title'], it['by']) for it in shown[:60])
    if deep:
        docs = pick_docs(items)
        futs = [(it, EX.submit(doc_text, it)) for it in docs]
        fp = EX.submit(periodic, items)
        out.append('#공시본문 주요공시 %d건 · 건당 앞부분만' % len(docs))
        for it, fu in futs:
            out.append('▶ %s %s · %s' % (it['date'], it['title'], it['by']))
            out.append(fu.result() or '(본문 없음)')
        rep, secs = fp.result()
        if rep:
            out.append('#정기보고서 %s · 접수 %s · 절별 발췌' % (rep['title'], rep['date']))
            for label, title, txt in secs:
                out.append('▷ %s — %s' % (label, title))
                out.append(txt or '(내용 없음)')
        else:
            out.append('#정기보고서 최근 180일 안에 없음')
        news = fg.result()
        out.append('#뉴스 최근 14일 · 제목에 종목명 있는 것만 · %d건' % len(news))
        out.extend(news)
    return out


def main(argv):
    if len(argv) < 2 or argv[0] != 'kr':
        print(__doc__)
        return 1
    codes = argv[1:]
    deep = len(codes) == 1
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=5) as top:
        fm = top.submit(market_all)
        parts = list(top.map(lambda c: kr_bundle(c, deep), codes))
        mk, gl = fm.result()
    print('#번들 kfilter · 수집 %s KST · %s · 이 출력이 자료의 전부다'
          % (NOW.strftime('%Y-%m-%d %H:%M'), '깊게' if deep else '얕게 %d종목' % len(codes)))
    print('#시장 ' + J(mk))
    print('#해외·환율 ' + J(gl))
    for p in parts:
        print('\n'.join(p))
    print('#오류 ' + (J(ERR) if ERR else '없음'))
    print('#수집시간 %.1f초' % (time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
