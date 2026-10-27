# -*- coding: utf-8 -*-
"""미장 한글 종목명 — 토스 표기 하나로 통일 (lists/names_ko.json, 티커 → 한글명)

사용(저장소 루트): python3 tools/names_fetch.py --all --apply     # 전부 토스 표기로 다시 받고 자료 파일에도 바로 반영
                 python3 tools/names_fetch.py --apply           # 사전에 없는 티커만 받기
                 python3 tools/names_fetch.py --dry             # 받기만 하고 쓰지 않음
GitHub Actions(.github/workflows/names.yml)가 매주 일요일 05:00(한국시간)에 --all --apply로 돌린다.

왜: 사전을 목록 만들 때마다 손으로 채워 S&P 500 503개 중 126개만 한글이었고, 표기도 제각각이었다
    (AMD/어드밴스드마이크로디바이시스, 알파벳/알파벳 A, JP모건/제이피모건 체이스, 일라이릴리/일라이 릴리 …)
    → 많은 사람이 주식 앱에서 보는 토스 표기 하나로 맞춘다. 네이버는 자료 수집에서 막아 둔 곳이라 쓰지 않는다.
대상 티커: lists/mcap.json(S&P 500·나스닥 100) + top20/picks.json(미장) + lists/analyst.json + lists/13f 최신 분기 + 기존 사전
토스에서 못 받은 티커는 기존 이름을 그대로 둔다(기존에도 없으면 화면에 영어 이름).
--apply: 사전을 지금 자료 파일(시총순·TOP 20·애널 추천·13F)의 name_ko에 바로 반영 — 각 수집기가 다음에 돌 때까지 기다리지 않게
"""
import glob, json, os, re, sys, time, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
TOSS = 'https://wts-info-api.tossinvest.com/api/v2/stock-infos/code-or-symbol/%s'


def rd(p):
    try:
        return json.load(open(os.path.join(ROOT, p), encoding='utf-8'))
    except Exception:
        return None


def tickers():
    s = set()
    m = rd('lists/mcap.json') or {}
    for k in ('sp500', 'ndx'):
        s |= {r['code'] for r in m.get(k) or []}
    p = rd('top20/picks.json') or {}
    for t in ('stable', 'aggressive'):
        s |= {x['code'] for x in ((p.get('us') or {}).get(t) or [])}
    a = rd('lists/analyst.json') or {}
    s |= {g['ticker'] for g in a.get('stocks') or []}
    for an in a.get('analysts') or []:
        s |= {x.get('ticker') for x in an.get('picks') or [] if x.get('ticker')}
    f = latest13f()
    if f:
        d = rd(f) or {}
        for L in (d.get('lists') or {}).values():
            s |= {x['ticker'] for x in L}
        for L in (d.get('consensus') or {}).values():
            if isinstance(L, list):
                s |= {x['ticker'] for x in L if isinstance(x, dict) and x.get('ticker')}
    s |= set((rd('lists/names_ko.json') or {}).keys())
    return sorted(x for x in s if x and re.fullmatch(r'[A-Z][A-Z0-9.]{0,6}', x))   # 사전의 검색용 별칭(예: 'JP모건': 'jpmorgan')은 티커가 아니라 뺀다


def latest13f():
    ix = rd('lists/13f/index.json') or {}
    p = 'lists/13f/%s.json' % ix.get('latest') if ix.get('latest') else None
    return p if p and os.path.exists(os.path.join(ROOT, p)) else None


def toss(sym):
    for i in range(3):
        try:
            req = urllib.request.Request(TOSS % sym, headers={'User-Agent': UA, 'Accept': 'application/json',
                                                             'Origin': 'https://www.tossinvest.com', 'Referer': 'https://www.tossinvest.com/'})
            r = (json.loads(urllib.request.urlopen(req, timeout=15).read()).get('result') or {})
            nm = (r.get('name') or '').strip()
            return nm or None
        except Exception:
            time.sleep(1 + i)
    return None


def apply(names):
    """사전을 자료 파일의 name_ko에 반영 — 바뀐 파일 목록을 돌려준다"""
    changed = []
    def fix(rows, key):
        n = 0
        for r in rows or []:
            if isinstance(r, dict) and r.get(key) in names and r.get('name_ko') != names[r[key]]:
                r['name_ko'] = names[r[key]]; n += 1
        return n
    jobs = [('lists/mcap.json', lambda d: fix(d.get('sp500'), 'code') + fix(d.get('ndx'), 'code'), (',', ':')),
            ('top20/top20.json', lambda d: sum(fix((d.get('us') or {}).get(t), 'code') for t in ('stable', 'aggressive')), None),
            ('lists/analyst.json', lambda d: fix(d.get('stocks'), 'ticker'), None)]
    f = latest13f()
    if f:
        jobs.append((f, lambda d: sum(fix(L, 'ticker') for L in (d.get('lists') or {}).values())
                     + sum(fix(L, 'ticker') for L in (d.get('consensus') or {}).values() if isinstance(L, list)), None))
    for path, fn, sep in jobs:
        d = rd(path)
        if not d:
            continue
        n = fn(d)
        if n:
            raw = open(os.path.join(ROOT, path), encoding='utf-8').read()
            ind = None if sep else (1 if raw.startswith('{\n ') and not raw.startswith('{\n  ') else 2)
            with open(os.path.join(ROOT, path), 'w', encoding='utf-8') as fp:
                if sep:
                    json.dump(d, fp, ensure_ascii=False, separators=sep)
                else:
                    json.dump(d, fp, ensure_ascii=False, indent=ind)
                fp.write('\n')
            changed.append('%s(%d)' % (path, n))
    return changed


def main(argv):
    dry, full, app = '--dry' in argv, '--all' in argv, '--apply' in argv
    old = rd('lists/names_ko.json') or {}
    ts = tickers()
    want = ts if full else [t for t in ts if t not in old]
    print('대상 티커 %d개 · 받을 것 %d개' % (len(ts), len(want)))
    got = {}
    def one(t):
        nm = toss(t); time.sleep(0.15); return t, nm
    with ThreadPoolExecutor(4) as ex:
        for t, nm in ex.map(one, want):
            if nm:
                got[t] = nm
    miss = [t for t in want if t not in got]
    new = dict(old); new.update(got)
    diff = [(t, old.get(t), got[t]) for t in got if old.get(t) != got[t]]
    print('받음 %d · 못 받음 %d%s · 바뀐 이름 %d' % (len(got), len(miss), (' (%s…)' % ', '.join(miss[:8])) if miss else '', len(diff)))
    for t, a, b in diff[:15]:
        print('  %-6s %s → %s' % (t, a or '(없음)', b))
    if len(want) >= 50 and len(got) < 0.5 * len(want):
        sys.exit('★ 절반도 받지 못함 — 토스가 막혔을 수 있어 쓰지 않음')
    if dry:
        return 0
    with open(NAMES, 'w', encoding='utf-8') as f:
        json.dump(dict(sorted(new.items())), f, ensure_ascii=False, indent=1); f.write('\n')
    print('저장: lists/names_ko.json %d개' % len(new))
    if app:
        print('자료 파일 반영:', ', '.join(apply(new)) or '바뀐 것 없음')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
