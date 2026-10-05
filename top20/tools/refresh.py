# -*- coding: utf-8 -*-
"""매수 후보 TOP 20 시장 자료 갱신기

사용법: python refresh.py [입력 top20.json] [출력 top20.json]

새로 받는 것 (자동)
  - 가격, 10일선, 하루 급락 기준(60일 하루 흔들림 x 2), 1년 종가 고점: 야후 파이낸스 일봉
  - 베타·고유 흔들림(최근 120거래일), 월봉·주봉 지표, 차트 자리(전고점·30주선·13주 저점)
  - 미장 옵션 내재 변동성(만기 60~120일, 행사가 ±5%): CBOE 지연 시세
  - 국장 외국인 지분율(지금·20일 전·60일 전): 다음 금융 투자자 일별
  - 위 값으로 시나리오별 결과(상승·하락 확률, 오르면·내리면 평균, 기대수익)와 순위

그대로 두는 것 (사람이 판단해서 고침)
  - alpha(고유 기대), act(행동), cm(주요 코멘트), why(판단 근거)
  - 실적·추정치 항목(hg, per, op, fg, up, dn, si, inst, ins, sector, desc)
  - meta의 시나리오 확률·폭, 문턱, 결론 문구

받기에 실패한 항목은 이전 값을 그대로 두고 stale 목록에 이름을 남긴다.
"""
import json, math, sys, os, datetime as dt, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'top20.json')
DST = sys.argv[2] if len(sys.argv) > 2 else SRC
UA = {'User-Agent': 'Mozilla/5.0'}
IDX_SYM = {'SOXX': 'SOXX', 'SPY': 'SPY', 'KOSDAQ': '^KQ11', 'KOSPI': '^KS11'}


def get_json(url, headers=None, timeout=25):
    req = urllib.request.Request(url, headers=headers or UA)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def yh(sym, interval, rng):
    """야후 일봉/주봉/월봉 → [(날짜, 시가, 고가, 저가, 종가)]"""
    try:
        j = get_json('https://query1.finance.yahoo.com/v8/finance/chart/%s?range=%s&interval=%s'
                     % (urllib.parse.quote(sym), rng, interval))['chart']['result'][0]
        q = j['indicators']['quote'][0]
        return [(dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime('%Y-%m-%d'), o, h, l, c)
                for ts, o, h, l, c in zip(j['timestamp'], q['open'], q['high'], q['low'], q['close']) if c and h and l and o]
    except Exception:
        return None


def cboe_iv(t, today):
    """CBOE 지연 시세에서 만기 60~120일, 행사가 ±5% 옵션의 내재 변동성 평균(연 단위 소수)"""
    try:
        d = get_json('https://cdn.cboe.com/api/global/delayed_quotes/options/%s.json' % t)['data']
        px = d.get('current_price') or d.get('close'); iv = []
        for o in d['options']:
            k = o['option'][len(t):]
            exp = dt.date(2000 + int(k[0:2]), int(k[2:4]), int(k[4:6])); strike = int(k[7:]) / 1000
            if 60 <= (exp - today).days <= 120 and abs(strike / px - 1) <= 0.05 and o.get('iv'):
                iv.append(o['iv'])
        return sum(iv) / len(iv) if iv else None
    except Exception:
        return None


def daum_own(code):
    """다음 금융 투자자 일별 → 외국인 지분율(%) 지금·20일 전·60일 전"""
    try:
        h = dict(UA); h['Referer'] = 'https://finance.daum.net/'
        rows = get_json('https://finance.daum.net/api/investor/days?symbolCode=A%s&perPage=60&page=1' % code, h).get('data') or []
        rate = [r.get('foreignOwnSharesRate') for r in rows]
        if not rate or rate[0] is None:
            return None
        return round(rate[0] * 100, 2), round(rate[min(20, len(rate) - 1)] * 100, 2), round(rate[-1] * 100, 2)
    except Exception:
        return None


def sd(x):
    m = sum(x) / len(x)
    return math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))


def beta_stats(d, di, N=120):
    a_ = dict((r[0], r[4]) for r in d); b_ = dict((r[0], r[4]) for r in di)
    days = sorted(set(a_) & set(b_)); N = min(N, len(days) - 1); days = days[-(N + 1):]
    a = [math.log(a_[days[i + 1]] / a_[days[i]]) for i in range(N)]
    b = [math.log(b_[days[i + 1]] / b_[days[i]]) for i in range(N)]
    ma = sum(a) / N; mb = sum(b) / N
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (N - 1)
    vb = sum((y - mb) ** 2 for y in b) / (N - 1); va = sum((x - ma) ** 2 for x in a) / (N - 1)
    be = cov / vb
    return be, math.sqrt(va), math.sqrt(max(va - be * be * vb, 0))


def chart_metrics(d, w, m):
    px = d[-1][4]
    mc = [r[4] for r in (m or [])]; ma20m = sum(mc[-20:]) / 20 if len(mc) >= 20 else None
    run = 0
    if len(mc) >= 21:
        for i in range(len(mc) - 1, 19, -1):
            if mc[i] > sum(mc[i - 19:i + 1]) / 20: run += 1
            else: break
    r36 = (px / mc[-37] - 1) * 100 if len(mc) > 37 else None
    k = min(12, len(mc) - 1)
    mrng = sum((m[i][2] - m[i][3]) / m[i][3] for i in range(len(m) - 1 - k, len(m) - 1)) / k * 100 if k > 0 else None
    pk = 0; ddv = 0
    for r in d[-250:]: pk = max(pk, r[2]); ddv = min(ddv, r[3] / pk - 1)
    w = w or []
    wc = [r[4] for r in w]; wl = [r[3] for r in w]
    w10 = sum(wc[-10:]) / 10 if len(wc) >= 10 else None; w30 = sum(wc[-30:]) / 30 if len(wc) >= 30 else None
    w10p = sum(wc[-14:-4]) / 10 if len(wc) >= 14 else None
    low13 = min(wl[-13:]) if len(wl) >= 13 else None
    hi_close = max(r[4] for r in d[-250:])
    lows = wl[-9:]; hl = sum(1 for i in range(1, len(lows)) if lows[i] > lows[i - 1]) if len(lows) > 1 else None
    return dict(run20=(run if ma20m else None), ext20=(round((px / ma20m - 1) * 100) if ma20m else None), r36=(round(r36) if r36 is not None else None),
                mrng=(round(mrng) if mrng else None), mdd=round(ddv * 100), w30=(round(w30, 2) if w30 else None),
                w30pos=(round((px / w30 - 1) * 100, 1) if w30 else None), w10up=(w10 > w10p if (w10 and w10p) else None), hl=hl,
                low13=(round(low13, 2) if low13 else None), lv_hi=round((hi_close / px - 1) * 100, 1),
                lv_w30=(round((w30 / px - 1) * 100, 1) if w30 else None), lv_low=(round((low13 / px - 1) * 100, 1) if low13 else None),
                hi_close=round(hi_close, 2))


Phi = lambda x: 0.5 * (1 + math.erf(x / math.sqrt(2)))
phi = lambda x: math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def scenario(b, sdd, idd, alpha, sc, iv_s=None, iv_i=None, k=1.0):
    """시장 세 갈래 x (베타 x 지수 움직임 + 고유 기대) + 고유 흔들림(정규분포)으로 결과 계산"""
    if iv_s and iv_i:
        tot = iv_s * math.sqrt(63 / 252); sidx = iv_i * math.sqrt(63 / 252)
        sid = math.sqrt(max(tot ** 2 - (b * sidx) ** 2, (0.25 * tot) ** 2)); swing = tot; src = '옵션 내재 변동성'
    else:
        sid = idd * math.sqrt(63); swing = sdd * math.sqrt(63); src = '지난 120일 실현 변동성'; k = 1.0
    a = alpha / 100; E = Pu = Eu = Ed = 0; out = []
    for lab, p, m in sc:
        m2 = m * k; mu = b * m2 + a; z = mu / sid; pu = Phi(z); pdn = 1 - pu
        eu = mu + sid * phi(z) / max(pu, 1e-9); ed = mu - sid * phi(z) / max(pdn, 1e-9)
        out.append(dict(lab=lab, p=round(p * 100), m=round(m2 * 100, 1), r=round(mu * 100, 1), pu=round(pu * 100)))
        E += p * mu; Pu += p * pu; Eu += p * pu * eu; Ed += p * pdn * ed
    return dict(beta=round(b, 2), swing=round(swing * 100), idio=round(sid * 100), E=round(E * 100, 1), Pup=round(Pu * 100),
                Pdn=round(100 - Pu * 100), Eup=round(Eu / max(Pu, 1e-9) * 100, 1), Edn=round(Ed / max(1 - Pu, 1e-9) * 100, 1), sc=out, volsrc=src,
                iv=(round(iv_s * 100) if (iv_s and iv_i) else None))


def main():
    F = json.load(open(SRC)); M = F['meta']
    today = dt.date.today()
    rows = [(mk, r) for mk in ('kr', 'us') for g in ('top', 'out', 'bench') for r in F[mk].get(g, [])]
    syms = sorted({r['sym'] for _, r in rows} | set(IDX_SYM.values()))
    def fetch(s): return s, dict(d=yh(s, '1d', '1y'), w=yh(s, '1wk', '3y'), m=yh(s, '1mo', '10y'))
    with ThreadPoolExecutor(8) as ex: P = dict(ex.map(fetch, syms))
    us_t = [r['sym'] for mk, r in rows if mk == 'us'] + ['SOXX', 'SPY']
    with ThreadPoolExecutor(8) as ex: IV = dict(zip(us_t, ex.map(lambda t: cboe_iv(t, today), us_t)))
    kr_codes = [r['code'] for mk, r in rows if mk == 'kr']
    with ThreadPoolExecutor(8) as ex: OWN = dict(zip(kr_codes, ex.map(daum_own, kr_codes)))

    def rv_ann(sym):
        c = [r[4] for r in P[sym]['d']][-121:]
        return sd([math.log(c[i + 1] / c[i]) for i in range(len(c) - 1)]) * math.sqrt(252)
    kmk = {}
    for idx in ('SOXX', 'SPY'):
        kmk[idx] = (IV[idx] / rv_ann(idx)) if (IV.get(idx) and P[idx]['d']) else 1.0

    stale = []; last_dates = {}
    for mk, r in rows:
        s = r['sym']; d = P[s]['d']; idx = r['idx']; di = P[IDX_SYM[idx]]['d']
        if not d or not di or len(d) < 130:
            stale.append(r['name']); continue
        c = [x[4] for x in d]; old_cur = r.get('cur')
        r['cur'] = round(c[-1], 2); r['ma10'] = round(sum(c[-10:]) / 10, 2)
        r60 = [math.log(c[i + 1] / c[i]) for i in range(len(c) - 61, len(c) - 1)]
        r['drop2'] = round(2 * sd(r60) * 100, 1)
        hi_i = max(range(max(0, len(c) - 250), len(c)), key=lambda i: c[i])
        r['peak'] = round(c[hi_i], 2); r['peak_date'] = d[hi_i][0]
        if r.get('mcap') and old_cur: r['mcap'] = r['mcap'] * r['cur'] / old_cur
        r['mw'] = chart_metrics(d, P[s]['w'], P[s]['m'])
        b, sdd, idd = beta_stats(d, di)
        if mk == 'us':
            r.update(scenario(b, sdd, idd, r['alpha'], M['scenarios'][idx], IV.get(s), IV.get(idx), kmk.get(idx, 1.0)))
            if not IV.get(s): stale.append(r['name'] + '(옵션)')
        else:
            r.update(scenario(b, sdd, idd, r['alpha'], M['scenarios'][idx]))
            o = OWN.get(r['code'])
            if o:
                r['own'], r['own20'], r['own60'] = o
                r['a60'] = round(o[0] - o[2], 2); r['a20'] = round(o[0] - o[1], 2)
            elif r.get('own') is not None:
                stale.append(r['name'] + '(외국인)')
        last_dates[mk] = max(last_dates.get(mk, ''), d[-1][0])

    for mk in ('kr', 'us'):
        th = M['thresholds'][mk]; allr = F[mk]['top'] + F[mk]['out'] + F[mk].get('bench', [])
        for r in allr: r['pass'] = r['Pup'] >= th
        keep = [r for r in allr if not r['act'].startswith('제외')]; drop = [r for r in allr if r['act'].startswith('제외')]
        passed = sorted([r for r in keep if r['pass']], key=lambda r: -r['Eup'])
        rest = sorted([r for r in keep if not r['pass']], key=lambda r: (-r['Pup'], -r['Eup']))
        top = (passed + rest)[:20]; spill = (passed + rest)[20:]
        for i, r in enumerate(top, 1): r['rank'] = i
        for r in spill: r.pop('rank', None)
        F[mk]['top'] = top; F[mk]['out'] = sorted(drop, key=lambda r: -r['Pup']); F[mk]['bench'] = spill; F[mk]['th'] = th

    M['record_date'] = today.isoformat()
    M['basis'] = '가격 국장 %s, 미장 %s 기준(장중이면 그 시점 가격), 미장 옵션 내재 변동성 %s 수집' % (
        last_dates.get('kr', '-'), last_dates.get('us', '-'), today.isoformat())
    M['iv_note'] = dict(soxx_iv=round(IV['SOXX'] * 100) if IV.get('SOXX') else None, spy_iv=round(IV['SPY'] * 100) if IV.get('SPY') else None,
                        k_soxx=round(kmk['SOXX'], 2), k_spy=round(kmk['SPY'], 2))
    M['stale'] = stale
    json.dump(F, open(DST, 'w'), ensure_ascii=False, indent=1)
    print('갱신 완료:', DST, '| 받지 못한 항목:', stale or '없음')
    for mk in ('kr', 'us'):
        print('[%s] 문턱 %d%%' % (mk, M['thresholds'][mk]))
        for r in F[mk]['top']:
            print('  %2d %s %s 상승 %d%% 오르면 %+.1f%% 내리면 %+.1f%% 기대 %+.1f%%' % (
                r['rank'], r['name'], '통과' if r['pass'] else '    ', r['Pup'], r['Eup'], r['Edn'], r['E']))


if __name__ == '__main__':
    main()
