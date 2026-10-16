#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""월가 상위 애널리스트 추천 — TipRanks에서 직접 받아 lists/analyst.json을 만든다.

사용(저장소 루트에서):
  python3 lists/tools/analyst_fetch.py              # analyst.json을 쓴다
  python3 lists/tools/analyst_fetch.py --history    # 기록(history/analyst/날짜.json)도 함께 남긴다 — 주 1회
  python3 lists/tools/analyst_fetch.py --dry        # 쓰지 않고 요약만 본다
  GitHub Actions(.github/workflows/analyst.yml)가 매일 07:00(한국시간)에 돌리고, 토요일엔 기록도 남긴다

- 순위표: TipRanks 애널리스트 순위 상위 100명 (api/experts/getTop)
- 의견: 애널리스트별 담당 종목의 최신 의견 (api/experts/getStocks)
  최근 28일(오늘 포함) 안에 낸 매수(Buy) 의견만, 미국 상장(티커에 ':' 없음)·거래 중인 종목만 쓴다
- 랭킹순 화면 = 상위 25위 각자의 매수 의견 / 추천 수순 화면 = 상위 100위 중 2명 이상이 매수한 종목
- 한글 종목명은 lists/names_ko.json(티커 → 한글명)에서 붙인다. 없으면 영문명을 쓴다
- TipRanks 웹 페이지는 Cloudflare로 막혀 있고 파이썬 urllib도 막힌다 → curl로 API만 부른다
- 순위표를 못 받거나 애널리스트 10% 넘게 못 받으면 기존 파일을 덮지 않고 멈춘다
"""
import json, os, re, subprocess, sys, datetime as dt, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = os.path.join(ROOT, 'lists', 'analyst.json')
HIST = os.path.join(ROOT, 'lists', 'history', 'analyst')
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
API = 'https://www.tipranks.com/api/experts/'
N_POOL, N_RANK, MIN_CNT, DAYS = 100, 25, 2, 28
KST = dt.timezone(dt.timedelta(hours=9))


LAST = {}       # 마지막 실패의 HTTP 상태·응답 앞부분 — 막혔을 때 원인을 로그에 남긴다


def get(url, tries=2):
    for _ in range(tries):
        r = subprocess.run(['curl', '-s', '-L', '-A', UA, '-H', 'Accept: application/json', '--max-time', '30',
                            '-w', '\n%{http_code}', url], capture_output=True)
        body, _, code = r.stdout.rpartition(b'\n')
        try:
            return json.loads(body)
        except Exception:
            LAST.update(code=code.decode(errors='replace'), head=body[:160].decode(errors='replace').replace('\n', ' '))
    return None


def slugs(name):
    # 프로필 주소 규칙: 소문자, 공백 → '-', 따옴표는 그대로 두고 %27로 (예: kevin-o%27halloran)
    b = re.sub(r'\s+', '-', name.strip().lower())
    cands = [b, b.replace("'", ''), b.replace('.', ''), re.sub(r"[^a-z0-9'-]+", '-', b).strip('-')]
    return [urllib.parse.quote(c, safe='-') for c in dict.fromkeys(cands)]


def stocks_of(a):
    for s in slugs(a['name']):
        d = get(API + 'getStocks/?name=' + s)
        if isinstance(d, list):
            return d
    return None


def main(argv):
    dry = '--dry' in argv
    keep_hist = '--history' in argv
    today = dt.datetime.now(KST).date()
    cut = (today - dt.timedelta(days=DAYS - 1)).isoformat()
    top = get(API + 'getTop/?expertType=analyst&numExperts=%d' % N_POOL)
    if not isinstance(top, list) or not top:
        sys.exit('★ 순위표(getTop)를 받지 못함 — 기존 파일 유지 (HTTP %s · %s)' % (LAST.get('code'), LAST.get('head')))
    top = sorted(top, key=lambda a: a['rank'])[:N_POOL]
    with ThreadPoolExecutor(10) as ex:
        got = list(ex.map(stocks_of, top))
    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}

    analysts, failed, agg = [], [], {}
    for a, d in zip(top, got):
        if d is None:
            failed.append(a['name'])
            continue
        picks = []
        for s in d:
            lr = s.get('latestRating') or {}
            tk = (s.get('ticker') or '').strip()
            date = (lr.get('ratingDate') or '')[:10]
            if lr.get('rating') != 'Buy' or date < cut or not tk or ':' in tk or s.get('isTraded') is False:
                continue
            picks.append({'ticker': tk, 'name': (s.get('name') or lr.get('stockName') or tk).strip(), 'date': date,
                          'action': lr.get('action') or '', 'pt': lr.get('priceTarget'), 'pt_prev': s.get('prevPriceTarget'),
                          'link': lr.get('link') or ''})
        picks.sort(key=lambda p: (p['date'], p['ticker']), reverse=True)            # 최신 의견 먼저
        tot = a.get('totalRecommendations') or 0
        rec = {'rank': a['rank'], 'name': a['name'].strip(), 'company': (a.get('company') or '').strip(),
               'success_pct': round(100 * (a.get('goodRecommendations') or 0) / tot) if tot else None,
               'avg_return_pct': round(100 * a['profit'], 1) if a.get('profit') is not None else None,
               'ratings': tot, 'picks': picks}
        if rec['rank'] > N_RANK:                                                     # 기사 주소는 랭킹순 화면(상위 25위)에서만 쓴다
            for p in picks: p.pop('link', None)
        analysts.append(rec)
        for p in picks:
            g = agg.setdefault(p['ticker'], {'ticker': p['ticker'], 'name': p['name'], 'by': []})
            g['by'].append({'rank': rec['rank'], 'name': rec['name'], 'company': rec['company'], 'date': p['date'],
                            'action': p['action'], 'pt': p['pt']})
    if len(failed) > N_POOL // 10:
        sys.exit('★ 애널리스트 %d명을 받지 못함(%s…) — 기존 파일 유지' % (len(failed), ', '.join(failed[:5])))
    for g in agg.values():
        g['by'].sort(key=lambda b: b['rank'])
        g['count'] = len(g['by'])
        g['best_rank'] = g['by'][0]['rank']
        g['latest'] = max(b['date'] for b in g['by'])
        g['name_ko'] = names.get(g['ticker'], '')
    stocks = sorted(agg.values(), key=lambda g: (-g['count'], g['best_rank'], g['ticker']))
    out = {'version': 2, 'updated': today.isoformat(), 'fetched_at': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M KST'),
           'window': {'from': cut, 'to': today.isoformat(), 'days': DAYS},
           'source': 'TipRanks 애널리스트 순위와 애널리스트별 최신 의견', 'analysts_tracked': 'TipRanks 약 12,500명',
           'rules': {'rank_view': '상위 %d위 애널리스트 각자의 최근 %d일 매수 의견' % (N_RANK, DAYS),
                     'count_view': '상위 %d위 중 %d명 이상이 최근 %d일에 매수 의견을 낸 종목, 인원순' % (N_POOL, MIN_CNT, DAYS),
                     'scope': '미국 상장·거래 중 종목, 최신 의견이 매수(Buy)인 것만',
                     'pool': N_POOL, 'rank_top': N_RANK, 'min_count': MIN_CNT},
           'analysts': analysts, 'stocks': stocks, 'failed': failed}

    rank_rows = [p for a in analysts if a['rank'] <= N_RANK for p in a['picks']]
    multi = [g for g in stocks if g['count'] >= MIN_CNT]
    print('기준 %s~%s · 순위표 %d명 · 못 받음 %s' % (cut, today, len(top), failed or '없음'))
    print('랭킹순(상위 %d위): 매수 의견 %d건 · 종목 %d개' % (N_RANK, len(rank_rows), len({p['ticker'] for p in rank_rows})))
    print('추천 수순(상위 %d위 중 %d명 이상): %d종목 — %s' % (N_POOL, MIN_CNT, len(multi),
          ', '.join('%s %d명' % (g['ticker'], g['count']) for g in multi[:10])))
    miss = sorted(({p['ticker'] for p in rank_rows} | {g['ticker'] for g in multi}) - set(names))
    print('화면에 나오는데 한글명이 없는 티커 %d개: %s' % (len(miss), ', '.join(miss)))
    if dry:
        return 0
    paths = [OUT]
    if keep_hist:                                                                      # 매일 쌓으면 저장소가 불어서 기록은 주 1회만
        os.makedirs(HIST, exist_ok=True)
        paths.append(os.path.join(HIST, today.isoformat() + '.json'))
    for path in paths:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
            f.write('\n')
    print('저장:', ' · '.join(os.path.relpath(p, ROOT) for p in paths))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
