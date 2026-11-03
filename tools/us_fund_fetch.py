# -*- coding: utf-8 -*-
"""미장 기업 재료 — S&P 500·나스닥 100 기업의 재무·추세·공시 사건 (깔때기 4단계 재료, 2026-11-03)

사용: python3 tools/us_fund_fetch.py [--limit N]   → lists/us_fund.json, lists/events.json의 us (3~4분)
범위: lists/mcap.json의 sp500 + ndx (겹침 빼고 520개 안팎)
자료
  SEC companyfacts(공식 재무, XBRL): 분기 매출·영업이익·순이익을 모아 최근 4분기 합(TTM)과 1년 전 대비 성장, 영업이익률,
                                    현금·장기부채 → 기업가치(EV = 시총 + 부채 − 현금)와 영업이익 대비 기업가치(EV/EBIT)
  SEC submissions(공시 목록): 최근 3일의 8-K(항목 번호로 사건 종류), 425(합병 관련 공시), S-4·DEFM14A(합병 서류), SC TO-T(공개매수)
  야후 일봉 1년: 1·3·6개월 수익률, 1년 최고가 대비, 50·200일선 대비, 변동성
SEC는 초당 10건 제한이라 6갈래로 나눠 천천히 부른다. 이름에 연락처를 넣으라는 SEC 규칙에 따라 User-Agent에 주소를 둔다.
"""
import json, os, sys, time, math, datetime as dt, urllib.request, urllib.error, threading
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
KST = dt.timezone(dt.timedelta(hours=9))
SEC_UA = 'kfilter research dodary.kfilter@gmail.com'
YH_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36'
ITEMS = {'1.01': '중요 계약 체결', '1.02': '중요 계약 종료', '1.03': '파산·회생', '2.01': '인수·매각 완료', '2.02': '실적 발표',
         '2.03': '부채 발생', '2.05': '구조조정', '2.06': '자산 손상', '3.01': '상장 유지 문제', '3.02': '지분 발행',
         '3.03': '주주 권리 변경', '4.01': '감사인 변경', '4.02': '재무제표 정정', '5.01': '지배권 변경', '5.02': '경영진·이사 변경',
         '5.03': '정관 변경', '5.07': '주총 결과', '7.01': '공정 공시', '8.01': '기타 중요 사항', '9.01': '첨부 서류'}
FORMS = {'425': '합병 관련 공시', 'S-4': '합병 등록 서류', 'DEFM14A': '합병 주총 안내', 'PREM14A': '합병 주총 예비 안내',
         'SC TO-T': '공개매수(제3자)', 'SC 14D9': '공개매수 의견', 'SC 13D': '5% 이상 지분 신고(경영 참여)'}
_lock = threading.Lock(); _last = [0.0]


def sec_get(url):
    for i in range(4):
        with _lock:                                   # SEC 초당 10건 제한 — 0.12초 간격
            w = 0.12 - (time.time() - _last[0])
            if w > 0: time.sleep(w)
            _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': SEC_UA, 'Accept-Encoding': 'gzip'})
            r = urllib.request.urlopen(req, timeout=30); b = r.read()
            if r.headers.get('Content-Encoding') == 'gzip':
                import gzip; b = gzip.decompress(b)
            return json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def yh_closes(sym):
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d' % sym.replace('.', '-')
    for i in range(3):
        try:
            j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': YH_UA}), timeout=20).read())
            c = [x for x in j['chart']['result'][0]['indicators']['quote'][0]['close'] if x is not None]
            return c if len(c) > 30 else None
        except Exception:
            time.sleep(1 + i)
    return None


def pct(a, b):
    return round((a / b - 1) * 100, 1) if a and b else None


def trend(c):
    if not c: return {}
    last = c[-1]; ret = lambda n: pct(last, c[-n - 1]) if len(c) > n else None
    ma = lambda n: pct(last, sum(c[-n:]) / n) if len(c) >= n else None
    rs = [math.log(c[i] / c[i - 1]) for i in range(max(1, len(c) - 63), len(c)) if c[i - 1] > 0]
    vol = round(math.sqrt(sum((x - sum(rs) / len(rs)) ** 2 for x in rs) / (len(rs) - 1)) * math.sqrt(252) * 100, 1) if len(rs) > 10 else None
    return {'r1m': ret(21), 'r3m': ret(63), 'r6m': ret(126), 'hi52': pct(last, max(c)), 'ma50': ma(50), 'ma200': ma(200), 'vol3m': vol}


def days(a, b):
    return (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days


def quarters(units):
    """분기 값 목록(끝 날짜 → 값). 4분기는 1년 값 − 9개월 누적으로 채운다(10-K엔 4분기만 따로 없는 회사가 많다)."""
    q, ann, nine = {}, {}, {}
    for x in units:
        s, e, v = x.get('start'), x.get('end'), x.get('val')
        if not (s and e) or v is None: continue
        d = days(s, e)
        if 80 <= d <= 100: q[e] = v
        elif 350 <= d <= 380: ann[(s, e)] = v
        elif 260 <= d <= 285: nine[(s, e)] = v
    for (s, e), v in ann.items():
        if e in q: continue
        for (s9, e9), v9 in nine.items():
            if s9 == s and 80 <= days(e9, e) <= 100:
                q[e] = v - v9; break
    return dict(sorted(q.items()))


def first_tag(g, tags):
    """후보 태그 중 '가장 최근 분기까지 이어진' 것을 고른다 — 회사가 중간에 태그를 바꾸면(예: 엔비디아) 옛 태그엔 몇 년 전 값만 남아 있다"""
    best, key = None, None
    for t in tags:
        u = (g.get(t) or {}).get('units', {}).get('USD')
        if not u: continue
        q = quarters(u)
        if not q: continue
        k = (max(q), len(q))                                    # 마지막 분기 끝 날짜, 그다음 분기 개수
        if key is None or k > key: best, key = u, k
    return best


def latest_instant(g, tags):
    best = None
    for t in tags:
        for x in (g.get(t) or {}).get('units', {}).get('USD', []) or []:
            if x.get('start'): continue
            if best is None or x['end'] > best[0]: best = (x['end'], x['val'])
    return best[1] if best else None


def fundamentals(cf, mcap):
    g = (cf or {}).get('facts', {}).get('us-gaap', {})
    if not g: return {}
    rev = first_tag(g, ['RevenueFromContractWithCustomerExcludingAssessedTax', 'Revenues', 'SalesRevenueNet',
                        'RevenueFromContractWithCustomerIncludingAssessedTax', 'RevenuesNetOfInterestExpense'])
    op = first_tag(g, ['OperatingIncomeLoss'])
    ni = first_tag(g, ['NetIncomeLoss', 'ProfitLoss'])
    out = {}
    def ttm(units, key):
        if not units: return
        q = quarters(units); vals = list(q.values()); ends = list(q.keys())
        if len(vals) >= 4:
            out[key + '_ttm'] = round(sum(vals[-4:]) / 1e6, 1)
            out['last_q'] = max(out.get('last_q', ''), ends[-1])
        if len(vals) >= 8 and sum(vals[-8:-4]):
            out[key + '_g'] = round((sum(vals[-4:]) / sum(vals[-8:-4]) - 1) * 100, 1) if sum(vals[-8:-4]) > 0 else None
        if len(vals) >= 5 and vals[-5]:
            out[key + '_q_yoy'] = round((vals[-1] / vals[-5] - 1) * 100, 1) if vals[-5] > 0 else None
        out[key + '_q'] = [round(v / 1e6, 1) for v in vals[-8:]]          # 최근 8분기(백만 달러) — 흐름을 Claude가 본다
    ttm(rev, 'rev'); ttm(op, 'op'); ttm(ni, 'ni')
    if out.get('rev_ttm') and out.get('op_ttm') is not None:
        out['op_margin'] = round(out['op_ttm'] / out['rev_ttm'] * 100, 1) if out['rev_ttm'] > 0 else None
    cash = latest_instant(g, ['CashAndCashEquivalentsAtCarryingValue', 'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents'])
    sti = latest_instant(g, ['ShortTermInvestments', 'MarketableSecuritiesCurrent', 'AvailableForSaleSecuritiesDebtSecuritiesCurrent'])
    debt = latest_instant(g, ['LongTermDebt']) or ((latest_instant(g, ['LongTermDebtNoncurrent']) or 0) + (latest_instant(g, ['LongTermDebtCurrent']) or 0)) or None
    out['cash'] = round(((cash or 0) + (sti or 0)) / 1e6, 1) if (cash or sti) else None
    out['debt'] = round(debt / 1e6, 1) if debt else None
    if mcap:
        ev = mcap / 1e6 + (out['debt'] or 0) - (out['cash'] or 0)
        out['ev'] = round(ev, 1)
        if out.get('op_ttm') and out['op_ttm'] > 0: out['ev_ebit'] = round(ev / out['op_ttm'], 1)
        if out.get('ni_ttm') and out['ni_ttm'] > 0: out['per'] = round(mcap / 1e6 / out['ni_ttm'], 1)
    return out


def events(sub, since):
    r = ((sub or {}).get('filings') or {}).get('recent') or {}
    out = []
    for i, f in enumerate(r.get('form', [])):
        d = r['filingDate'][i]
        if d < since: break                                        # 최신순이라 지나면 멈춤
        kind = None
        if f in ('8-K', '8-K/A'):
            its = [x for x in (r.get('items', [''] * (i + 1))[i] or '').split(',') if x and x != '9.01']
            if not its: continue
            kind = ' · '.join(ITEMS.get(x, x) for x in its)
        elif f in FORMS:
            kind = FORMS[f]
        if kind:
            out.append({'date': d, 'form': f, 'kind': kind, 'doc': r['primaryDocDescription'][i] or ''})
    seen, uniq = set(), []                                        # 합병 공시(425)는 하루에 수십 건 — 날짜·종류로 묶는다
    for e in out:
        k = (e['date'], e['kind'])
        if k in seen: continue
        seen.add(k); uniq.append(e)
    return uniq


def main(argv):
    t0 = time.time()
    lim = int(argv[argv.index('--limit') + 1]) if '--limit' in argv else None
    M = json.load(open(os.path.join(ROOT, 'lists', 'mcap.json'), encoding='utf-8'))
    uni = {}
    for key in ('sp500', 'ndx'):
        for x in M.get(key) or []:
            uni.setdefault(x['code'], {'name': x.get('name_ko') or x.get('name'), 'sector': x.get('sector'), 'industry': x.get('industry'),
                                       'mcap': x.get('mcap'), 'idx': []})['idx'].append(key)
    tick = list(uni)[:lim] if lim else list(uni)
    cikmap = {}
    for v in (sec_get('https://www.sec.gov/files/company_tickers.json') or {}).values():
        cikmap[v['ticker'].upper()] = str(v['cik_str']).zfill(10)
    since = (dt.datetime.now(KST).date() - dt.timedelta(days=3)).isoformat()

    def one(t):
        """기업 하나 — 원자료(재무 JSON은 회사마다 수 MB)는 여기서 바로 숫자로 줄이고 버린다(전부 들고 있으면 메모리가 넘친다)"""
        cik = cikmap.get(t.upper()) or cikmap.get(t.upper().replace('.', '-'))
        u = uni[t]
        f = fundamentals(sec_get('https://data.sec.gov/api/xbrl/companyfacts/CIK%s.json' % cik), u['mcap']) if cik else {}
        ev = events(sec_get('https://data.sec.gov/submissions/CIK%s.json' % cik), since) if cik else []
        return t, f, ev
    with ThreadPoolExecutor(6) as ex:
        sec = list(ex.map(one, tick))
    with ThreadPoolExecutor(8) as ex:
        px = list(ex.map(lambda t: trend(yh_closes(t)), tick))

    items, evs, miss = {}, [], []
    for (t, f, ev), tr in zip(sec, px):
        u = uni[t]
        rec = {'name': u['name'], 'sector': u['sector'], 'industry': u['industry'], 'idx': u['idx'],
               'mcap_b': round((u['mcap'] or 0) / 1e9, 1)}
        if not f: miss.append(t)
        rec.update(f); rec.update(tr)
        items[t] = rec
        for e in ev:
            evs.append(dict(code=t, name=u['name'], sector=u['sector'], **e))
    evs.sort(key=lambda e: e['date'], reverse=True)
    now = dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M')
    if len(items) - len(miss) < len(tick) * 0.6:
        print('★ 재무를 절반 넘게 못 받음 — 저장하지 않음 (%d/%d)' % (len(items) - len(miss), len(tick))); return 1
    json.dump({'updated': now, 'n': len(items), 'no_fund': miss, 'unit': '백만 달러(rev·op·ni·cash·debt·ev), %(성장·이익률·수익률)', 'items': items},
              open(os.path.join(ROOT, 'lists', 'us_fund.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    ep = os.path.join(ROOT, 'lists', 'events.json')
    E = json.load(open(ep, encoding='utf-8')) if os.path.exists(ep) else {}
    E['us'] = {'updated': now, 'since': since, 'items': evs}
    json.dump(E, open(ep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('저장: lists/us_fund.json %d개(재무 못 받음 %d: %s) · 공시 사건 %d건(%s 이후) · %.0f초'
          % (len(items), len(miss), ','.join(miss[:8]), len(evs), since, time.time() - t0))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
