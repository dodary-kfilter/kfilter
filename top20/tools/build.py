# -*- coding: utf-8 -*-
"""TOP 20 v4 — 애널리스트 판단 목록(안정형 20·적극형 20)을 지금 가격으로 다시 계산

사용(저장소 루트): python3 top20/tools/build.py           # top20/top20.json을 새로 만든다
                 python3 top20/tools/build.py --dry     # 쓰지 않고 요약만
GitHub Actions(.github/workflows/top20.yml)가 매주 토요일 08:30(한국시간)에 돌린다 — 판단(picks.json)은 그대로, 가격만 새로.

판단(top20/picks.json — 채팅에서 Claude가 애널리스트로서 고른다)
  - 안정형: 크게 흔들리지 않으면서 오를 가능성이 분명한 종목 / 적극형: 위험을 감수하되 맞으면 크게 오를 종목
  - 종목마다 상승 확률(p), 목표가(target), 손절가(stop, 이 판단이 틀렸다고 볼 가격), 권하는 이유 셋(why), 핵심 위험(risk)
계산
  - 오를 폭 = 목표가 ÷ 지금 가격 − 1, 내릴 폭 = 손절가 ÷ 지금 가격 − 1
  - 기대 수익 = 상승 확률 × 오를 폭 + 하락 확률 × 내릴 폭
  - 순서: 안정형은 상승 확률 높은 순, 적극형은 기대 수익 큰 순
  - 지금 가격이 목표가 이상이면 '목표 도달', 손절가 이하면 '손절 도달' — 다음 판단 때 바꿀 신호
  - 점검: 목표까지의 폭이 이 종목의 평소 석 달 움직임(1년 일봉의 흔들림)의 몇 배인지 — 판단이 현실적인지 보는 참고(순서에는 쓰지 않음)
(이전 v3의 공식 계산기는 top20/tools/build_v3_formula.py — 2026-10-24 애널리스트 판단 방식으로 바꾸며 쓰지 않음)
"""
import json, math, os, sys, time, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = os.path.join(ROOT, 'top20', 'top20.json')
PICKS = os.path.join(ROOT, 'top20', 'picks.json')
FUND = os.path.join(ROOT, 'cache', 'fundamentals.json')
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
KST = dt.timezone(dt.timedelta(hours=9))


def get(url, tries=3):
    for i in range(tries):
        try:
            return json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=25).read())
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


def daily(sym):
    j = get('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d' % urllib.parse.quote(sym))
    try:
        r = j['chart']['result'][0]; q = r['indicators']['quote'][0]; close = list(q['close']); m = r.get('meta') or {}
        if close and close[-1] is None and m.get('regularMarketPrice'):
            close[-1] = m['regularMarketPrice']
        c = [(dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y-%m-%d'), v) for ts, v in zip(r['timestamp'], close) if v]
        return c, m.get('longName') or m.get('shortName') or ''
    except Exception:
        return None, ''


def main(argv):
    dry = '--dry' in argv
    P = json.load(open(PICKS, encoding='utf-8'))
    fd = (json.load(open(FUND, encoding='utf-8')).get('data') or {}) if os.path.exists(FUND) else {}
    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}
    syms = {}
    for mk in ('kr', 'us'):
        for t in ('stable', 'aggressive'):
            for x in P[mk][t]:
                if mk == 'kr':
                    f = fd.get('A' + x['code']) or {}
                    syms[(mk, x['code'])] = x['code'] + ('.KQ' if f.get('market') == 'KOSDAQ' else '.KS')
                else:
                    syms[(mk, x['code'])] = x['code'].replace('.', '-')
    with ThreadPoolExecutor(6) as ex:
        D = dict(zip(syms, ex.map(daily, syms.values())))
    miss, last = [], {}
    res = {}
    for mk in ('kr', 'us'):
        res[mk] = {}
        for t in ('stable', 'aggressive'):
            rows = []
            for x in P[mk][t]:
                d, longname = D.get((mk, x['code'])) or (None, '')
                if not d:
                    miss.append(x['code']); continue
                c = [v for _, v in d]; cur = c[-1]
                rets = [math.log(c[i + 1] / c[i]) for i in range(max(0, len(c) - 121), len(c) - 1)]
                sd = (sum((r - sum(rets) / len(rets)) ** 2 for r in rets) / (len(rets) - 1)) ** 0.5 if len(rets) > 20 else None
                move = sd * math.sqrt(63) * 0.8 if sd else None                  # 평소 석 달 움직임(오르거나 내릴 때 평균 크기)
                p = x['p'] / 100; up = x['target'] / cur - 1; dn = x['stop'] / cur - 1
                f = fd.get('A' + x['code']) or {}
                r = {'code': x['code'], 'sym': syms[(mk, x['code'])], 'p_up': x['p'], 'cur': round(cur, 2),
                     'target': x['target'], 'stop': x['stop'], 'ref': x.get('ref'),
                     'up': round(up * 100, 1), 'dn': round(dn * 100, 1), 'exp': round((p * up + (1 - p) * dn) * 100, 1),
                     'move': round(move * 100, 1) if move else None, 'reach': round(up / move, 1) if move and up > 0 else None,
                     'hit': '목표 도달' if cur >= x['target'] else ('손절 도달' if cur <= x['stop'] else ''),
                     'why': x['why'], 'risk': x['risk'], 'peak': round(max(c[-250:]), 2), 'dd': round((cur / max(c[-250:]) - 1) * 100, 1)}
                if mk == 'kr':
                    r.update(name=f.get('name') or x['code'], market=f.get('market') or '', sector=f.get('sector') or '')
                else:
                    r.update(name=longname or x['code'], name_ko=names.get(x['code'], ''), sector='')
                rows.append(r)
                last[mk] = max(last.get(mk, ''), d[-1][0])
            rows.sort(key=(lambda z: (-z['p_up'], -z['exp'])) if t == 'stable' else (lambda z: -z['exp']))
            for i, z in enumerate(rows, 1):
                z['rank'] = i
            res[mk][t] = rows
        both = {z['code'] for z in res[mk]['stable']} & {z['code'] for z in res[mk]['aggressive']}
        for t in ('stable', 'aggressive'):
            for z in res[mk][t]:
                z['both'] = z['code'] in both
    if len(miss) > 8:
        sys.exit('★ 시세를 받지 못한 종목 %d개(%s…) — 쓰지 않음' % (len(miss), ', '.join(miss[:5])))
    M = dict(P['meta']); M.update(version=4, record_date=dt.datetime.now(KST).date().isoformat(), stale=miss,
                                  basis='가격 국장 %s, 미장 %s 기준' % (last.get('kr', '-'), last.get('us', '-')))
    out = {'meta': M, 'kr': res['kr'], 'us': res['us']}
    for mk in ('kr', 'us'):
        for t, lab in (('stable', '안정형'), ('aggressive', '적극형')):
            print('[%s %s]' % (mk, lab), ', '.join('%s %d%%·%+.1f' % (z.get('name_ko') or z['name'][:10], z['p_up'], z['exp']) for z in res[mk][t][:8]), '…')
    if dry:
        return 0
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1); f.write('\n')
    print('저장:', os.path.relpath(OUT, ROOT), '· 받지 못함', miss or '없음')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
