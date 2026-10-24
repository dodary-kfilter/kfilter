# -*- coding: utf-8 -*-
"""매일 판단 입력 — 거시·시장·뉴스·TOP 20 종목 상태를 한 번에 모은다 (tools/daily_judge.md 절차의 1단계)

사용(저장소 루트): python3 tools/daily_collect.py > /tmp/today.txt
  - 번들(tools/bundle.py)을 국장·미장 한 번씩 돌려 머리 줄(#거시·#시장·#미국시장·#미국 심리·신용·#환율·유가·시장 뉴스)만 가져온다
  - 지정학·정책 뉴스 제목(최근 2일): 휴전·공습·관세·금리·수출 규제 등 — 판단을 바꿀 사건을 놓치지 않으려고
  - TOP 20 판단 종목(top20/picks.json) 80개의 지금 가격·1일·5일 등락·판단 때 대비·목표까지·손절까지·도달 여부
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


def main():
    now = dt.datetime.now(KST)
    print('# 매일 판단 입력 — 모은 시각 %s KST' % now.strftime('%Y-%m-%d %H:%M'))
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


if __name__ == '__main__':
    main()
