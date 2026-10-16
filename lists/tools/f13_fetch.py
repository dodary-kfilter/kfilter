#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""13F 공통 매매 — Dataroma 슈퍼투자자 분기 거래 내역을 모아 lists/13f/<분기>.json을 만든다.

사용(저장소 루트에서):
  python3 lists/tools/f13_fetch.py                   # 공시 마감이 지난 최신 분기를, 파일이 없을 때만 만든다
  python3 lists/tools/f13_fetch.py --quarter 2026Q3  # 분기를 지정한다
  python3 lists/tools/f13_fetch.py --dry             # 쓰지 않고 요약만 본다
  python3 lists/tools/f13_fetch.py --force           # 파일이 있어도 다시 만든다
  GitHub Actions(.github/workflows/f13.yml)가 매주 토요일 08:00(한국시간)에 돌린다.
  분기 공시 마감(분기 말 + 45일, 주말이면 다음 월요일) 뒤 첫 토요일에 한 번 만들고, 그 뒤엔 건너뛴다.

규칙(README와 같음)
- 출처: Dataroma 매니저 목록(managers.php)의 슈퍼투자자 전원, 매니저별 거래 내역(m_activity.php)의 해당 분기 부분
- 행동: Buy = 신규 편입, Add = 비중 확대, Reduce = 비중 축소, Sell = 청산
- 리스트 4개: 같은 행동을 한 인원 2명 이상, 인원순(동률이면 같은 방향 순매수·순매도 큰 순) 상위 10
- 매수:매도 = (신규+확대 인원) : (축소+청산 인원)
- 합의 강도(리스트에 오른 종목 중): 매수 우위 = 매수 비중 70% 이상, 매도 우위 = 30% 이하, 의견 분산 = 40~60%이고 10명 이상
- 제외: ETF, 분기 중 새로 상장된 종목(분사·신규 상장으로 기계적으로 편입된 물량) — 야후 시세의 상품 종류·첫 거래일로 가린다
- 분기 말 종가: 야후 일봉의 분기 말(그 날 이전 마지막 거래일) 종가
- 받지 못한 매니저가 10명을 넘으면 파일을 쓰지 않고 멈춘다
"""
import json, os, re, sys, time, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

try:                                   # GitHub 서버에선 브라우저 흉내가 있어야 막히지 않는 사이트가 있다
    from curl_cffi import requests as _cr
except Exception:
    _cr = None

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
DIR = os.path.join(ROOT, 'lists', '13f')
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
DR = 'https://www.dataroma.com/m/'
MIN_CNT, TOP_N, MAX_FAIL = 2, 10, 10
KST = dt.timezone(dt.timedelta(hours=9))
CATS = (('new', 'Buy'), ('add', 'Add'), ('reduce', 'Reduce'), ('exit', 'Sell'))


def fetch(url, as_json=False, tries=3):
    for i in range(tries):
        try:
            if _cr is not None:
                r = _cr.get(url, impersonate='chrome' if i % 2 == 0 else 'safari', timeout=30)
                if r.status_code == 200:
                    return r.json() if as_json else r.text
            else:
                req = urllib.request.Request(url, headers={'User-Agent': UA})
                b = urllib.request.urlopen(req, timeout=30).read()
                return json.loads(b) if as_json else b.decode('utf-8', 'replace')
        except Exception:
            pass
        time.sleep(1.5 * (i + 1))
    return None


def quarter_end(y, q):
    return dt.date(y, 3 * q, (31, 30, 30, 31)[q - 1])


def deadline(qe):
    d = qe + dt.timedelta(days=45)
    while d.weekday() >= 5:            # 주말이면 다음 월요일
        d += dt.timedelta(days=1)
    return d


def latest_due(today):
    """공시 마감 + 이틀(Dataroma 반영)이 지난 가장 최근 분기"""
    y, q = today.year, (today.month - 1) // 3 + 1
    for _ in range(8):
        if deadline(quarter_end(y, q)) + dt.timedelta(days=2) <= today:
            return y, q
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return None


def managers():
    t = fetch(DR + 'managers.php') or ''
    out = []
    for code, name in re.findall(r'href="/m/holdings\.php\?m=([^"&]+)"[^>]*>([^<]+)<', t):
        if code not in [c for c, _ in out]:
            out.append((code, name.strip()))
    return out


ROW = re.compile(r'sym=([^"&]+)">[^<]*<span>\s*-\s*([^<]*)</span></a></td>\s*<td class="(?:buy|sell)">\s*([A-Za-z]+)')


def activity(code, label):
    """매니저 한 명의 해당 분기 거래 → [(티커, 영문명, 행동)] · 분기 표시가 없으면 [] · 못 받으면 None"""
    t = fetch(DR + 'm_activity.php?m=%s&typ=a' % urllib.parse.quote(code))
    if t is None:
        return None
    heads = [(m.start(), '%s %s' % (m.group(1), m.group(2)))
             for m in re.finditer(r'<tr class="q_chg"><td[^>]*><b>(Q\d)</b>\s*&nbsp;?\s*<b>(\d{4})</b>', t)]
    for i, (pos, lab) in enumerate(heads):
        if lab == label:
            end = heads[i + 1][0] if i + 1 < len(heads) else len(t)
            return [(s.strip(), n.strip(), a) for s, n, a in ROW.findall(t[pos:end])]
    return []


def ymeta(tk, qe):
    """야후 일봉 → (상품 종류, 첫 거래일, 분기 말 종가)"""
    sym = tk.replace('.', '-')
    p1 = int(dt.datetime.combine(qe - dt.timedelta(days=12), dt.time(), dt.timezone.utc).timestamp())
    p2 = int(dt.datetime.combine(qe + dt.timedelta(days=1), dt.time(), dt.timezone.utc).timestamp())
    j = fetch('https://query1.finance.yahoo.com/v8/finance/chart/%s?period1=%d&period2=%d&interval=1d'
              % (urllib.parse.quote(sym), p1, p2), as_json=True)
    try:
        r = j['chart']['result'][0]; m = r.get('meta') or {}
        closes = [(ts, c) for ts, c in zip(r.get('timestamp') or [], r['indicators']['quote'][0]['close']) if c]
        px = round(closes[-1][1], 2) if closes else None
        ftd = m.get('firstTradeDate')
        ftd = dt.datetime.fromtimestamp(ftd, dt.timezone.utc).date() if ftd else None
        return m.get('instrumentType'), ftd, px
    except Exception:
        return None, None, None


def main(argv):
    dry, force = '--dry' in argv, '--force' in argv
    today = dt.datetime.now(KST).date()
    if '--quarter' in argv:
        m = re.match(r'^(\d{4})Q([1-4])$', argv[argv.index('--quarter') + 1])
        y, q = int(m.group(1)), int(m.group(2))
    else:
        yq = latest_due(today)
        if not yq:
            print('공시 마감이 지난 분기가 없음'); return 0
        y, q = yq
    qkey, label = '%dQ%d' % (y, q), 'Q%d %d' % (q, y)
    qe, qs = quarter_end(y, q), quarter_end(y, q - 1) if q > 1 else quarter_end(y - 1, 4)
    path = os.path.join(DIR, qkey + '.json')
    if os.path.exists(path) and not force and not dry:
        print('%s 파일이 이미 있음 — 건너뜀 (다음 분기 마감 뒤 첫 토요일에 새로 만든다)' % qkey); return 0

    mg = managers()
    if len(mg) < 50:
        sys.exit('★ 매니저 목록을 받지 못함(%d명) — 쓰지 않음' % len(mg))
    with ThreadPoolExecutor(4) as ex:
        acts = list(ex.map(lambda c: activity(c[0], label), mg))
    failed = [n for (c, n), a in zip(mg, acts) if a is None]
    if len(failed) > MAX_FAIL:
        sys.exit('★ 매니저 %d명의 거래 내역을 받지 못함 — 쓰지 않음' % len(failed))
    filed = sum(1 for a in acts if a)

    cnt, en = {}, {}
    for a in acts:
        seen = set()
        for tk, nm, act in a or []:
            cat = {'Buy': 'new', 'Add': 'add', 'Reduce': 'reduce', 'Sell': 'exit'}.get(act)
            if not cat or (tk, cat) in seen:
                continue
            seen.add((tk, cat))
            c = cnt.setdefault(tk, {'new': 0, 'add': 0, 'reduce': 0, 'exit': 0})
            c[cat] += 1
            en.setdefault(tk, nm)
    bs = {tk: (c['new'] + c['add'], c['reduce'] + c['exit']) for tk, c in cnt.items()}

    # 리스트 후보(같은 행동 2명 이상)만 야후로 상품 종류·첫 거래일·분기 말 종가를 확인
    cand = sorted(tk for tk, c in cnt.items() if max(c.values()) >= MIN_CNT)
    with ThreadPoolExecutor(6) as ex:
        meta = dict(zip(cand, ex.map(lambda tk: ymeta(tk, qe), cand)))
    excluded = []
    for tk in cand:
        typ, ftd, _ = meta[tk]
        if typ == 'ETF':
            excluded.append({'ticker': tk, 'reason': 'ETF'})
        elif ftd and qs < ftd <= qe:
            excluded.append({'ticker': tk, 'reason': '%s 상장 — 분기 중 새로 상장(분사·신규 상장)돼 기계적으로 편입된 물량' % ftd.isoformat()})
    exc = {e['ticker'] for e in excluded}

    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}
    pkey = 'price_%02d%02d' % (qe.month, qe.day)
    lists = {}
    for cat, _ in CATS:
        side = 0 if cat in ('new', 'add') else 1
        rows = [tk for tk in cand if tk not in exc and cnt[tk][cat] >= MIN_CNT]
        rows.sort(key=lambda tk: (-cnt[tk][cat], -((bs[tk][0] - bs[tk][1]) if side == 0 else (bs[tk][1] - bs[tk][0])), tk))
        lists[cat] = [{'ticker': tk, 'name_ko': names.get(tk, ''), 'name': en.get(tk, tk), 'count': cnt[tk][cat],
                       'buy': bs[tk][0], 'sell': bs[tk][1], pkey: meta[tk][2]} for tk in rows[:TOP_N]]

    pool = list(dict.fromkeys(x['ticker'] for cat, _ in CATS for x in lists[cat]))
    share = {tk: bs[tk][0] / (bs[tk][0] + bs[tk][1]) for tk in pool if sum(bs[tk])}
    chip = lambda tk: {'ticker': tk, 'name_ko': names.get(tk, ''), 'buy': bs[tk][0], 'sell': bs[tk][1]}
    buy_t = sorted([tk for tk in pool if share.get(tk, 0) >= 0.7], key=lambda tk: (-(bs[tk][0] - 2 * bs[tk][1]), -bs[tk][0], tk))
    sell_t = sorted([tk for tk in pool if share.get(tk, 1) <= 0.3], key=lambda tk: (-(bs[tk][1] - 2 * bs[tk][0]), -bs[tk][1], tk))
    split = sorted([tk for tk in pool if 0.4 <= share.get(tk, 0) <= 0.6 and sum(bs[tk]) >= 10], key=lambda tk: (-sum(bs[tk]), tk))

    out = {'quarter': qkey, 'as_of': qe.isoformat(), 'generated': today.isoformat(), 'tracked': len(mg), 'filed': filed,
           'source': 'Dataroma 슈퍼투자자 분기 거래 내역(m_activity.php)',
           'rules': {'count': '같은 행동을 한 슈퍼투자자 수', 'min_count': MIN_CNT, 'top_n': TOP_N,
                     'sort': '인원 내림차순, 동률이면 같은 방향 순매수(순매도) 큰 순', 'buy_sell': 'buy=신규+확대 인원, sell=축소+청산 인원',
                     'consensus': '리스트 종목 중 매수 비중 70% 이상=매수 우위, 30% 이하=매도 우위, 40~60%이고 10명 이상=의견 분산',
                     'exclude': 'ETF, 분기 중 신규 상장(분사·IPO)'},
           'lists': lists,
           'consensus': {'buy_tilt': [chip(tk) for tk in buy_t[:8]], 'sell_tilt': [chip(tk) for tk in sell_t[:8]],
                         'split': [chip(tk) for tk in split[:5]]},
           'excluded': excluded, 'counts_all': {tk: cnt[tk] for tk in sorted(cnt)}, 'failed': failed}

    print('%s(%s 보유) · 슈퍼투자자 %d명 중 이 분기 거래 %d명 · 못 받음 %s' % (qkey, qe, len(mg), filed, failed or '없음'))
    for cat, lab in (('new', '신규 편입'), ('add', '비중 확대'), ('reduce', '비중 축소'), ('exit', '청산')):
        print(' %s: %s' % (lab, ', '.join('%s %d(%d:%d)' % (x['ticker'], x['count'], x['buy'], x['sell']) for x in lists[cat])))
    print(' 매수 우위: %s' % ', '.join('%s %d:%d' % (c['ticker'], c['buy'], c['sell']) for c in out['consensus']['buy_tilt']))
    print(' 매도 우위: %s' % ', '.join('%s %d:%d' % (c['ticker'], c['buy'], c['sell']) for c in out['consensus']['sell_tilt']))
    print(' 의견 분산: %s' % ', '.join('%s %d:%d' % (c['ticker'], c['buy'], c['sell']) for c in out['consensus']['split']))
    print(' 제외: %s' % (', '.join('%s(%s)' % (e['ticker'], e['reason'][:12]) for e in excluded) or '없음'))
    miss = sorted({x['ticker'] for cat, _ in CATS for x in lists[cat]} - set(names))
    print(' 한글명이 없는 티커 %d개: %s' % (len(miss), ', '.join(miss)))
    if dry:
        return 0
    os.makedirs(DIR, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1); f.write('\n')
    ipath = os.path.join(DIR, 'index.json')
    ix = json.load(open(ipath, encoding='utf-8')) if os.path.exists(ipath) else {'latest': qkey, 'quarters': []}
    ix['quarters'] = sorted(set(ix.get('quarters', [])) | {qkey})
    ix['latest'] = max(ix['quarters'])
    with open(ipath, 'w', encoding='utf-8') as f:
        json.dump(ix, f, ensure_ascii=False); f.write('\n')
    print('저장:', os.path.relpath(path, ROOT), '· index.json latest =', ix['latest'])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
