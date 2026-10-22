# -*- coding: utf-8 -*-
"""거시 자료 수집기 — cache/macro.json (번들이 '#거시'로 읽는다)

사용(저장소 루트): python3 tools/macro_fetch.py           # 최근 10일 + 앞으로 14일 달력만 새로 받아 쌓는다
                 python3 tools/macro_fetch.py --backfill # 지난 16개월 달력을 거슬러 채운다(처음 한 번)
GitHub Actions(.github/workflows/macro.yml)가 하루 두 번(한국시간 07:30·23:10) 돌린다.

자료원 (2026-10-22 curl 점검 — FRED는 503, BLS 웹·API는 403·키 없이 한도 초과, ISM은 사람 확인 화면이라 쓰지 않는다)
  - BLS 자료 열람 페이지(data.bls.gov/timeseries — 열린다): CPI·근원 CPI 지수(계절조정·원계열), 실업률, 비농업 고용 수준, 시간당 임금
      공식 월간 표 10년치라 전년비·전월비·3개월 연율·고용 증감·샘 룰을 직접 계산한다. 발표일은 표에 없어,
      새 달이 처음 보인 날(하루 두 번 수집)을 발표일로 적는다. 처음 채울 때는 달력에 같은 발표가 있으면 그 날짜, 없으면 비워 둔다
  - 나스닥 경제 지표 달력: PCE·근원 PCE, 실업수당 청구 4주 평균, ISM(제조·서비스와 가격 지수), 향후 발표 일정
      ★고용 보고서·일부 CPI가 빠져 있다(2026-10-02 9월 고용 보고서 없음) → 물가·고용은 BLS 표를 쓴다
      같은 이름이 두 줄(전년비·전월비)로 나오고 순서가 일정하지 않다 → 절댓값이 작은 쪽을 전월비로
      기준 기간: ISM = 발표 달의 전달, PCE = 발표일이 20일 이후면 전달·그 전이면 전전달(10/1 발표 = 8월분), 청구는 발표일
  - 재무부 금리 곡선(명목·실질): 2년·10년, 10년 기대인플레이션 = 명목 10년 − 실질 10년
  - 뉴욕연준: 실효 연방기금금리와 목표 범위
  - 연준 FOMC 일정 페이지, 한국은행 기준금리 변경 표와 통화정책방향 결정회의 일정
  - 야후: WTI·브렌트 / 지정학 위험 지수(Caldara·Iacoviello, 월간, 1985~2019 평균 = 100) / 구글 뉴스: 지정학 사건 제목
  - 나스닥 실적 달력: 빅테크 실적 발표일
받지 못한 값은 비워 두고 err에 적는다 — 번들이 '확인 불가'로 쓴다. 다른 항목은 멈추지 않는다.
"""
import csv, html, io, json, os, re, sys, time, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
OUT = os.path.join(ROOT, 'cache', 'macro.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
KST = dt.timezone(dt.timedelta(hours=9))
TODAY = dt.datetime.now(KST).date()
ERR = []
BIGTECH = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'META', 'NVDA', 'TSLA', 'AVGO']


def get(url, headers=None, tries=2, timeout=25, raw=False):
    h = {'User-Agent': UA}
    h.update(headers or {})
    for i in range(tries):
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout).read()
            return b if raw else b.decode('utf-8', 'replace')
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def num(s):
    """'3.7%' '254K' '1,701K' '-0.5%' → 숫자. 비었으면 None"""
    s = html.unescape(str(s or '')).strip().replace(',', '')
    m = re.match(r'^([-+]?\d+(?:\.\d+)?)\s*([KMB%]?)', s)
    if not m:
        return None
    v = float(m.group(1))
    return v * {'K': 1, 'M': 1000, 'B': 1e6}.get(m.group(2), 1) if m.group(2) in 'KMB' and m.group(2) else v


# ───────── 1) 경제 지표 달력 ─────────
def cal_day(d):
    s = get('https://api.nasdaq.com/api/calendar/economicevents?date=%s' % d.isoformat(), {'Accept': 'application/json'})
    try:
        rows = (json.loads(s).get('data') or {}).get('rows') or []
    except Exception:
        return d, None
    return d, [r for r in rows if (r.get('country') or '') == 'United States']


def prev_month(d):
    return (d.replace(day=1) - dt.timedelta(days=1)).strftime('%Y-%m')


# (키, 달력 이름 정규식, 종류) — pair: 같은 이름이 전년비·전월비 두 줄, one: 한 줄
SPEC = [
    ('PCE', r'^PCE [Pp]rice [Ii]ndex$', 'pair', None),
    ('CORE_PCE', r'^Core PCE Price Index$', 'pair', None),
    ('CLAIMS4W', r'^Jobless Claims 4-Week Avg', 'one', None),
    ('ISM_MFG', r'^ISM Manufacturing PMI$', 'one', None),
    ('ISM_MFG_PRICES', r'^ISM Manufacturing Prices', 'one', None),
    ('ISM_SVC', r'^ISM (Non-Manufacturing|Services) PMI$', 'one', None),
    ('ISM_SVC_PRICES', r'^ISM (Non-Manufacturing|Services) Prices', 'one', None),
]
SCHED = r'^(CPI|Core PCE Price Index|Nonfarm Payrolls|ISM Manufacturing PMI|ISM (Non-Manufacturing|Services) PMI|GDP \(QoQ\)|PPI)$'


def parse_day(d, rows, hist):
    """하루치 달력 → hist에 쌓는다. 월간은 기준 기간(발표 달의 전달), 청구는 발표일"""
    if not rows:
        return
    by = {}
    for r in rows:
        by.setdefault((r.get('eventName') or '').strip(), []).append(r)
    for key, pat, kind, idx_pat in SPEC:
        names = [n for n in by if re.search(pat, n)]
        if not names:
            continue
        rs = [r for n in names for r in by[n] if num(r.get('actual')) is not None]
        if not rs:
            continue
        if key == 'CLAIMS4W':
            per = d.isoformat()
        elif key in ('PCE', 'CORE_PCE') and d.day < 20:                 # 월초 발표 PCE는 전전달 자료(10/1 발표 = 8월분)
            per = prev_month(d.replace(day=1) - dt.timedelta(days=1))
        else:
            per = prev_month(d)
        if kind == 'one':
            r = rs[0]
            hist.setdefault(key, {})[per] = {'v': num(r['actual']), 'prev': num(r.get('previous')), 'rel': d.isoformat()}
            continue
        vals = [(num(r['actual']), num(r.get('previous'))) for r in rs][:2]
        if len(vals) < 2:
            continue
        mom = None
        if idx_pat:                                                    # 지수 수준으로 전월비를 맞혀 본다
            ir = [r for n in by if re.search(idx_pat, n) for r in by[n]]
            if ir:
                a, p = num(ir[0].get('actual')), num(ir[0].get('previous'))
                if a and p:
                    calc = (a / p - 1) * 100
                    mom = min(vals, key=lambda x: abs(x[0] - calc))
        if mom is None:
            mom = min(vals, key=lambda x: abs(x[0]))                   # 지수가 없으면 절댓값이 작은 쪽이 전월비
        yoy = [x for x in vals if x is not mom][0]
        hist.setdefault(key + '_YOY', {})[per] = {'v': yoy[0], 'prev': yoy[1], 'rel': d.isoformat()}
        hist.setdefault(key + '_MOM', {})[per] = {'v': mom[0], 'prev': mom[1], 'rel': d.isoformat()}


CAL_REL = {}          # (지표, 기준 달) → 달력 발표일 — BLS 표의 발표일을 처음 채울 때 쓴다


def calendar_block(hist, back_days, fwd_days=14):
    days = [TODAY - dt.timedelta(days=i) for i in range(back_days, -1, -1)] + [TODAY + dt.timedelta(days=i) for i in range(1, fwd_days + 1)]
    days = [d for d in days if d.weekday() < 5]
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(cal_day, days))
    miss = [d for d, r in got if r is None]
    if len(miss) > len(days) // 3:
        ERR.append('경제 지표 달력 %d일 중 %d일 받지 못함' % (len(days), len(miss)))
    upcoming = []
    for d, rows in got:
        if d <= TODAY:
            parse_day(d, rows, hist)
            for r in rows or []:
                n = (r.get('eventName') or '').strip()
                if n == 'CPI' and num(r.get('actual')) is not None:
                    CAL_REL[('CPI', prev_month(d))] = CAL_REL[('CORE_CPI', prev_month(d))] = d.isoformat()
                if n == 'Nonfarm Payrolls' and num(r.get('actual')) is not None:
                    for k in ('NFP', 'UNRATE', 'AHE'):
                        CAL_REL[(k, prev_month(d))] = d.isoformat()
        if rows and TODAY <= d <= TODAY + dt.timedelta(days=fwd_days):
            for r in rows:
                n = (r.get('eventName') or '').strip()
                if re.search(SCHED, n) and not any(u['date'] == d.isoformat() and u['name'] == n for u in upcoming):
                    upcoming.append({'date': d.isoformat(), 'name': n})
    return upcoming


# ───────── 1-2) BLS 공식 월간 표 ─────────
BLS = {'CPI_SA': 'CUSR0000SA0', 'CPI_NSA': 'CUUR0000SA0', 'CORE_SA': 'CUSR0000SA0L1E', 'CORE_NSA': 'CUUR0000SA0L1E',
       'UNRATE': 'LNS14000000', 'PAYEMS': 'CES0000000001', 'AHE': 'CES0500000003'}


def bls_series(sid):
    h = get('https://data.bls.gov/timeseries/%s?output_view=data&include_graphs=false' % sid,
            {'Accept': 'text/html', 'Accept-Language': 'en-US,en;q=0.9'})
    if not h or 'id="table0"' not in h:
        return None
    i = h.find('id="table0"'); seg = h[i:h.find('</table>', i)]
    out = {}
    for r in re.findall(r'<tr[^>]*>(.*?)</tr>', seg, re.S | re.I):
        y = re.search(r'<th[^>]*>\s*(\d{4})\s*</th>', r, re.I)
        if not y:
            continue
        cells = [html.unescape(re.sub(r'<[^>]+>', '', c)).strip() for c in re.findall(r'<td[^>]*>(.*?)</td>', r, re.S | re.I)]
        for m, c in enumerate(cells[:12], 1):
            v = re.match(r'^(-?\d+(?:\.\d+)?)', c)
            if v:
                out['%s-%02d' % (y.group(1), m)] = {'v': float(v.group(1)), 'p': '(P)' in c}
    return out or None


def bls_block(seen, cal_rel):
    with ThreadPoolExecutor(7) as ex:
        S = dict(zip(BLS, ex.map(bls_series, BLS.values())))
    for k, v in S.items():
        if not v:
            ERR.append('BLS %s 받지 못함' % k)
    def shift(per, n):
        y, m = map(int, per.split('-')); m -= n
        while m <= 0:
            m += 12; y -= 1
        return '%04d-%02d' % (y, m)
    def chg(s, per, n):
        a, b = (s or {}).get(per), (s or {}).get(shift(per, n))
        return round((a['v'] / b['v'] - 1) * 100, 1) if a and b else None
    out = {}
    def stamp(key, per):                                         # 새 달이 처음 보인 날 = 발표일(처음 채울 때는 달력 날짜)
        d = seen.setdefault(key, {})
        if per not in d:
            d[per] = cal_rel.get((key, per)) or (TODAY.isoformat() if seen.get('_init') else None)
        return d[per]
    for key, sa, nsa in (('CPI', 'CPI_SA', 'CPI_NSA'), ('CORE_CPI', 'CORE_SA', 'CORE_NSA')):
        s_sa, s_nsa = S.get(sa), S.get(nsa)
        if not s_sa or not s_nsa:
            continue
        per = max(set(s_sa) & set(s_nsa)); pp = shift(per, 1)
        a3 = (s_sa[per]['v'] / s_sa[shift(per, 3)]['v']) ** 4 - 1 if s_sa.get(shift(per, 3)) else None
        out[key] = {'per': per, 'rel': stamp(key, per), 'yoy': chg(s_nsa, per, 12), 'yoy_prev': chg(s_nsa, pp, 12),
                    'mom': chg(s_sa, per, 1), 'mom_prev': chg(s_sa, pp, 1), 'ann3': round(a3 * 100, 1) if a3 is not None else None}
    u = S.get('UNRATE')
    if u:
        per = max(u); ks = sorted(u)
        avg3 = lambda p: (sum(u[x]['v'] for x in (p, shift(p, 1), shift(p, 2)) if x in u) / len([x for x in (p, shift(p, 1), shift(p, 2)) if x in u])) if p in u else None
        prior = [avg3(shift(per, k)) for k in range(1, 13) if shift(per, k) in u]
        sahm = round(avg3(per) - min(prior), 2) if prior else None
        out['UNRATE'] = {'per': per, 'rel': stamp('UNRATE', per), 'v': u[per]['v'], 'prev': u.get(shift(per, 1), {}).get('v'),
                         'sahm': sahm, 'sahm_on': (sahm is not None and sahm >= 0.5)}
    e = S.get('PAYEMS')
    if e:
        per = max(e)
        d = lambda p: round(e[p]['v'] - e[shift(p, 1)]['v']) if p in e and shift(p, 1) in e else None
        last3 = [d(shift(per, k)) for k in range(3)]
        out['NFP'] = {'per': per, 'rel': stamp('NFP', per), 'v': d(per), 'prev': d(shift(per, 1)), 'prelim': e[per]['p'],
                      'avg3': round(sum(x for x in last3 if x is not None) / len([x for x in last3 if x is not None])) if any(x is not None for x in last3) else None}
    w = S.get('AHE')
    if w:
        per = max(w)
        out['AHE'] = {'per': per, 'rel': stamp('AHE', per), 'yoy': chg(w, per, 12), 'yoy_prev': chg(w, shift(per, 1), 12)}
    return out


# ───────── 2) 금리 ─────────
def treasury(kind):
    rows = []
    for yr in (TODAY.year, TODAY.year - 1):
        t = get('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/%d/all'
                '?type=%s&field_tdr_date_value=%d&page&_format=csv' % (yr, kind, yr))
        if t:
            rows += list(csv.DictReader(io.StringIO(t)))
        if len(rows) > 25:
            break
    return rows


def rates_block():
    out = {}
    try:
        nom, real = treasury('daily_treasury_yield_curve'), treasury('daily_treasury_real_yield_curve')
        f = lambda r, k: float(r[k]) if r and r.get(k) not in (None, '', 'N/A') else None
        if nom:
            out['y2'] = {'v': f(nom[0], '2 Yr'), 'd20': f(nom[20], '2 Yr') if len(nom) > 20 else None, 'date': nom[0].get('Date')}
            out['y10'] = {'v': f(nom[0], '10 Yr'), 'd20': f(nom[20], '10 Yr') if len(nom) > 20 else None, 'date': nom[0].get('Date')}
        if nom and real:
            rd = {r.get('Date'): r for r in real}
            be = lambda r: round(f(r, '10 Yr') - f(rd[r['Date']], '10 YR'), 2) if r.get('Date') in rd and f(r, '10 Yr') is not None and f(rd[r['Date']], '10 YR') is not None else None
            out['be10'] = {'v': be(nom[0]), 'd20': be(nom[20]) if len(nom) > 20 else None, 'date': nom[0].get('Date')}
    except Exception as e:
        ERR.append('재무부 금리 곡선 — %s' % type(e).__name__)
    if 'y2' not in out:
        ERR.append('재무부 금리 곡선 받지 못함')
    try:
        j = json.loads(get('https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json') or '{}')
        r = (j.get('refRates') or [{}])[0]
        if r.get('percentRate') is not None:
            out['effr'] = {'v': r['percentRate'], 'lo': r.get('targetRateFrom'), 'hi': r.get('targetRateTo'), 'date': r.get('effectiveDate')}
    except Exception as e:
        ERR.append('뉴욕연준 기준금리 — %s' % type(e).__name__)
    if 'effr' not in out:
        ERR.append('연준 기준금리 받지 못함')
    return out


# ───────── 3) 회의 일정 ─────────
MON = {m: i for i, m in enumerate(['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
                                   'October', 'November', 'December'], 1)}


def fomc_dates():
    h = get('https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm') or ''
    out = []
    for yr in (TODAY.year, TODAY.year + 1):
        i = h.find('%d FOMC Meetings' % yr)
        if i < 0:
            continue
        j = h.find('FOMC Meetings', i + 20)
        seg = h[i:j if j > 0 else i + 40000]
        for mon, day in re.findall(r'fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<', seg, re.S):
            mon = mon.split('/')[-1].strip(); m = re.match(r'(\d+)(?:-(\d+))?', day.strip())
            if mon in MON and m:
                end = int(m.group(2) or m.group(1))
                mo = MON[mon] + (1 if m.group(2) and int(m.group(2)) < int(m.group(1)) else 0)
                try:
                    out.append({'start': dt.date(yr, MON[mon], int(m.group(1))).isoformat(), 'end': dt.date(yr, min(mo, 12), end).isoformat(),
                                'sep': day.strip().endswith('*')})
                except ValueError:
                    pass
    if not out:
        ERR.append('FOMC 일정 받지 못함')
    return out


def bok_block():
    out = {}
    t = get('https://www.bok.or.kr/portal/singl/baseRate/list.do?dataSeCd=01&menuNo=200643')
    if t:
        txt = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
        m = re.findall(r'(20\d\d) (\d\d)월 (\d\d)일 (\d+\.\d+)', txt)
        if m:
            y, mo, d, v = m[0]
            out['rate'] = {'v': float(v), 'since': '%s-%s-%s' % (y, mo, d), 'prev': float(m[1][3]) if len(m) > 1 else None}
    t = get('https://www.bok.or.kr/portal/singl/crncyPolicyDrcMtg/listYear.do?mtgSe=A&menuNo=200755')
    if t:
        txt = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', t)))
        ds = []
        for mo, d in re.findall(r'(\d{1,2})월\s*(\d{1,2})일\s*\([월화수목금]\)', txt):
            try:
                ds.append(dt.date(TODAY.year, int(mo), int(d)))
            except ValueError:
                pass
        nxt = [x for x in sorted(set(ds)) if x >= TODAY]
        if nxt:
            out['next'] = nxt[0].isoformat()
    if 'rate' not in out:
        ERR.append('한은 기준금리 받지 못함')
    if 'next' not in out:
        ERR.append('한은 다음 회의일 받지 못함')
    return out


# ───────── 4) 지정학 ─────────
def yclose(sym):
    s = get('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=3mo&interval=1d' % urllib.parse.quote(sym))
    try:
        r = json.loads(s)['chart']['result'][0]
        c = [x for x in r['indicators']['quote'][0]['close'] if x]
        return {'v': round(c[-1], 2), 'd5': round((c[-1] / c[-6] - 1) * 100, 1), 'd20': round((c[-1] / c[-21] - 1) * 100, 1),
                'date': dt.datetime.fromtimestamp(r['meta']['regularMarketTime'], KST).strftime('%Y-%m-%d')}
    except Exception:
        ERR.append('야후 %s 받지 못함' % sym)
        return None


def gpr_block():
    b = get('https://www.matteoiacoviello.com/gpr_files/data_gpr_export.xls', raw=True, timeout=40)
    if not b:
        ERR.append('지정학 위험 지수 받지 못함'); return None
    try:
        import xlrd
        wb = xlrd.open_workbook(file_contents=b); sh = wb.sheet_by_index(0)
        hd = [str(c) for c in sh.row_values(0)]
        rows = [sh.row_values(i) for i in range(1, sh.nrows)]
        rows = [r for r in rows if r[hd.index('GPR')] not in ('', None)]
        mon = lambda r: '%04d-%02d' % xlrd.xldate_as_tuple(r[0], wb.datemode)[:2]
        last = rows[-1]; prev = rows[-2]
        avg12 = sum(r[hd.index('GPR')] for r in rows[-13:-1]) / 12
        return {'v': round(last[hd.index('GPR')], 1), 'month': mon(last), 'prev': round(prev[hd.index('GPR')], 1),
                'avg12': round(avg12, 1), 'threat': round(last[hd.index('GPRT')], 1), 'act': round(last[hd.index('GPRA')], 1)}
    except ImportError:
        ERR.append('지정학 위험 지수 — xlrd 없음'); return None
    except Exception as e:
        ERR.append('지정학 위험 지수 — %s' % type(e).__name__); return None


def geo_news():
    out, seen = [], set()
    for q in ('중동 전쟁', '이란 이스라엘', '지정학 리스크', '관세 무역 갈등'):
        t = get('https://news.google.com/rss/search?q=%s+when:7d&hl=ko&gl=KR&ceid=KR:ko' % urllib.parse.quote(q))
        for it in re.findall(r'<item>(.*?)</item>', t or '', re.S)[:6]:
            ti = html.unescape(re.sub(r'<[^>]+>', '', (re.search(r'<title>(.*?)</title>', it, re.S) or [None, ''])[1])).strip()
            pd = (re.search(r'<pubDate>(.*?)</pubDate>', it) or [None, ''])[1]
            try:
                d = dt.datetime.strptime(pd[:25], '%a, %d %b %Y %H:%M:%S').replace(tzinfo=dt.timezone.utc).astimezone(KST).strftime('%m-%d')
            except Exception:
                d = ''
            key = re.sub(r'\W', '', ti)[:30]
            if ti and key not in seen:
                seen.add(key); out.append({'date': d, 'title': ti[:90]})
    out.sort(key=lambda x: x['date'], reverse=True)
    if not out:
        ERR.append('지정학 뉴스 제목 받지 못함')
    return out[:6]


def earnings_dates():
    out = []
    def one(tk):
        s = get('https://api.nasdaq.com/api/analyst/%s/earnings-date' % tk, {'Accept': 'application/json'})
        try:
            t = (json.loads(s).get('data') or {}).get('reportText') or ''
            m = re.search(r'(\d{1,2})/(\d{1,2})/(\d{4})', t)
            when = '장 마감 뒤' if 'after market close' in t else ('장 시작 전' if 'before market open' in t else '')
            return {'tk': tk, 'date': '%s-%02d-%02d' % (m.group(3), int(m.group(1)), int(m.group(2))), 'when': when} if m else None
        except Exception:
            return None
    with ThreadPoolExecutor(8) as ex:
        out = [x for x in ex.map(one, BIGTECH) if x]
    return sorted(out, key=lambda x: x['date'])


def main(argv):
    old = {}
    if os.path.exists(OUT):
        try:
            old = json.load(open(OUT, encoding='utf-8'))
        except Exception:
            old = {}
    hist = old.get('hist') or {}
    for k in ('CPI_YOY', 'CPI_MOM', 'CORE_CPI_YOY', 'CORE_CPI_MOM', 'AHE_YOY', 'AHE_MOM', 'NFP', 'UNRATE'):
        hist.pop(k, None)                                          # 물가·고용은 BLS 표로 옮겼다
    seen = old.get('seen') or {}
    if not seen:
        seen['_init'] = False                                      # 처음 채울 때는 달력에 없는 발표일을 오늘로 적지 않는다
    back = 480 if ('--backfill' in argv or len(hist.get('UNRATE') or {}) < 15) else 10
    with ThreadPoolExecutor(6) as ex:
        f_cal = ex.submit(calendar_block, hist, back)
        f_rate = ex.submit(rates_block); f_fomc = ex.submit(fomc_dates); f_bok = ex.submit(bok_block)
        f_wti = ex.submit(yclose, 'CL=F'); f_brent = ex.submit(yclose, 'BZ=F'); f_gpr = ex.submit(gpr_block)
        f_news = ex.submit(geo_news); f_earn = ex.submit(earnings_dates)
        upcoming = f_cal.result()
        blsd = bls_block(seen, CAL_REL)
        seen['_init'] = True
        out = {'collected_at': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M KST'), 'hist': hist, 'bls': blsd, 'seen': seen, 'rates': f_rate.result(),
               'fomc': f_fomc.result(), 'bok': f_bok.result(), 'oil': {'wti': f_wti.result(), 'brent': f_brent.result()},
               'gpr': f_gpr.result(), 'geo_news': f_news.result(), 'earnings': f_earn.result(), 'upcoming': upcoming, 'err': ERR}
    for k in list(hist):                                              # 오래된 기록은 20개월(청구는 30주)만 남긴다
        keep = 30 if k == 'CLAIMS4W' else 20
        hist[k] = dict(sorted(hist[k].items())[-keep:])
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1); f.write('\n')
    print('저장: %s · 쌓인 지표 %s · 오류 %s' % (os.path.relpath(OUT, ROOT), {k: len(v) for k, v in hist.items()}, ERR or '없음'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
