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
- TipRanks 웹 페이지는 Cloudflare로 막혀 있고 파이썬 urllib도 막힌다 → API만 부른다.
  curl_cffi(브라우저 흉내)가 있으면 그걸로, 없으면 curl로 — GitHub 서버에선 curl도 막혀 curl_cffi가 필요하다
- 순위표를 못 받거나 애널리스트 10% 넘게 못 받으면 기존 파일을 덮지 않고 멈춘다
- 랭킹순(상위 25위)에서 보여 줄 의견이 0건인 애널리스트는 왜 비었는지 한 줄(why)과 설명 한 줄(why_more)을 남긴다
  (미국 밖 상장만 매수 / 마지막 매수가 28일 밖 / 매수 없이 보유·매도만 / 의견 없음 / 자료를 못 받음)
"""
import json, os, re, subprocess, sys, time, datetime as dt, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = os.path.join(ROOT, 'lists', 'analyst.json')
HIST = os.path.join(ROOT, 'lists', 'history', 'analyst')
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'
API = 'https://www.tipranks.com/api/experts/'
N_POOL, N_RANK, MIN_CNT, DAYS = 100, 25, 2, 28
KST = dt.timezone(dt.timedelta(hours=9))
COUNTRY = {'GB': '영국', 'DE': '독일', 'FR': '프랑스', 'IT': '이탈리아', 'ES': '스페인', 'NL': '네덜란드', 'CH': '스위스',
           'SE': '스웨덴', 'NO': '노르웨이', 'DK': '덴마크', 'FI': '핀란드', 'BE': '벨기에', 'IE': '아일랜드', 'AT': '오스트리아',
           'PT': '포르투갈', 'PL': '폴란드', 'CA': '캐나다', 'AU': '호주', 'JP': '일본', 'HK': '홍콩', 'CN': '중국', 'KR': '한국',
           'TW': '대만', 'SG': '싱가포르', 'IN': '인도', 'IL': '이스라엘', 'BR': '브라질', 'MX': '멕시코', 'ZA': '남아공',
           'TSE': '캐나다', 'TSX': '캐나다', 'TSXV': '캐나다', 'CVE': '캐나다', 'ASX': '호주', 'LSE': '영국', 'LON': '영국',
           'TYO': '일본', 'SHA': '중국', 'SHE': '중국', 'TLV': '이스라엘'}   # 나라 코드 또는 거래소 코드(TSE=토론토)
_SUFFIX = re.compile(r'(,?\s+(Ltd\.?|Limited|S\.p\.A\.|S\.A\.|SA|AG|SE|N\.V\.|plc|PLC|Inc\.?|Corporation|Corp\.?))+$')


def _md(d):
    return '%d/%d' % (int(d[5:7]), int(d[8:10]))


def _short(n):
    return _SUFFIX.sub('', (n or '').strip()) or (n or '')


def why_empty(d, cut):
    """보여 줄 매수 의견이 0건일 때 — (이유 한 줄, 설명 한 줄)"""
    rows = []
    for s in d:
        lr = s.get('latestRating') or {}
        rows.append((lr.get('rating') or '', (lr.get('ratingDate') or '')[:10], (s.get('ticker') or '').strip(),
                     _short(s.get('name') or lr.get('stockName')), s.get('isTraded')))
    rows.sort(key=lambda r: r[1], reverse=True)
    recent = [r for r in rows if r[1] >= cut]
    rb = [r for r in recent if r[0] == 'Buy']
    if rb:
        nonus = [r for r in rb if ':' in r[2]]
        if nonus:
            ex = ', '.join('%s(%s)' % (r[3], COUNTRY.get(r[2].split(':')[0], r[2].split(':')[0])) for r in nonus[:3])
            who = ('모두 ' if len(rb) > 1 else '') if len(nonus) == len(rb) else '%d건이 ' % len(nonus)
            return ('최근 %d일 매수 %d건 — %s미국 밖 상장이라 제외' % (DAYS, len(rb), who),
                    '%s%s. 리포트 자료가 미국 상장 종목만 받아서 뺀다.' % (ex, ' 등' if len(nonus) > 3 else ''))
        return ('최근 %d일 매수 %d건 — 거래가 멈춘 종목이라 제외' % (DAYS, len(rb)),
                '%s%s(인수·상장폐지 등). 지금 살 수 없는 종목이라 뺀다.' % (', '.join(r[3] for r in rb[:3]), ' 등' if len(rb) > 3 else ''))
    ob = [r for r in rows if r[0] == 'Buy' and r[2] and ':' not in r[2] and r[4] is not False]
    if ob:
        last = ob[0][1]
        same = [r for r in ob if r[1] == last][:3]
        return ('마지막 매수 의견 %s — %d일 기준(%s~) 밖' % (_md(last), DAYS, _md(cut)),
                '%s%s. %d일이 지난 의견은 그사이 주가·실적이 바뀌어 지금 판단과 어긋날 수 있어 뺀다.'
                % (', '.join('%s(%s)' % (r[3], r[2]) for r in same), ' 등' if len([r for r in ob if r[1] == last]) > 3 else '', DAYS))
    if recent:
        ko = {'Hold': '보유', 'Sell': '매도'}
        return ('최근 %d일 의견 %d건 — 매수는 없음' % (DAYS, len(recent)),
                '%s%s. 매수 의견만 모으는 목록이라 뺀다.' % (', '.join('%s %s' % (r[3], ko.get(r[0], r[0])) for r in recent[:3]), ' 등' if len(recent) > 3 else ''))
    return ('최근 의견 없음', '담당 종목에 %d일 동안 새 의견을 내지 않았다.' % DAYS)


LAST = {}       # 마지막 실패의 HTTP 상태·응답 앞부분 — 막혔을 때 원인을 로그에 남긴다


try:                   # GitHub 서버에선 TipRanks가 일반 curl을 Cloudflare로 막는다(403 'Just a moment') — 브라우저 흉내로 받는다
    from curl_cffi import requests as _cr
except Exception:
    _cr = None


def get(url, tries=3):
    for i in range(tries):
        if i:
            time.sleep(1.0 * i)        # 몰아서 부르면 Cloudflare가 일부를 막는다(실측: 10개 동시에 30명 실패) — 쉬었다 다시
        if _cr is not None:
            try:
                r = _cr.get(url, impersonate='chrome' if i == 0 else 'safari', timeout=30, headers={'Accept': 'application/json'})
                try:
                    return r.json()
                except Exception:
                    LAST.update(code=str(r.status_code), head=r.text[:160].replace('\n', ' '))
                    continue
            except Exception as e:
                LAST.update(code='ERR', head=str(e)[:160])
                continue
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
    with ThreadPoolExecutor(4) as ex:
        got = list(ex.map(stocks_of, top))
    for i, a in enumerate(top):                      # 못 받은 애널리스트는 한 명씩 천천히 다시
        if got[i] is None:
            time.sleep(1.5)
            got[i] = stocks_of(a)
    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}

    analysts, failed, agg = [], [], {}
    for a, d in zip(top, got):
        if d is None:
            failed.append(a['name'])
            if a['rank'] <= N_RANK:      # 랭킹순 자리를 비우지 않고 이유를 보인다
                analysts.append({'rank': a['rank'], 'name': a['name'].strip(), 'company': (a.get('company') or '').strip(),
                                 'success_pct': None, 'avg_return_pct': None, 'ratings': a.get('totalRecommendations') or 0,
                                 'picks': [], 'failed': True, 'why': '자료를 받지 못함',
                                 'why_more': 'TipRanks 접속이 잠깐 막혀 이번 갱신에서 빠졌다. 다음 갱신(매일 07:00)에서 다시 받는다.'})
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
        elif not picks:                                                              # 랭킹순에서 비는 자리 — 이유와 설명
            rec['why'], rec['why_more'] = why_empty(d, cut)
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
