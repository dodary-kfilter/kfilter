# -*- coding: utf-8 -*-
"""매일 판단 입력 — 거시·시장·뉴스·TOP 20 종목 상태를 한 번에 모은다 (tools/daily_judge.md 절차의 1단계)

사용(저장소 루트): python3 tools/daily_collect.py > /tmp/today.txt      (판단 실행)
                  python3 tools/daily_collect.py --notes                (노트 쌓기 실행 — 노트 쓸 차례 6개만)
  - 번들(tools/bundle.py)을 국장·미장 한 번씩 돌려 머리 줄(#거시·#시장·#미국시장·#미국 심리·신용·#환율·유가·시장 뉴스)만 가져온다
  - 지정학·정책 뉴스 제목(최근 2일): 휴전·공습·관세·금리·수출 규제 등 — 판단을 바꿀 사건을 놓치지 않으려고
  - TOP 20 판단 종목(top20/picks.json) 80개의 지금 가격·1일·5일 등락·판단 때 대비·목표까지·손절까지·도달 여부
  - 깔때기 재료(2026-11-03): 재료가 오래됐으면 여기서 새로 받고(업종 추세·공시 사건은 매번, 미장 재무는 20시간 넘으면),
    거르기(tools/funnel_screen.py)를 돌려 업종 점수·후보 표·공시 사건·기업 노트 현황을 붙인다
받지 못한 것은 '확인 불가'로 적고 멈추지 않는다.
"""
import html, json, os, re, subprocess, sys, time, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
KST = dt.timezone(dt.timedelta(hours=9))
HEAD_KR = ('#거시', '물가 ·', '경기 ·', '정책 ·', '지정학 ·', '일정(2주)', '거시 수집 오류', '#시장 ', '#해외·환율', '#미국 금리·달러', '#시장 뉴스')
HEAD_US = ('#미국시장', '#미국 심리·신용', '#환율·유가', '#시장 뉴스')
NEWS_KO = ['휴전', '이란 공습', '관세', '연준 금리', '한국은행 금리', '반도체 수출 규제', '코스피 외국인']
NEWS_EN = ['ceasefire', 'Fed rate decision', 'tariffs', 'AI capex', 'oil prices']


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
    now = dt.datetime.now(KST)
    print('# 매일 판단 입력 — 모은 시각 %s KST' % now.strftime('%Y-%m-%d %H:%M'))
    print('## 깔때기 재료 새로 받기')
    for l in refresh_materials():
        print('- ' + l)
    with ThreadPoolExecutor(4) as ex:
        fk = ex.submit(bundle, ['kr', '069500', '--brief']); fu = ex.submit(bundle, ['us', 'SPY'])
        fn = [ex.submit(news, q) for q in NEWS_KO] + [ex.submit(news, q, True) for q in NEWS_EN]
        kr, us = fk.result(), fu.result()
        nw = [f.result() for f in fn]
    print('\n## 거시·국장 시장 (번들 국장 머리 줄)')
    print('\n'.join(heads(kr, HEAD_KR)))
    print('\n## 미장 시장·심리·신용 (번들 미장 머리 줄)')
    print('\n'.join(heads(us, HEAD_US)))
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
