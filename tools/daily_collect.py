# -*- coding: utf-8 -*-
"""매일 판단 입력 — 거시·시장·뉴스·TOP 20 종목 상태를 한 번에 모은다 (tools/daily_judge.md 절차의 1단계)

사용(저장소 루트): python3 tools/daily_collect.py > /tmp/today.txt      (판단 실행)
                  python3 tools/daily_collect.py --notes                (노트 쌓기 실행 — 노트 쓸 차례 6개만)
  - 번들(tools/bundle.py)을 국장·미장 한 번씩 돌려 머리 줄(#거시·#시장·#미국시장·#미국 심리·신용·#환율·유가·시장 뉴스)과
    지수·자산 자료 카드(시황 종합과 같은 대용 ETF 7개 — 가격 위치·거래량·수급·분배금·공매도)를 가져온다(2026-11-25)
  - 지정학·정책 뉴스 제목(최근 2일): 휴전·공습·관세·금리·수출 규제 등 — 판단을 바꿀 사건을 놓치지 않으려고
  - TOP 20 판단 종목(top20/picks.json) 80개의 지금 가격·1일·5일 등락·판단 때 대비·목표까지·손절까지·도달 여부
  - 깔때기 재료(2026-11-03): 재료가 오래됐으면 여기서 새로 받고(업종 추세·공시 사건은 매번, 미장 재무는 20시간 넘으면),
    거르기(tools/funnel_screen.py)를 돌려 업종 점수·후보 표·공시 사건·기업 노트 현황을 붙인다
  - 지표 판(점수판, 2026-11-26): 주요 지수 8개(S&P 500·나스닥·필라델피아 반도체·코스피·코스닥·니케이·대만 가권·중국 상해, 2026-11-27)와
    공포·탐욕·VIX·미국 2년·10년·연준·한은 기준금리·원/달러·달러 인덱스·WTI를 코드가 모아
    /tmp/kf_dash.json에 쓴다 → top20/tools/build.py가 top20.json의 meta.dash로 넣고, 사이트가 시황 리포트 맨 위에 보여 준다.
    판단도 같은 값을 보도록 출력에도 찍는다. 저장소 밖(/tmp)에 쓰는 건 판단 커밋에 섞이지 않게(판단 반영 워크플로가 허용 파일만 옮긴다)
받지 못한 것은 '확인 불가'로 적고 멈추지 않는다.
"""
import csv, html, io, json, os, re, subprocess, sys, time, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
KST = dt.timezone(dt.timedelta(hours=9))
HEAD_KR = ('#거시', '물가 ·', '경기 ·', '정책 ·', '지정학 ·', '일정(2주)', '거시 수집 오류', '#시장 ', '#해외·환율', '#미국 금리·달러', '#시장 뉴스')
HEAD_US = ('#미국시장', '#미국 심리·신용', '#환율·유가', '#시장 뉴스')
# 지수·자산 자료 카드(2026-11-25) — 사이트의 '시황 종합' 프롬프트와 같은 자료로 리포트를 쓰려고. 대용: KODEX 200=코스피, KODEX 코스닥150=코스닥,
#   SPY=S&P 500, IWM=러셀 2000, QQQ=나스닥, SOXX=필라델피아 반도체, TLT=미국 장기 국채(금·비트코인은 #미국시장 줄)
IDX_KR = ['069500', '229200']
IDX_US = ['SPY', 'IWM', 'QQQ', 'SOXX', 'TLT']
CARD_US = ('#시세', '#가격', '#분할·배당', '#기관·공매도·내부자')       # ETF라 재무·실적·SEC·영문 뉴스는 뺀다
NEWS_KO = ['휴전', '이란 공습', '관세', '연준 금리', '한국은행 금리', '반도체 수출 규제', '코스피 외국인']
NEWS_EN = ['ceasefire', 'Fed rate decision', 'tariffs', 'AI capex', 'oil prices']
DASH_TMP = '/tmp/kf_dash.json'
RAW = 'https://raw.githubusercontent.com/dodary-kfilter/kfilter/main'
TREASURY = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/%d/all'
            '?type=daily_treasury_yield_curve&field_tdr_date_value=%d&page&_format=csv')


def get(url, tries=2):
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=20).read().decode('utf-8', 'replace')
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def bundle(args):
    try:
        r = subprocess.run([sys.executable, os.path.join(ROOT, 'tools', 'bundle.py')] + args + ['--ver', 'dev'],
                           capture_output=True, text=True, timeout=150, cwd=ROOT)
        return r.stdout
    except Exception as e:
        return '#오류 번들 실행 실패 — %s' % type(e).__name__


def heads(text, keys):
    out = []
    for l in (text or '').split('\n'):
        if l.startswith(keys):
            out.append(l[:1600])
    return out or ['확인 불가']


def cards(text, us):
    """번들의 자료 카드(==== 이름 ====)에서 지수·자산 판단에 쓸 줄만 — 국장 카드는 전부, 미장 카드는 시세·가격·분배금·공매도"""
    out, on = [], False
    for l in (text or '').split('\n'):
        if l.startswith('===='):
            on = True; out.append(l); continue
        if not on:
            continue
        if l.startswith(('#오류', '#수집시간')):
            on = False; continue
        if (l.startswith(CARD_US) if us else l.strip()):
            out.append(l[:1600])
    return out or ['확인 불가']


def news(q, en=False):
    url = 'https://news.google.com/rss/search?q=%s+when:2d&%s' % (urllib.parse.quote(q), 'hl=en-US&gl=US&ceid=US:en' if en else 'hl=ko&gl=KR&ceid=KR:ko')
    t = get(url) or ''
    out = []
    for it in re.findall(r'<item>(.*?)</item>', t, re.S)[:5]:
        ti = html.unescape(re.sub(r'<[^>]+>', '', (re.search(r'<title>(.*?)</title>', it, re.S) or [None, ''])[1])).strip()
        pd = (re.search(r'<pubDate>(.*?)</pubDate>', it) or [None, ''])[1]
        try:
            d = dt.datetime.strptime(pd[:25], '%a, %d %b %Y %H:%M:%S').replace(tzinfo=dt.timezone.utc).astimezone(KST).strftime('%m-%d %H:%M')
        except Exception:
            d = ''
        out.append('%s %s' % (d, ti[:120]))
    return out


def price(sym):
    t = get('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1mo&interval=1d' % urllib.parse.quote(sym))
    try:
        r = json.loads(t)['chart']['result'][0]; c = [v for v in r['indicators']['quote'][0]['close'] if v]
        m = r.get('meta') or {}
        cur = m.get('regularMarketPrice') or c[-1]
        return cur, (cur / c[-2] - 1) * 100 if len(c) > 1 else None, (cur / c[-6] - 1) * 100 if len(c) > 5 else None
    except Exception:
        return None


def head_json(text, key):
    """번들 머리 줄 '#키 {json}'의 json — 없거나 깨졌으면 {}"""
    for l in (text or '').split('\n'):
        if l.startswith(key + ' {'):
            try:
                return json.loads(l[len(key) + 1:])
            except Exception:
                return {}
    return {}


def get_h(url, headers, tries=2):
    """머리글(Referer 등)이 있어야 열리는 곳 — 다음 환율·CNN"""
    h = {'User-Agent': UA}
    h.update(headers)
    for i in range(tries):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=20).read().decode('utf-8', 'replace')
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


# ── 지표 판(점수판) 자료 — 값과 전 거래일 대비 변화를 한 출처의 일별 값에서 같이 받는다(DASH_N일 — 휴장이 끼어도 전 거래일이 들어오게)
DASH_N = 10


def ych(sym):
    """야후 일봉 — (지금 값, 종가 목록 오래된 것부터). 마지막 봉이 오늘 것이면 그 종가가 지금 값이다"""
    t = get('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1mo&interval=1d' % urllib.parse.quote(sym))
    try:
        r = json.loads(t)['chart']['result'][0]
        c = [v for v in r['indicators']['quote'][0]['close'] if v]
        cur = (r.get('meta') or {}).get('regularMarketPrice') or c[-1]
        if c and abs(c[-1] - cur) > 1e-9 * max(1, abs(cur)):
            c[-1] = cur                                         # 오늘 봉의 종가가 늦게 채워지는 경우 지금 값으로
        return cur, c[-DASH_N:]
    except Exception:
        return None


def treasury():
    """재무부 금리 곡선(일별) — {'2 Yr': [오래된 것부터], '10 Yr': [...]}"""
    yr, rows = dt.datetime.now(KST).year, []
    for y in (yr, yr - 1):
        t = get(TREASURY % (y, y))
        if t:
            rows += list(csv.DictReader(io.StringIO(t)))
        if len(rows) >= DASH_N:
            break
    f = lambda r, k: float(r[k]) if r.get(k) not in (None, '', 'N/A') else None
    out = {}
    for k in ('2 Yr', '10 Yr'):
        v = [f(r, k) for r in rows[:DASH_N]]
        v = [x for x in v if x is not None][::-1]
        if len(v) >= 2:
            out[k] = v
    return out


def cnn_fg():
    """CNN 공포·탐욕 — (지금 점수, 상태, 날짜별 점수 오래된 것부터). 날짜마다 마지막 값, 끝의 이월된 같은 값(장 열기 전)은 뺀다"""
    t = get_h('https://production.dataviz.cnn.io/index/fearandgreed/graphdata',
              {'Accept': 'application/json', 'Origin': 'https://edition.cnn.com', 'Referer': 'https://edition.cnn.com/markets/fear-and-greed'})
    try:
        j = json.loads(t)
        fg = j['fear_and_greed']
        day = {}
        for p in (j.get('fear_and_greed_historical') or {}).get('data') or []:
            day[dt.datetime.fromtimestamp(p['x'] / 1000, dt.timezone.utc).strftime('%Y-%m-%d')] = p['y']
        h = [day[d] for d in sorted(day)]
        if len(h) >= 2 and h[-1] == h[-2]:
            h.pop()
        s = fg['score']
        if not h or abs(h[-1] - s) > 0.05:
            h.append(s)
        return s, FG_RATE.get(fg.get('rating'), fg.get('rating') or ''), h[-DASH_N:]
    except Exception:
        return None


def daum_fx():
    """다음 원/달러(매매기준율) 일별 — (지금 값, 오래된 것부터)"""
    t = get_h('https://finance.daum.net/api/exchanges/FRX.KRWUSD/days?symbolCode=FRX.KRWUSD&terms=days&page=1&perPage=%d' % DASH_N,
              {'Referer': 'https://finance.daum.net/exchanges/FRX.KRWUSD'})
    try:
        v = [r['basePrice'] for r in json.loads(t)['data'] if r.get('basePrice')][::-1]
        return (v[-1], v) if len(v) >= 2 else None
    except Exception:
        return None


FG_RATE = {'extreme fear': '극단적 공포', 'fear': '공포', 'neutral': '중립', 'greed': '탐욕', 'extreme greed': '극단적 탐욕'}


# 점수판의 주요 지수(2026-11-27) — 다음 금융 한 곳에서(번들의 #시장·#해외·환율과 같은 출처). 국장은 시장 지수 일별, 해외는 지수 일별
DASH_IDX = [('S&P 500', 'US.SP500'), ('나스닥', 'US.COMP'), ('필라델피아 반도체', 'US.SOX'), ('코스피', 'KOSPI'), ('코스닥', 'KOSDAQ'),
            ('니케이', 'JP.NI225'), ('대만 가권', 'TW.TAIEX'), ('중국 상해', 'CN000001')]


def daum_idx(code):
    """다음 지수 일별 — (마지막 날짜, 종가 오래된 것부터). 장 열기 전 거래량 0인 줄은 뺀다"""
    if code in ('KOSPI', 'KOSDAQ'):
        url, ref = ('https://finance.daum.net/api/market_index/days?page=1&perPage=%d&market=%s&pagination=true' % (DASH_N, code),
                    'https://finance.daum.net/domestic')
    else:
        url, ref = 'https://finance.daum.net/api/quote/%s/days?page=1&perPage=%d' % (code, DASH_N), 'https://finance.daum.net/global/quotes/' + code
    try:
        rows = [r for r in json.loads(get_h(url, {'Referer': ref}))['data'] if r.get('tradePrice')]
        if code in ('KOSPI', 'KOSDAQ'):
            rows = [r for r in rows if r.get('accTradeVolume') != 0]
        c = [r['tradePrice'] for r in rows][::-1]
        return (rows[0]['date'][:10], c) if len(c) >= 2 else None
    except Exception:
        return None


def dash_indices():
    """점수판 지수 칸 — (칸 목록, 지수 코드별 마지막 거래일). 다른 지수보다 날짜가 늦은 칸(휴장 등)엔 그 날짜를 적는다"""
    with ThreadPoolExecutor(8) as ex:
        got = list(ex.map(lambda x: daum_idx(x[1]), DASH_IDX))
    dates = {code: (g[0] if g else None) for (_, code), g in zip(DASH_IDX, got)}
    last = max((g[0] for g in got if g), default='')
    out = []
    for (nm, _), g in zip(DASH_IDX, got):
        if not g:
            out.append({'g': 'idx', 'name': nm, 'value': '확인 불가', 'chg': '', 'dir': '', 'sub': ''}); continue
        d, c = g
        r = (c[-1] / c[-2] - 1) * 100 if c[-2] else None
        out.append({'g': 'idx', 'name': nm, 'value': '{:,.2f}'.format(c[-1]), 'chg': '%+.2f%%' % r if r is not None else '',
                    'dir': ('up' if r > 0 else 'down' if r < 0 else '') if r is not None else '',
                    'sub': ('%d/%d 기준' % (int(d[5:7]), int(d[8:10]))) if d < last else ''})
    return out, dates


def dash_snapshot(kr, us):
    """지표 판(점수판) — 리포트 맨 위에 박아 두고 매일 추적하는 값. 받지 못한 값은 '확인 불가'(추정하지 않는다).
    변화는 전 거래일 대비 — 값과 변화를 같은 출처에서 받는다(그래프는 넣지 않는다, 2026-11-27)"""
    now = dt.datetime.now(KST)
    with ThreadPoolExecutor(7) as ex:
        F = {k: ex.submit(*a) for k, a in {'vix': (ych, '^VIX'), 'dxy': (ych, 'DX-Y.NYB'), 'wti': (ych, 'CL=F'), 'tr': (treasury,),
                                            'fg': (cnn_fg,), 'fx': (daum_fx,), 'mac': (get, RAW + '/cache/macro.json')}.items()}
        R = {k: f.result() for k, f in F.items()}
    try:
        M = json.loads(R['mac'] or '{}')
    except Exception:
        M = {}
    NA = '확인 불가'
    sign = lambda v: 'up' if v and v > 0 else ('down' if v and v < 0 else '')
    def item(name, value, chg=None, sub='', d=None):
        return {'name': name, 'value': value if value else NA, 'chg': chg or '', 'dir': sign(d) if chg else '', 'sub': sub}
    def mkt(name, got, fmt, pct_dp):                            # 야후·다음 일봉: 값, 전 거래일 대비 %
        if not got or len(got[1]) < 2:
            return item(name, None)
        cur, h = got
        d = (cur / h[-2] - 1) * 100 if h[-2] else None
        return item(name, fmt % cur if '%' in fmt else fmt.format(cur), ('%+.' + str(pct_dp) + 'f%%') % d if d is not None else None, '', d)
    out = []
    fg = R['fg']
    if fg:
        s, rate, h = fg
        d = round(h[-1] - h[-2], 1) if len(h) >= 2 else None
        out.append(item('공포·탐욕', '%.1f' % s, '%+.1f' % d if d is not None else None, rate, d))
    else:                                                       # CNN을 직접 못 받으면 번들이 본 값(추이 없이)
        sent = head_json(us, '#미국 심리·신용')
        b = next((v for k, v in sent.items() if k.startswith('CNN공포탐욕')), None) or {}
        s = b.get('점수')
        out.append(item('공포·탐욕', '%.1f' % s if isinstance(s, (int, float)) else None, sub=b.get('상태') or ''))
    out.append(mkt('VIX', R['vix'], '%.2f', 1))
    tr, usm, sent = R['tr'] or {}, head_json(us, '#미국시장'), head_json(us, '#미국 심리·신용')
    for nm, k, alt in (('미국 10년', '10 Yr', (usm.get('미국10년물금리') or {}).get('수준')), ('미국 2년', '2 Yr', (sent.get('미국2년물') or {}).get('금리'))):
        h = tr.get(k)
        if h:
            bp = round((h[-1] - h[-2]) * 100)
            out.append(item(nm, '%.2f%%' % h[-1], '%+dbp' % bp, '', bp))
        else:                                                   # 재무부 표를 못 받으면 번들이 본 값(변화·추이 없이)
            out.append(item(nm, '%.2f%%' % alt if isinstance(alt, (int, float)) else None))
    E, B = (M.get('rates') or {}).get('effr') or {}, M.get('bok') or {}
    md = lambda s: '%d/%d' % (int(s[5:7]), int(s[8:10])) if s and re.match(r'\d{4}-\d\d-\d\d', s) else ''
    nx = next((f for f in (M.get('fomc') or []) if (f.get('end') or '') >= now.date().isoformat()), None)
    fomc = ''
    if nx:
        a, b = nx.get('start') or nx['end'], nx['end']
        fomc = '다음 ' + (md(a) + ('~%d' % int(b[8:10]) if a[5:7] == b[5:7] else '~' + md(b)) if a != b else md(b))
    num = lambda v: isinstance(v, (int, float))
    out.append(item('연준 기준금리', '%.2f~%.2f%%' % (E['lo'], E['hi']) if num(E.get('lo')) and num(E.get('hi')) else None, sub=fomc))
    br = (B.get('rate') or {}).get('v')
    out.append(item('한은 기준금리', '%.2f%%' % br if num(br) else None, sub=('다음 ' + md(B.get('next'))) if md(B.get('next')) else ''))
    if R['fx']:
        out.append(mkt('원/달러', R['fx'], '{:,.1f}원', 2))
    else:                                                       # 다음 환율을 못 받으면 번들이 본 값(추이 없이)
        gl = head_json(kr, '#해외·환율') or head_json(us, '#환율·유가')
        fx = next((v for k, v in gl.items() if 'USD/KRW' in k), None)
        ok = fx and num(fx[0])
        out.append(item('원/달러', '{:,.1f}원'.format(fx[0]) if ok else None, '%+.2f%%' % fx[1] if ok and num(fx[1]) else None, '', ok and fx[1]))
    out.append(mkt('달러 인덱스', R['dxy'], '%.2f', 2))
    out.append(mkt('WTI', R['wti'], '%.2f달러', 1))
    for x in out:
        x['g'] = 'mac'
    idx, dates = dash_indices()
    # 각 시장의 마지막 거래일(국장 = 코스피, 미장 = S&P 500) — 다음 판단 때 '새 거래가 있나'를 이걸로 본다(휴장·주말엔 판단 쉼)
    return {'at': now.strftime('%Y-%m-%d %H:%M'), 'sessions': {'kr': dates.get('KOSPI'), 'us': dates.get('US.SP500')}, 'items': idx + out}


# ── 새 거래 확인(2026-11-29) — 지난 판단 뒤로 국장·미장 어디에도 새 거래가 없으면(주말·연휴) 판단을 새로 하지 않는다.
#   같은 숫자로 다시 판단하면 리포트가 '토요일이라 거래가 없었다'로 채워지고 사용량만 든다. 월요일 아침만은 주말 소식을 반영하려 판단한다
WEEK = '월화수목금토일'


def mdw(s):
    return '%d/%d(%s)' % (int(s[5:7]), int(s[8:10]), WEEK[dt.date(int(s[:4]), int(s[5:7]), int(s[8:10])).weekday()]) if s and re.match(r'\d{4}-\d\d-\d\d', s) else '확인 불가'


def session_check(now=None):
    """(판단할지, 설명 줄들). 지난 판단의 거래일은 top20/top20.json의 meta.dash.sessions"""
    now = now or dt.datetime.now(KST)
    with ThreadPoolExecutor(2) as ex:
        k, u = ex.submit(daum_idx, 'KOSPI'), ex.submit(daum_idx, 'US.SP500')
        cur = {'kr': (k.result() or [None])[0], 'us': (u.result() or [None])[0]}
    try:
        m = json.load(open(os.path.join(ROOT, 'top20', 'top20.json'), encoding='utf-8')).get('meta') or {}
    except Exception:
        m = {}
    last, ja = (m.get('dash') or {}).get('sessions') or {}, str(m.get('judged_at') or '')
    new = [k for k in ('kr', 'us') if cur.get(k) and last.get(k) and cur[k] > last[k]]
    lines = ['국장 마지막 거래 %s · 미장 마지막 거래 %s' % (mdw(cur['kr']), mdw(cur['us'])),
             '지난 판단 %s — 국장 %s · 미장 %s 거래까지 반영' % (ja[:16] or '확인 불가', mdw(last.get('kr')), mdw(last.get('us')))]
    if not last or not (cur['kr'] or cur['us']):
        return True, lines + ['→ 판단함: 지난 판단의 거래일 기록이 없거나 지금 거래일을 받지 못해 평소대로 판단한다']
    if new:
        return True, lines + ['→ 판단함: 새 거래 — ' + ' · '.join('%s %s' % ('국장' if x == 'kr' else '미장', mdw(cur[x])) for x in new)]
    if now.weekday() == 0 and ja[:10] < now.strftime('%Y-%m-%d'):
        return True, lines + ['→ 판단함: 새 거래는 없지만 월요일 장 전이라 주말 소식을 반영한다']
    return False, lines + ['→ 판단 쉼: 지난 판단 뒤로 국장·미장 모두 새 거래가 없다(주말·휴장). 판단을 새로 하지 않는다']


def run_tool(args, timeout):
    try:
        r = subprocess.run([sys.executable] + args, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
        return (r.stdout or '').strip().split('\n')[0][:160] or (r.stderr or '').strip()[-160:]
    except Exception as e:
        return '실패 — %s' % type(e).__name__


def age_h(path, key='updated'):
    try:
        u = json.load(open(os.path.join(ROOT, path), encoding='utf-8')).get(key)
        t = dt.datetime.strptime(str(u)[:16], '%Y-%m-%d %H:%M').replace(tzinfo=KST)
        return (dt.datetime.now(KST) - t).total_seconds() / 3600
    except Exception:
        return 999


def refresh_materials():
    """깔때기 재료 — GitHub 예약이 빠져도 묵은 재료로 판단하지 않게 여기서 새로 받는다(이 세션 안에서만 쓰고 커밋하지 않는다)"""
    log = []
    log.append('업종 추세: ' + run_tool(['tools/sector_fetch.py'], 60))
    log.append('국장 공시: ' + run_tool(['tools/kr_events_fetch.py'], 120))
    if age_h('lists/us_fund.json') > 20:
        log.append('미장 재무·공시: ' + run_tool(['tools/us_fund_fetch.py'], 400))
    else:
        log.append('미장 재무·공시: %.0f시간 전 자료 사용(20시간 안)' % age_h('lists/us_fund.json'))
    log.append('국장 기업 숫자: %.0f시간 전 자료(수급 스캔이 만든다)' % age_h('lists/kr_fund.json'))
    log.append('거르기: ' + run_tool(['tools/funnel_screen.py'], 60))
    return log


def fmt(v, d=1, pct=False):
    if v is None or v == '': return '-'
    if isinstance(v, (int, float)): return ('%+.' + str(d) + 'f' if pct else '%.' + str(d) + 'f') % v
    return str(v)


def note_info(mk, code):
    p = os.path.join(ROOT, 'notes', mk, code.replace('/', '_') + '.md')
    if not os.path.exists(p): return '없음'
    m = re.search(r'업데이트:\s*(\d{4}-\d{2}-\d{2})', open(p, encoding='utf-8').read(400))
    return m.group(1) if m else '있음'


def print_funnel():
    C = {}
    try:
        C = json.load(open(os.path.join(ROOT, 'lists', 'candidates.json'), encoding='utf-8'))
    except Exception:
        print('\n## 깔때기 재료 — 확인 불가(후보 파일 없음)'); return
    print('\n## 업종 점수 (코드 계산 — 3개월·1개월 지수 대비 초과, 50·200일선 추세, 업종 기업 매출 성장 중앙값. 국면 적합성·앞으로의 이익은 네가 판단)')
    for mk in ('us', 'kr'):
        print('%s|업종|점수|3개월 초과%%p|1개월 초과%%p|3개월%%|고점 대비%%|200일선 위|매출 성장 중앙값%%' % ('미장' if mk == 'us' else '국장'))
        for x in (C.get('sectors') or {}).get(mk) or []:
            print('%s|%s|%.0f|%s|%s|%s|%s|%s|%s' % (mk, x['name'], x['score'], fmt(x.get('rs3m'), pct=True), fmt(x.get('rs1m'), pct=True), fmt(x.get('r3m'), pct=True),
                                                  fmt(x.get('hi52')), '예' if x.get('above_ma200') else '아니오', fmt(x.get('growth_med'))))
    print('\n## 후보 (거르기 결과 — 지금 TOP 20 전부 + 안정형·적극형 새 후보 상위 30씩 + 꼭 볼 것). 노트=기업 노트 마지막 업데이트')
    for mk in ('us', 'kr'):
        if mk == 'us':
            print('시장|코드|이름|업종(점수)|안정|적극|지금 TOP20|뽑힌 이유|매출 성장%|최근 분기 성장%|영업이익률%|EV/EBIT|PER|3개월%|고점 대비%|애널|13F|사건|노트')
        else:
            print('시장|코드|이름|업종(점수)|안정|적극|지금 TOP20|뽑힌 이유|매출 성장%|영업이익률%(업종)|EV/EBIT(업종)|PER|1개월%|6개월%|고점 대비%|외국인|기관|사건|노트')
        for r in (C.get(mk) or []):
            f = r.get('facts') or {}
            mem = {'stable': '안정형', 'aggressive': '적극형'}.get(r.get('member'), '-')
            why = '·'.join(w.replace(' 점수 상위', '').replace('(사건·애널·13F·동시 수급)', '') for w in r.get('why_pick') or [])
            ev = '; '.join('%s %s' % (e['date'][5:], e['kind']) for e in (r.get('events') or [])[:3]) or '-'
            sec = '%s(%s)' % (r.get('bucket') or '-', fmt(r.get('sector_score'), 0))
            if r.get('no_numbers'):
                print('%s|%s|%s|%s|-|-|%s|%s|숫자 없음(적자이거나 목록 밖) — 번들로 볼 것|%s|%s' % (mk, r['code'], r.get('name') or '', sec, mem, why, ev, note_info(mk, r['code']))); continue
            if mk == 'us':
                print('us|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s' % (
                    r['code'], r.get('name'), sec, fmt(r.get('stable'), 0), fmt(r.get('aggressive'), 0), mem, why,
                    fmt(f.get('rev_g')), fmt(f.get('rev_q_yoy')), fmt(f.get('op_margin')), fmt(f.get('ev_ebit')), fmt(f.get('per')),
                    fmt(f.get('r3m'), pct=True), fmt(f.get('hi52')), r.get('analysts') or 0, ','.join(r.get('f13') or []) or '-', ev, note_info(mk, r['code'])))
            else:
                print('kr|%s|%s|%s|%s|%s|%s|%s|%s|%s(%s)|%s(%s)|%s|%s|%s|%s|%s|%s|%s|%s' % (
                    r['code'], r.get('name'), sec, fmt(r.get('stable'), 0), fmt(r.get('aggressive'), 0), mem, why,
                    fmt(f.get('sales_growth')), fmt(f.get('opm_recent')), fmt(f.get('sector_opm')), fmt(f.get('ev_ebit')), fmt(f.get('sector_ev_ebit')),
                    fmt(f.get('per')), fmt(f.get('r1'), pct=True), fmt(f.get('r6'), pct=True), fmt(f.get('dd_close')),
                    f.get('f_buydays') or '-', f.get('p_buydays') or '-', ev, note_info(mk, r['code'])))
    try:
        E = json.load(open(os.path.join(ROOT, 'lists', 'events.json'), encoding='utf-8'))
        big = ('합병', '인수', '공개매수', '양수도', '분할', '지배권', '공급계약', '계약 해지', '파산', '정정', '상장 유지')
        print('\n## 중대 공시 사건 — 후보 밖 기업 포함(최근 3일). 업계 재편·경쟁 구도 변화로 같은 업종 종목을 다시 볼지 판단')
        for mk in ('us', 'kr'):
            for e in ((E.get(mk) or {}).get('items') or []):
                if any(b in e['kind'] for b in big):
                    print('- %s %s %s(%s) [%s] %s' % (mk, e['date'], e.get('name'), e['code'], e.get('sector') or '', e['kind'] + ((' — ' + e['title'][:60]) if e.get('title') else '')))
    except Exception:
        pass


def notes_queue(n=6):
    """노트 쓸 차례 — ① 오늘 편입된 종목 ② 사건이 난 지금 TOP 20 ③ 노트 없는 지금 TOP 20 ④ 노트 없는 새 후보 상위 (노트 쌓기 실행용)"""
    C = json.load(open(os.path.join(ROOT, 'lists', 'candidates.json'), encoding='utf-8'))
    P = json.load(open(os.path.join(ROOT, 'top20', 'picks.json'), encoding='utf-8'))
    rows = {(mk, r['code']): (mk, r) for mk in ('kr', 'us') for r in (C.get(mk) or [])}
    out, seen = [], set()
    def add(mk, code, why):
        if (mk, code) in seen: return
        r = (rows.get((mk, code)) or (mk, {'code': code}))[1]
        nd = note_info(mk, code)
        if why.startswith('사건') and nd not in ('없음', '있음'):
            if any(e['date'] <= nd for e in (r.get('events') or [])): return     # 사건 뒤에 이미 노트를 고쳤으면 건너뜀
        elif nd != '없음' and not why.startswith('사건'):
            return
        seen.add((mk, code)); out.append((mk, r, why, nd))
    for x in ((P.get('meta') or {}).get('moves') or []):
        if x.get('move') == '편입': add(x['mk'], x['code'], '오늘 편입')
    for mk in ('kr', 'us'):
        for r in (C.get(mk) or []):
            if r.get('member') and r.get('events'): add(mk, r['code'], '사건 — ' + ', '.join(e['kind'] for e in r['events'][:2]))
    for t in ('stable', 'aggressive'):
        for i in range(20):
            for mk in ('kr', 'us'):
                L = (P.get(mk) or {}).get(t) or []
                if i < len(L): add(mk, L[i]['code'], '지금 TOP 20(' + ('안정형' if t == 'stable' else '적극형') + ' %d위)' % (i + 1))
    for mk in ('kr', 'us'):
        for r in sorted([r for r in (C.get(mk) or []) if not r.get('member') and r.get('stable') is not None],
                        key=lambda r: -max(r.get('stable') or 0, r.get('aggressive') or 0))[:15]:
            add(mk, r['code'], '새 후보(점수 상위)')
    print('# 노트 쓸 차례 — 위에서 2개만 쓴다 (모은 시각 %s KST)' % dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M'))
    print('시장|코드|이름|왜 지금|업종|안정·적극 점수|사건|노트')
    for mk, r, why, nd in out[:n]:
        ev = '; '.join('%s %s' % (e['date'][5:], e['kind']) for e in (r.get('events') or [])[:3]) or '-'
        print('%s|%s|%s|%s|%s|%s·%s|%s|%s' % (mk, r.get('code'), r.get('name') or '', why, r.get('bucket') or '-', fmt(r.get('stable'), 0), fmt(r.get('aggressive'), 0), ev, nd))
    print('\n깊이 볼 때: python3 tools/bundle.py kr 종목코드 --brief --ver dev / python3 tools/bundle.py us 티커 --ver dev — 숫자는 lists/candidates.json의 facts')


def main():
    if '--notes' in sys.argv:
        return notes_queue()
    if '--sessions' in sys.argv:                                 # 판단을 고르기 전 빠른 확인(절차서 0-2)
        go, lines = session_check()
        print('## 새 거래 확인 — ' + ('판단함' if go else '판단 쉼'))
        print('\n'.join(lines))
        return 0
    now = dt.datetime.now(KST)
    print('# 매일 판단 입력 — 모은 시각 %s KST' % now.strftime('%Y-%m-%d %H:%M'))
    print('## 깔때기 재료 새로 받기')
    for l in refresh_materials():
        print('- ' + l)
    with ThreadPoolExecutor(4) as ex:
        fk = ex.submit(bundle, ['kr'] + IDX_KR + ['--brief']); fu = ex.submit(bundle, ['us'] + IDX_US)
        fn = [ex.submit(news, q) for q in NEWS_KO] + [ex.submit(news, q, True) for q in NEWS_EN]
        kr, us = fk.result(), fu.result()
        nw = [f.result() for f in fn]
    dash = dash_snapshot(kr, us)
    try:
        with open(DASH_TMP, 'w', encoding='utf-8') as f:
            json.dump(dash, f, ensure_ascii=False)
    except Exception as e:
        print('(지표 판 저장 실패 — %s)' % type(e).__name__)
    ss = dash.get('sessions') or {}
    print('\n## 거래일 — 국장 마지막 거래 %s · 미장 마지막 거래 %s. 각 시장의 숫자는 이 날짜 기준이다. 장이 열리지 않은 날(주말·휴장)의 이야기는 리포트에 쓰지 않는다'
          % (mdw(ss.get('kr')), mdw(ss.get('us'))))
    print('\n## 지표 판 — 사이트 시황 리포트 맨 위에 점수판으로 이대로 박힌다(코드가 %s KST에 모음, 변화는 전 거래일 대비).'
          ' 본문에서 이 숫자를 다시 읽어 주지 않는다. 아래 머리 줄의 같은 지표와 숫자가 다르면 이 줄이 기준' % dash['at'][11:])
    for g, lab in (('idx', '지수'), ('mac', '지표')):
        print(lab + ': ' + ' · '.join('%s %s%s' % (x['name'], x['value'], '(' + ', '.join(s for s in (x['chg'], x['sub']) if s) + ')' if (x['chg'] or x['sub']) else '')
                                    for x in dash['items'] if x.get('g') == g))
    print('\n## 거시·국장 시장 (번들 국장 머리 줄)')
    print('\n'.join(heads(kr, HEAD_KR)))
    print('\n## 미장 시장·심리·신용 (번들 미장 머리 줄)')
    print('\n'.join(heads(us, HEAD_US)))
    print('\n## 지수·자산 자료 카드 — 시황 종합 리포트와 같은 자료 (대용 ETF: KODEX 200=코스피 · KODEX 코스닥150=코스닥 · SPY=S&P 500 · '
          'IWM=러셀 2000 · QQQ=나스닥 · SOXX=필라델피아 반도체 · TLT=미국 장기 국채, 금·비트코인은 위 #미국시장 줄)')
    print('\n'.join(cards(kr, False) + cards(us, True)))
    print('\n## 지정학·정책 뉴스 제목(최근 2일)')
    for q, items in zip(NEWS_KO + NEWS_EN, nw):
        print('- %s: %s' % (q, ' / '.join(items) if items else '없음'))
    P = json.load(open(os.path.join(ROOT, 'top20', 'picks.json'), encoding='utf-8'))
    fd = {}
    try:
        fd = json.load(open(os.path.join(ROOT, 'cache', 'fundamentals.json'), encoding='utf-8')).get('data') or {}
    except Exception:
        pass
    rows = []
    for mk in ('kr', 'us'):
        for t in ('stable', 'aggressive'):
            for x in P[mk][t]:
                sym = (x['code'] + ('.KQ' if (fd.get('A' + x['code']) or {}).get('market') == 'KOSDAQ' else '.KS')) if mk == 'kr' else x['code'].replace('.', '-')
                rows.append((mk, t, x, sym))
    with ThreadPoolExecutor(8) as ex:
        px = list(ex.map(lambda r: price(r[3]), rows))
    print('\n## TOP 20 판단 종목 상태 — 판단일 %s (%s)' % (P['meta'].get('judged_at') or P['meta'].get('judged'), P['meta'].get('by', '')))
    print('시장|목록|코드|이름|확률|지금|1일%|5일%|판단 때 대비%|목표까지%|손절까지%|상태')
    for (mk, t, x, sym), p in zip(rows, px):
        nm = (fd.get('A' + x['code']) or {}).get('name', '') if mk == 'kr' else ''
        if not p:
            print('%s|%s|%s|%s|%s|확인 불가' % (mk, '안정형' if t == 'stable' else '적극형', x['code'], nm, x['p'])); continue
        cur, d1, d5 = p
        hit = '목표 도달' if cur >= x['target'] else ('손절 도달' if cur <= x['stop'] else '')
        f = lambda v: '%+.1f' % v if v is not None else '-'
        print('%s|%s|%s|%s|%d|%s|%s|%s|%s|%s|%s|%s' % (mk, '안정형' if t == 'stable' else '적극형', x['code'], nm, x['p'], round(cur, 2), f(d1), f(d5),
              f((cur / x['ref'] - 1) * 100 if x.get('ref') else None), f((x['target'] / cur - 1) * 100), f((x['stop'] / cur - 1) * 100), hit))
    print('\n## 지난 판단 요지')
    print('국장:', P['meta'].get('view_kr', '')); print('미장:', P['meta'].get('view_us', ''))
    print('지난 변경:', ' / '.join(P['meta'].get('changes') or []) or '없음')
    print_funnel()


if __name__ == '__main__':
    main()
