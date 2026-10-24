# -*- coding: utf-8 -*-
"""TOP 20 v3 — 안정형 20(상승 확률 높은 순)·공격형 20(오르면 큰 순)

사용(저장소 루트): python3 top20/tools/build.py            # top20/top20.json을 새로 만든다
               python3 top20/tools/build.py --dry      # 쓰지 않고 요약만
GitHub Actions(.github/workflows/top20.yml)가 매주 토요일 08:30(한국시간)에 돌린다.

후보(대표 지수 구성 종목)
  - 국장: 코스피 200 + 코스닥 150 — 거래소 구성 목록은 막혀 있어(data.krx.co.kr) 다음 시가총액 순위로 근사한다
          (보통주만, 스팩·리츠 제외, 코스피 상위 200·코스닥 상위 150)
  - 미장: S&P 500(위키백과 목록) + 나스닥 100(나스닥 API), 겹치는 종목은 하나로

1단계 상승 확률 — 방향만 본다
  P↑ = P시장 + Δ   (5~95%로 자름)
  P시장 = Σ wₖ · Φ(β · mₖ ÷ σ)
    k: 시장 갈래(좋은 장·나쁜 장·횡보), wₖ: 그 갈래 확률, mₖ: 그 갈래의 석 달 지수 움직임 (meta.scenarios)
    β: 지수에 대한 민감도(최근 120거래일), σ: 시장과 상관없는 고유 흔들림(석 달)
    Φ: 정규분포에서 0보다 클 확률
  Δ: 종목 판단(±%p, top20/judgments.json) — 판단이 없으면 0

2단계 폭 — 크기만 본다. 오를 폭과 내릴 폭을 서로 다른 근거로 따로 계산한다(2026-10-23)
  흔들림 기본값: Δ를 넣지 않은 분포(시장 갈래 × 베타 + 고유 흔들림)에서 오른 경우·내린 경우의 평균 — 정규분포라 크기가 거의 같다
  오를 폭 = 지금 가격에서 위쪽 저항까지: 최근 120거래일 거래대금이 가장 몰린 가격대(매물대), 없으면 1년 고점
  내릴 폭 = 지금 가격에서 아래쪽 지지까지: 최근 120거래일 거래대금이 가장 몰린 가격대, 없으면 1년 저점
            국장은 PBR 1배 가격(주당 순자산)이 지금 가격 아래에 있으면 더 가까운 쪽을 지지로 본다
  쓰는 범위: 판단한 종목에만(판단 없는 종목은 흔들림 기본값) — 판단 없는 동전 던지기가 구조 폭만으로 목록에 들어오지 않게
  현실 범위: 구조 폭은 흔들림 기본값과 반씩 섞고 0.67~1.5배 안으로 / 판단(judgments.json의 up·dn, %)이 있으면 그 값을 쓴다
  ★한 분포에서 같이 뽑으면 기대 수익이 '(2 × 상승 확률 − 1) × 흔들림'이 되어 상승 확률 목록과 같은 종목을 고르게 된다

선발
  안정형 20 = P↑ 높은 순(같으면 σ 작은 순)
  기대 수익 20 = P↑ 51% 이상(오를 확률이 내릴 확률보다 높은 것) 중 기대 수익 큰 순 — 내릴 확률이 더 큰 종목은 뺀다
    기대 수익 = P↑ × 오르면 + (1 − P↑) × 내리면  (2026-10-23: '오르면'만으로 줄 세우면 크게 흔들리는 종목을 고르는 복권이 된다.
    내릴 때의 폭까지 넣어야 흔들림이 공짜로 유리해지지 않는다) · 기대 수익이 0 이하인 종목은 넣지 않는다
    ★바닥이 50%면 판단 없는 종목(시장 몫 중앙 50.2%, 사실상 동전 던지기)이 흔들림이 크다는 이유만으로
      공격형을 채운다(실측 2026-10-20: 국장 20개 전부). 51%는 내릴 확률보다 2%p 이상 높다는 최소한의 방향 우위다
  판단에서 제외(excluded)한 종목은 두 목록에 넣지 않는다
"""
import json, math, os, re, sys, time, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
OUT = os.path.join(ROOT, 'top20', 'top20.json')
JUDGE = os.path.join(ROOT, 'top20', 'judgments.json')
FUND = os.path.join(ROOT, 'cache', 'fundamentals.json')
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
KST = dt.timezone(dt.timedelta(hours=9))
N_KOSPI, N_KOSDAQ, N_PICK = 200, 150, 20
AGGR_FLOOR = 51         # 공격형의 상승 확률 바닥(%)
IDX = {'KOSPI': '^KS11', 'KOSDAQ': '^KQ11', 'SPY': 'SPY'}
Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
phi = lambda x: math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def get(url, headers=None, tries=3, as_json=True):
    h = {'User-Agent': UA}
    h.update(headers or {})
    for i in range(tries):
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=25).read()
            return json.loads(b) if as_json else b.decode('utf-8', 'replace')
        except Exception:
            time.sleep(1.5 * (i + 1))
    return None


# ───────── 후보 ─────────
def kr_universe():
    """다음 시가총액 순위 → 코스피 상위 200·코스닥 상위 150(보통주, 스팩·리츠 제외)"""
    fd = (json.load(open(FUND, encoding='utf-8')).get('data') or {}) if os.path.exists(FUND) else {}
    out = []
    for mk, n in (('KOSPI', N_KOSPI), ('KOSDAQ', N_KOSDAQ)):
        got = []
        for page in range(1, 6):
            j = get('https://finance.daum.net/api/trend/market_capitalization?page=%d&perPage=200&fieldName=marketCap'
                    '&order=desc&market=%s&pagination=true' % (page, mk), {'Referer': 'https://finance.daum.net/'})
            rows = (j or {}).get('data') or []
            for r in rows:
                sc = r.get('symbolCode') or ''; name = r.get('name') or ''; f = fd.get(sc) or {}
                if not f or not sc[1:].endswith('0') or '스팩' in name or '리츠' in name or f.get('sector') == '부동산':
                    continue                                   # 주식 목록(펀더멘털)에 없는 ETF·ETN, 우선주, 스팩, 리츠는 뺀다
                got.append({'code': sc[1:], 'sym': sc[1:] + ('.KS' if mk == 'KOSPI' else '.KQ'), 'name': name,
                            'market': mk, 'idx': mk, 'sector': f.get('sector') or '', 'mcap': r.get('marketCap')})
                if len(got) >= n:
                    break
            if len(got) >= n or not rows:
                break
        out += got[:n]
    return out


def us_universe():
    """S&P 500(위키백과 원문) + 나스닥 100(나스닥 API)"""
    t = get('https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw',
            {'User-Agent': 'kfilter/1.0 (github.com/dodary-kfilter)'}, as_json=False) or ''
    seg = t[t.find('{|'):t.find('|}', t.find('{|'))]
    sp = {}
    for row in seg.split('|-')[1:]:
        m = re.search(r'\{\{\s*(?:NyseSymbol|NasdaqSymbol|Nyse symbol|Nasdaq symbol|BZX link|Cboe symbol)\s*\|\s*([A-Z.]+)', row, re.I)
        if not m:
            continue
        cells = [c.strip() for c in row.split('||') if c.strip()]          # [기호, 회사명, 업종, 세부 업종, …]
        name = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', cells[1] if len(cells) > 1 else '').strip(' |')
        sector = cells[2].strip(' |') if len(cells) > 2 else ''
        sp[m.group(1).upper()] = (name, sector)
    j = get('https://api.nasdaq.com/api/quote/list-type/nasdaq100', {'Accept': 'application/json'})
    nq = {r['symbol']: r for r in (((j or {}).get('data') or {}).get('data') or {}).get('rows') or []}
    out = []
    for s in sorted(set(sp) | set(nq)):
        name, sector = sp.get(s, ('', ''))
        r = nq.get(s) or {}
        if not name:
            name = re.sub(r'\s+(Common Stock|Class [A-C].*|Ordinary Shares.*|American Depositary.*)$', '', r.get('companyName') or s)
        mc = None
        try:
            mc = float((r.get('marketCap') or '').replace(',', '')) if r.get('marketCap') else None
        except Exception:
            pass
        out.append({'code': s, 'sym': s.replace('.', '-'), 'name': name, 'market': 'US', 'idx': 'SPY', 'sector': sector,
                    'mcap': mc, 'sp500': s in sp, 'ndx': s in nq})
    return out, len(sp), len(nq)


# ───────── 자료 ─────────
def daily(sym):
    j = get('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=1y&interval=1d' % urllib.parse.quote(sym))
    try:
        r = j['chart']['result'][0]; q = r['indicators']['quote'][0]; close = list(q['close'])
        m = r.get('meta') or {}
        if close and close[-1] is None and q['open'][-1] and m.get('regularMarketPrice'):
            close[-1] = m['regularMarketPrice']                     # 장 마감 뒤 비어 있는 마지막 종가는 현재가로
        vol = list(q.get('volume') or [0] * len(close))
        return [(dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y-%m-%d'), c, v or 0)
                for ts, c, v in zip(r['timestamp'], close, vol) if c]
    except Exception:
        return None


def beta_sigma(d, di, N=120):
    a_ = {t: c for t, c, *_ in d}; b_ = {t: c for t, c, *_ in di}
    days = sorted(set(a_) & set(b_)); N = min(N, len(days) - 1); days = days[-(N + 1):]
    a = [math.log(a_[days[i + 1]] / a_[days[i]]) for i in range(N)]
    b = [math.log(b_[days[i + 1]] / b_[days[i]]) for i in range(N)]
    ma = sum(a) / N; mb = sum(b) / N
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (N - 1)
    vb = sum((y - mb) ** 2 for y in b) / (N - 1); va = sum((x - ma) ** 2 for x in a) / (N - 1)
    be = cov / vb
    return be, math.sqrt(max(va - be * be * vb, 0)) * math.sqrt(63)        # 베타, 고유 흔들림(석 달)


def market_part(b, s, sc):
    """Δ를 넣지 않은 분포 → (P시장, 오르면 평균, 내리면 평균)"""
    s = max(s, 1e-4); P = Eu = Ed = 0.0
    for _, w, m in sc:
        mu = b * m; z = mu / s; pu = Phi(z); pd = 1 - pu
        eu = mu + s * phi(z) / max(pu, 1e-9); ed = mu - s * phi(z) / max(pd, 1e-9)
        P += w * pu; Eu += w * pu * eu; Ed += w * pd * ed
    return P, Eu / max(P, 1e-9), Ed / max(1 - P, 1e-9)


def levels(d, cur, bps=None, N=120, bins=24):
    """(위쪽 저항, 아래쪽 지지, 근거 이름들) — 최근 N거래일 거래대금이 가장 몰린 가격대, 없으면 1년 고점·저점, 국장은 PBR 1배"""
    seg = d[-N:]
    lo, hi = min(x[1] for x in seg), max(x[1] for x in seg)
    res = sup = None; rs = ss = ''
    if hi > lo:
        w = (hi - lo) / bins; h = [0.0] * bins
        for _, c, v in seg:
            h[min(bins - 1, int((c - lo) / w))] += (v or 0) * c
        cen = lambda i: lo + (i + 0.5) * w
        big = 0.4 * max(h)                                          # 의미 있는 구간 = 가장 많이 거래된 구간의 40% 이상
        up_b = [cen(i) for i in range(bins) if cen(i) > cur * 1.01 and h[i] >= big]
        dn_b = [cen(i) for i in range(bins) if cen(i) < cur * 0.99 and h[i] >= big]
        if up_b: res, rs = min(up_b), '매물대'                        # 위로 가장 가까운 벽
        if dn_b: sup, ss = max(dn_b), '거래 밀집'                     # 아래로 가장 가까운 바닥
    y = d[-250:]; H, L = max(x[1] for x in y), min(x[1] for x in y)
    if res is None and H > cur * 1.02: res, rs = H, '1년 고점'
    if sup is None and L < cur * 0.98: sup, ss = L, '1년 저점'
    if bps and cur * 0.5 < bps < cur * 0.99 and (sup is None or bps > sup):
        sup, ss = bps, 'PBR 1배'                                  # 순자산 가격이 더 가까운 지지
    return res, sup, rs, ss


def main(argv):
    dry = '--dry' in argv
    today = dt.datetime.now(KST).date()
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    M = old.get('meta') or {}
    SC = M.get('scenarios') or {}
    J = json.load(open(JUDGE, encoding='utf-8')) if os.path.exists(JUDGE) else {'kr': {}, 'us': {}}
    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}
    fd = (json.load(open(FUND, encoding='utf-8')).get('data') or {}) if os.path.exists(FUND) else {}    # 국장 PBR 1배 가격(주당 순자산)

    kr = kr_universe(); us, n_sp, n_nq = us_universe()
    if len(kr) < 300 or len(us) < 450:
        sys.exit('★ 후보 목록을 다 받지 못함(국장 %d · 미장 %d) — 쓰지 않음' % (len(kr), len(us)))
    syms = sorted({x['sym'] for x in kr + us} | set(IDX.values()))
    with ThreadPoolExecutor(6) as ex:
        D = dict(zip(syms, ex.map(daily, syms)))
    stale, last = [], {}
    res = {}
    for mk, U in (('kr', kr), ('us', us)):
        rows = []
        for x in U:
            d = D.get(x['sym']); di = D.get(IDX[x['idx']])
            if not d or not di or len(d) < 130:
                stale.append(x['name']); continue
            b, s = beta_sigma(d, di)
            sc = SC.get(x['idx']) or SC.get('SPY')
            pm, up0, dn0 = market_part(b, s, sc)
            j = (J.get(mk) or {}).get(x['code']) or {}
            delta = float(j.get('delta') or 0)
            c = [z[1] for z in d]; hi = max(c[-250:])
            bps = (fd.get('A' + x['code']) or {}).get('bps') if mk == 'kr' else None
            rl, sl, rs, ss = levels(d, c[-1], bps)
            clip = lambda v, a, b_: max(a, min(b_, v))
            # 가격 구조는 판단한 종목에만 쓴다 — 판단 없는 종목에 쓰면 확률 51% 동전 던지기가 구조 폭만으로 기대 수익 목록을 채운다(복권, 2026-10-23 실측)
            # 구조 폭은 흔들림 기본값과 반씩 섞고 0.67~1.5배 안으로 — 많이 빠진 종목일수록 위 벽이 멀고 아래 바닥이 가까워 한쪽으로 치우치기 쉽다
            def mix(raw, base):
                return clip(base + 0.5 * (raw - base), 0.67 * base, 1.5 * base)
            if j.get('up') is not None:
                up, up_src = float(j['up']) / 100, '판단'
            elif j and rl:
                up, up_src = mix(rl / c[-1] - 1, up0), rs
            else:
                up, up_src = up0, '흔들림 기본'
            if j.get('dn') is not None:
                dn, dn_src = -abs(float(j['dn'])) / 100, '판단'
            elif j and sl:
                dn, dn_src = -mix(1 - sl / c[-1], -dn0), ss
            else:
                dn, dn_src = dn0, '흔들림 기본'
            pu = min(95, max(5, pm * 100 + delta))
            rows.append(dict(x, cur=round(c[-1], 2), peak=round(hi, 2), dd=round((c[-1] / hi - 1) * 100, 1),
                             beta=round(b, 2), sigma=round(s * 100, 1), p_mkt=round(pm * 100, 1), delta=round(delta, 1),
                             p_up=round(pu, 1), up=round(up * 100, 1), dn=round(dn * 100, 1),
                             up0=round(up0 * 100, 1), dn0=round(dn0 * 100, 1), up_src=up_src, dn_src=dn_src,
                             up_lv=round(rl, 2) if rl and up_src in ('매물대', '1년 고점') else None,
                             dn_lv=round(sl, 2) if sl and dn_src in ('거래 밀집', '1년 저점', 'PBR 1배') else None,
                             why_mag=j.get('why_mag') or '',
                             exp=round(pu / 100 * up * 100 + (1 - pu / 100) * dn * 100, 1),
                             judged=bool(j), excluded=bool(j.get('excluded')), why=j.get('why') or '',
                             name_ko=names.get(x['code'], '') if mk == 'us' else ''))
            last[mk] = max(last.get(mk, ''), d[-1][0])
        cand = [r for r in rows if not r['excluded']]
        stable = sorted(cand, key=lambda r: (-r['p_up'], r['sigma']))[:N_PICK]
        aggr = sorted([r for r in cand if r['p_up'] >= AGGR_FLOOR and r['exp'] > 0], key=lambda r: -r['exp'])[:N_PICK]
        both = {r['code'] for r in stable} & {r['code'] for r in aggr}
        for L in (stable, aggr):
            for i, r in enumerate(L, 1):
                r['rank'] = i; r['both'] = r['code'] in both
        res[mk] = {'stable': [dict(r) for r in stable], 'aggressive': [dict(r) for r in aggr],
                   'n': len(rows), 'judged_n': sum(1 for r in rows if r['judged']),
                   'universe': ('코스피 200·코스닥 150(시가총액 순 근사)' if mk == 'kr' else 'S&P 500·나스닥 100')}

    if len(stale) > (len(kr) + len(us)) // 10:                 # 시세를 10% 넘게 못 받은 날(야후가 막힌 날)은 덮어쓰지 않는다
        sys.exit('★ 시세를 받지 못한 종목 %d개 — 쓰지 않음' % len(stale))
    meta = dict(M)
    for k in ('thresholds', 'stale'):
        meta.pop(k, None)
    if (J.get('meta') or {}).get('judged'):
        meta['judged'] = J['meta']['judged']                    # 판단일은 판단 파일을 따른다
    meta.update(version=3, record_date=today.isoformat(), stale=stale, aggr_floor=AGGR_FLOOR,
                basis='가격 국장 %s, 미장 %s 기준' % (last.get('kr', '-'), last.get('us', '-')),
                method='상승 확률 = 시장 몫 + 종목 판단, 높은 순 · 기대 수익 = 상승 확률 × 오를 폭 + 하락 확률 × 내릴 폭, 상승 확률 %d%% 이상 중 큰 순 · 오를 폭은 위로 가장 가까운 매물대(없으면 1년 고점), 내릴 폭은 아래로 가장 가까운 거래 밀집(없으면 1년 저점, 국장은 PBR 1배도)까지 — 판단한 종목만, 흔들림 기본값과 반씩 섞어 0.67~1.5배 안' % AGGR_FLOOR,
                universe={'kr': res['kr']['n'], 'us': res['us']['n'], 'sp500': n_sp, 'ndx': n_nq})
    out = {'meta': meta, 'kr': res['kr'], 'us': res['us']}
    print('후보 국장 %d · 미장 %d (S&P %d · 나스닥100 %d) · 받지 못함 %d' % (res['kr']['n'], res['us']['n'], n_sp, n_nq, len(stale)))
    for mk in ('kr', 'us'):
        R = res[mk]
        print('[%s] 판단 %d개' % (mk, R['judged_n']))
        print('  안정형:', ', '.join('%s %.0f%%' % (r['name'], r['p_up']) for r in R['stable'][:10]), '…')
        print('  기대 수익:', ', '.join('%s %+.1f%%(%.0f%%·%+.0f/%+.0f)' % (r['name'], r['exp'], r['p_up'], r['up'], r['dn']) for r in R['aggressive'][:10]), '…')
    if dry:
        return 0
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=1); f.write('\n')
    print('저장:', os.path.relpath(OUT, ROOT))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
