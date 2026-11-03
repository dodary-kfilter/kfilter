# -*- coding: utf-8 -*-
"""업종 추세 — 업종 ETF로 업종마다 추세·상대 강도를 잰다 (깔때기 3단계 재료, 2026-11-03)

사용: python3 tools/sector_fetch.py      → lists/sectors.json (10초 안팎)
자료: 야후 일봉 1년 (미장 ETF, 국장 ETF는 '.KS')
잰 것(종가 기준)
  r1w·r1m·r3m·r6m  5·21·63·126거래일 수익률 %
  rs1m·rs3m        같은 기간 지수(미장 SPY, 국장 KODEX 200) 대비 초과 수익 %p — 업종이 시장보다 강한지
  hi52             1년 최고가 대비 % (0이면 신고가)
  ma50·ma200       50일·200일 이동평균선 대비 % (플러스면 선 위)
  vol3m            최근 63거래일 일간 수익률의 표준편차를 1년으로 환산한 %(변동성)
"""
import json, os, sys, time, math, datetime as dt, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
OUT = os.path.join(ROOT, 'lists', 'sectors.json')
KST = dt.timezone(dt.timedelta(hours=9))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36'

US = [('XLK', '기술'), ('XLC', '커뮤니케이션'), ('XLY', '경기소비재'), ('XLP', '필수소비재'), ('XLF', '금융'),
      ('XLV', '헬스케어'), ('XLI', '산업재'), ('XLE', '에너지'), ('XLB', '소재'), ('XLU', '유틸리티'), ('XLRE', '부동산'),
      ('SOXX', '반도체'), ('IGV', '소프트웨어'), ('ITA', '항공우주·방산'), ('XBI', '바이오'), ('KRE', '지역은행'), ('URA', '우라늄·원전')]
KR = [('091160', '반도체'), ('157490', '소프트웨어'), ('315270', '커뮤니케이션'), ('266360', '콘텐츠'), ('091170', '은행'),
      ('102970', '증권'), ('140700', '보험'), ('091180', '자동차'), ('305720', '2차전지'), ('143860', '헬스케어'),
      ('466920', '조선'), ('139230', '중공업'), ('449450', '방산'), ('117700', '건설'), ('117680', '철강'),
      ('117460', '에너지화학'), ('228790', '화장품'), ('139290', '경기소비재'), ('266410', '필수소비재')]
BENCH = {'us': 'SPY', 'kr': '069500'}


def closes(sym):
    url = 'https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d' % sym
    for i in range(3):
        try:
            j = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': UA}), timeout=20).read())
            r = j['chart']['result'][0]
            c = [x for x in r['indicators']['quote'][0]['close'] if x is not None]
            return c if len(c) > 30 else None
        except Exception:
            time.sleep(1 + i)
    return None


def pct(a, b):
    return round((a / b - 1) * 100, 1) if a and b else None


def stats(c):
    last = c[-1]
    ret = lambda n: pct(last, c[-n - 1]) if len(c) > n else None
    ma = lambda n: pct(last, sum(c[-n:]) / n) if len(c) >= n else None
    rs = [math.log(c[i] / c[i - 1]) for i in range(max(1, len(c) - 63), len(c))]
    vol = round(math.sqrt(sum((x - sum(rs) / len(rs)) ** 2 for x in rs) / (len(rs) - 1)) * math.sqrt(252) * 100, 1) if len(rs) > 10 else None
    return {'last': round(last, 2), 'r1w': ret(5), 'r1m': ret(21), 'r3m': ret(63), 'r6m': ret(126),
            'hi52': pct(last, max(c)), 'ma50': ma(50), 'ma200': ma(200), 'vol3m': vol}


def main():
    t0 = time.time()
    jobs = [('us', s, n, s) for s, n in US] + [('kr', s, n, s + '.KS') for s, n in KR] + \
           [('us', 'SPY', '지수', 'SPY'), ('kr', '069500', '지수', '069500.KS')]
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(lambda j: closes(j[3]), jobs))
    out = {'updated': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M'), 'bench': BENCH, 'us': [], 'kr': [], 'missing': []}
    bench = {}
    for (mk, sym, name, _), c in zip(jobs, res):
        if name == '지수' and c:
            bench[mk] = stats(c)
    for (mk, sym, name, _), c in zip(jobs, res):
        if name == '지수':
            continue
        if not c:
            out['missing'].append(sym); continue
        s = stats(c); b = bench.get(mk) or {}
        s['rs1m'] = round(s['r1m'] - b['r1m'], 1) if s.get('r1m') is not None and b.get('r1m') is not None else None
        s['rs3m'] = round(s['r3m'] - b['r3m'], 1) if s.get('r3m') is not None and b.get('r3m') is not None else None
        out[mk].append(dict(sym=sym, name=name, **s))
    for mk in ('us', 'kr'):
        out[mk].sort(key=lambda x: (x.get('rs3m') if x.get('rs3m') is not None else -999), reverse=True)
    out['bench_stats'] = bench
    if len(out['us']) + len(out['kr']) < (len(US) + len(KR)) * 0.6:
        print('★ 업종 자료를 절반 넘게 못 받음 — 저장하지 않음', out['missing']); return 1
    json.dump(out, open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('저장: lists/sectors.json · 미장 %d · 국장 %d · 못 받음 %s · %.1f초' % (len(out['us']), len(out['kr']), out['missing'] or '없음', time.time() - t0))
    for mk in ('us', 'kr'):
        print(' %s 3개월 상대 강도 상위: %s' % (mk, ', '.join('%s %+.1f' % (x['name'], x['rs3m']) for x in out[mk][:5] if x.get('rs3m') is not None)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
