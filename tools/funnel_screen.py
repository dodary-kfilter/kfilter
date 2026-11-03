# -*- coding: utf-8 -*-
"""깔때기 2단계 — 숫자로 거르기: 업종 점수 + 기업 점수 → 시장마다 후보 (2026-11-03)

사용: python3 tools/funnel_screen.py      → lists/candidates.json (1초 안팎, 자료 파일만 읽는다)
입력: lists/sectors.json(업종 추세) · us_fund.json · kr_fund.json(기업 숫자) · events.json(공시 사건)
      analyst.json(상위 애널 매수) · 13f/최근분기.json(슈퍼투자자) · data.json(국장 외국인·기관 동시 매수)
      top20/picks.json(지금 TOP 20 — 언제나 후보에 넣어 새 후보와 같은 줄에서 다시 견준다)

점수(모두 같은 시장 안에서의 백분위 0~100, 자료가 없으면 50)
  업종 점수   = 0.4·3개월 지수 대비 초과 + 0.2·1개월 초과 + 0.2·추세(50·200일선 위) + 0.2·업종 기업들의 매출 성장 중앙값
  기업 부분   수익성(업종 대비 영업이익률)·성장(매출·이익)·가치(업종 대비 기업가치 배수, 낮을수록 높은 점수)
             추세(3·6개월 수익률·이동평균선)·안정(변동성·고점 대비 낙폭이 작을수록)·돈의 흐름(미장 애널·13F, 국장 외국인·기관)
  안정형 점수 = 0.75 × (0.30 수익성 + 0.20 가치 + 0.15 성장 + 0.15 추세 + 0.20 안정) + 0.25 × 업종 점수 + 사건 가감
  적극형 점수 = 0.75 × (0.30 성장 + 0.25 추세 + 0.15 수익성 + 0.10 가치 + 0.20 돈의 흐름) + 0.25 × 업종 점수 + 사건 가감
후보 = 지금 TOP 20 전부 + 안정형 새 후보 상위 30 + 적극형 새 후보 상위 30 + 꼭 볼 것(중대 공시 사건·애널 다수·13F 신규·국장 동시 매수, 최대 15)
국장 새 후보는 코스피 200·코스닥 150 기업만(거래가 충분해야 판단대로 사고팔 수 있다), 안정형 새 후보는 시총 1조원 이상만 — 지금 TOP 20은 조건과 상관없이 남긴다
코드는 줄 세우기의 재료만 만든다 — 업종이 국면에 맞는지, 기업의 특성·개성, 수익 예상과 최종 순위는 매일 판단(Claude)이 정한다.
"""
import json, os, glob, datetime as dt, statistics as st

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
KST = dt.timezone(dt.timedelta(hours=9))
J = lambda p: json.load(open(os.path.join(ROOT, p), encoding='utf-8')) if os.path.exists(os.path.join(ROOT, p)) else {}

# ── 업종 묶음 ──
US_GICS = {'Information Technology': 'XLK', 'Communication Services': 'XLC', 'Consumer Discretionary': 'XLY',
           'Consumer Staples': 'XLP', 'Financials': 'XLF', 'Health Care': 'XLV', 'Industrials': 'XLI', 'Energy': 'XLE',
           'Materials': 'XLB', 'Utilities': 'XLU', 'Real Estate': 'XLRE'}
US_IND = [('Semiconductor', 'SOXX'), ('Software', 'IGV'), ('EDP Services', 'IGV'), ('Programming Data Processing', 'IGV'),
          ('Aerospace', 'ITA'), ('Military/Government', 'ITA'), ('Biotechnology', 'XBI')]
KR_SEC = [(('반도체와반도체장비', '디스플레이장비및부품'), '반도체'), (('전자장비와기기', '전기제품', '통신장비', '컴퓨터와주변기기', '핸드셋', '전자제품'), 'IT하드웨어'),
          (('IT서비스', '소프트웨어'), '소프트웨어'), (('양방향미디어와서비스', '무선통신서비스', '다각화된통신서비스'), '커뮤니케이션'),
          (('게임엔터테인먼트', '방송과엔터테인먼트', '미디어', '광고'), '콘텐츠'), (('은행',), '은행'), (('증권', '창업투자', '기타금융', '카드'), '증권'),
          (('손해보험', '생명보험'), '보험'), (('자동차', '자동차부품', '타이어'), '자동차'),
          (('제약', '생물공학', '건강관리장비와용품', '생명과학도구및서비스', '건강관리업체및서비스', '건강관리기술'), '헬스케어'),
          (('조선',), '조선'), (('기계', '전기장비', '건축제품', '복합기업'), '중공업'), (('우주항공과국방',), '방산'), (('건설', '건축자재'), '건설'),
          (('철강', '비철금속'), '철강'), (('화학', '석유와가스', '에너지장비및서비스'), '에너지화학'), (('화장품',), '화장품'),
          (('백화점과일반상점', '섬유,의류,신발,호화품', '호텔,레스토랑,레저', '가정용기기와용품', '레저용장비와제품', '교육서비스'), '경기소비재'),
          (('식품', '음료', '담배', '식품과기본식료품소매'), '필수소비재')]
KR_BATTERY = {'LG에너지솔루션', '삼성SDI', '에코프로', '에코프로비엠', '엘앤에프', '포스코퓨처엠', 'SK아이이테크놀로지', '엔켐', '코스모신소재',
              '나노신소재', '대주전자재료', '천보', '솔루스첨단소재', '에코프로머티', '레이크머티리얼즈', '피엔티', '씨아이에스'}
POS_KR = {'대규모 공급계약', '자사주 소각', '자사주 매입', '배당', '다른 회사 인수', '합병', '시설 투자', '무상증자'}
NEG_KR = {'계약 해지·철회', '유상증자', '주식 연계 채권 발행', '소송', '상장 유지 문제', '감자', '최대주주 변경'}
BIG_KR = {'합병', '회사 분할', '다른 회사 인수', '사업·자산 양수도', '대규모 공급계약', '계약 해지·철회', '유상증자', '공개매수', '최대주주 변경', '상장 유지 문제'}
BIG_US = ('합병', '공개매수', '중요 계약 체결', '인수·매각 완료', '지배권 변경', '파산', '재무제표 정정', '상장 유지 문제', '구조조정', '자산 손상')
NEG_US = ('파산', '재무제표 정정', '상장 유지 문제', '자산 손상', '구조조정', '지분 발행')


def us_bucket(x):
    for k, e in US_IND:
        if k in (x.get('industry') or ''):
            return e
    return US_GICS.get(x.get('sector'))


def kr_bucket(x):
    if x.get('name') in KR_BATTERY:
        return '2차전지'
    for names, b in KR_SEC:
        if x.get('sector') in names:
            return b
    return None


def pct_map(vals):
    """{key: 값} → {key: 백분위 0~100} (값 없는 key는 빠진다)"""
    xs = sorted((v, k) for k, v in vals.items() if isinstance(v, (int, float)))
    n = len(xs)
    if n < 2:
        return {k: 50.0 for _, k in xs}
    return {k: round(i / (n - 1) * 100, 1) for i, (_, k) in enumerate(xs)}


def p(m, k):
    return m.get(k, 50.0)


def sector_scores(mk, S, growth_by_bucket):
    rows = S.get(mk) or []
    key = (lambda r: r['sym']) if mk == 'us' else (lambda r: r['name'])
    rs3 = pct_map({key(r): r.get('rs3m') for r in rows}); rs1 = pct_map({key(r): r.get('rs1m') for r in rows})
    gr = pct_map({key(r): growth_by_bucket.get(key(r)) for r in rows})
    out = {}
    for r in rows:
        k = key(r)
        tr = 100 if (r.get('ma50') or 0) > 0 and (r.get('ma200') or 0) > 0 else 50 if (r.get('ma200') or 0) > 0 else 0
        out[k] = {'name': r['name'], 'sym': r['sym'], 'score': round(0.4 * p(rs3, k) + 0.2 * p(rs1, k) + 0.2 * tr + 0.2 * p(gr, k), 1),
                  'rs3m': r.get('rs3m'), 'rs1m': r.get('rs1m'), 'r3m': r.get('r3m'), 'hi52': r.get('hi52'),
                  'above_ma200': (r.get('ma200') or 0) > 0, 'growth_med': growth_by_bucket.get(k)}
    return out


def members(P, mk):
    out = {}
    for t in ('stable', 'aggressive'):
        for x in (P.get(mk) or {}).get(t, []):
            out[x['code']] = t
    return out


def main():
    S, US, KR, E = J('lists/sectors.json'), J('lists/us_fund.json'), J('lists/kr_fund.json'), J('lists/events.json')
    A, D, P = J('lists/analyst.json'), J('data.json'), J('top20/picks.json')
    f13p = sorted(glob.glob(os.path.join(ROOT, 'lists', '13f', '*.json')))
    F = json.load(open(f13p[-1], encoding='utf-8')) if f13p else {}
    out = {'updated': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M'), 'sectors': {}, 'us': [], 'kr': [], 'notes': []}

    # ── 미장 ──
    UI = US.get('items') or {}
    seen_cik_names = {}
    for t, x in list(UI.items()):                                 # 같은 회사 두 종류 주식(GOOGL·GOOG 등)은 시총 큰 쪽만
        nm = (x.get('name') or '').replace(' A', '').replace(' C', '').replace(' B', '').strip()
        if nm in seen_cik_names and UI[seen_cik_names[nm]].get('rev_ttm') == x.get('rev_ttm'):
            if (x.get('mcap_b') or 0) > (UI[seen_cik_names[nm]].get('mcap_b') or 0):
                UI.pop(seen_cik_names[nm]); seen_cik_names[nm] = t
            else:
                UI.pop(t)
        else:
            seen_cik_names[nm] = t
    gb = {}
    for t, x in UI.items():
        b = us_bucket(x); x['_b'] = b
        if b and isinstance(x.get('rev_g'), (int, float)): gb.setdefault(b, []).append(x['rev_g'])
    us_sec = sector_scores('us', S, {b: round(st.median(v), 1) for b, v in gb.items() if len(v) >= 3})
    ana = {}
    for s_ in (A.get('stocks') or []):
        by = s_.get('by') or []
        pts = [b['pt'] for b in by if b.get('pt')]
        ana[s_['ticker']] = {'n': len(by), 'pt': round(st.median(pts), 2) if pts else None, 'top': min((b.get('rank') or 999) for b in by) if by else None}
    f13 = {}
    for lst, w in (('new', 'new'), ('add', 'add'), ('reduce', 'reduce'), ('exit', 'exit')):
        for y in ((F.get('lists') or {}).get(lst) or []):
            f13.setdefault(y['ticker'], []).append(w)
    for y in ((F.get('consensus') or {}).get('buy_tilt') or []):
        f13.setdefault(y['ticker'], []).append('buy_tilt')
    for y in ((F.get('consensus') or {}).get('sell_tilt') or []):
        f13.setdefault(y['ticker'], []).append('sell_tilt')
    ev_us = {}
    for e in ((E.get('us') or {}).get('items') or []):
        ev_us.setdefault(e['code'], []).append({'date': e['date'], 'kind': e['kind']})
    # 업종 대비 기준값
    med = lambda key, b: st.median([y[key] for y in UI.values() if y.get('_b') == b and isinstance(y.get(key), (int, float)) and y[key] > 0] or [0]) or None
    sec_med = {b: {'op_margin': med('op_margin', b), 'ev_ebit': med('ev_ebit', b), 'per': med('per', b)} for b in set(y.get('_b') for y in UI.values())}
    q_rel, v_rel, flow = {}, {}, {}
    for t, x in UI.items():
        sm = sec_med.get(x['_b']) or {}
        if isinstance(x.get('op_margin'), (int, float)) and sm.get('op_margin'): q_rel[t] = x['op_margin'] - sm['op_margin']
        if isinstance(x.get('ev_ebit'), (int, float)) and x['ev_ebit'] > 0 and sm.get('ev_ebit'): v_rel[t] = -(x['ev_ebit'] / sm['ev_ebit'])
        elif isinstance(x.get('per'), (int, float)) and x['per'] > 0 and sm.get('per'): v_rel[t] = -(x['per'] / sm['per'])
        a = ana.get(t) or {}; f = f13.get(t) or []
        flow[t] = a.get('n', 0) * 10 + (15 if 'new' in f else 0) + (10 if 'add' in f else 0) + (10 if 'buy_tilt' in f else 0) - (10 if 'sell_tilt' in f else 0) - (10 if 'exit' in f else 0)
    Q, V = pct_map(q_rel), pct_map(v_rel)
    G1, G2 = pct_map({t: x.get('rev_g') for t, x in UI.items()}), pct_map({t: x.get('rev_q_yoy') for t, x in UI.items()})
    T3, T6 = pct_map({t: x.get('r3m') for t, x in UI.items()}), pct_map({t: x.get('r6m') for t, x in UI.items()})
    VOL = pct_map({t: -x['vol3m'] for t, x in UI.items() if isinstance(x.get('vol3m'), (int, float))})
    FL = pct_map(flow)
    mem = members(P, 'us')
    rows = []
    for t, x in UI.items():
        sc = (us_sec.get(x['_b']) or {}).get('score', 50.0)
        ma = 100 if (x.get('ma200') or 0) > 0 and (x.get('ma50') or 0) > 0 else 50 if (x.get('ma200') or 0) > 0 else 0
        parts = {'수익성': p(Q, t), '성장': round(0.6 * p(G1, t) + 0.4 * p(G2, t), 1), '가치': p(V, t),
                 '추세': round(0.4 * p(T3, t) + 0.3 * p(T6, t) + 0.3 * ma, 1), '안정': p(VOL, t), '돈의 흐름': p(FL, t)}
        evs = ev_us.get(t, [])
        adj = -8 if any(any(n in e['kind'] for n in NEG_US) for e in evs) else 0
        s_ = 0.75 * (0.30 * parts['수익성'] + 0.20 * parts['가치'] + 0.15 * parts['성장'] + 0.15 * parts['추세'] + 0.20 * parts['안정']) + 0.25 * sc + adj
        a_ = 0.75 * (0.30 * parts['성장'] + 0.25 * parts['추세'] + 0.15 * parts['수익성'] + 0.10 * parts['가치'] + 0.20 * parts['돈의 흐름']) + 0.25 * sc + adj
        an = ana.get(t) or {}
        rows.append({'code': t, 'name': x.get('name'), 'bucket': (us_sec.get(x['_b']) or {}).get('name') or x.get('sector'), 'sector_score': sc,
                     'stable': round(s_, 1), 'aggressive': round(a_, 1), 'parts': parts, 'member': mem.get(t),
                     'events': evs, 'analysts': an.get('n', 0), 'analyst_pt': an.get('pt'), 'f13': f13.get(t, []),
                     'facts': {k: x.get(k) for k in ('mcap_b', 'rev_g', 'rev_q_yoy', 'op_margin', 'ev_ebit', 'per', 'r1m', 'r3m', 'r6m', 'hi52', 'vol3m', 'last_q')}})
    out['sectors']['us'] = sorted(us_sec.values(), key=lambda r: r['score'], reverse=True)
    out['us'] = pick(rows, mem, must=lambda r: any(any(n in e['kind'] for n in BIG_US) for e in r['events']) or r['analysts'] >= 4 or 'new' in r['f13'])

    # ── 국장 ──
    KI = KR.get('items') or {}
    gbk = {}
    for c, x in KI.items():
        b = kr_bucket(x); x['_b'] = b
        if b and isinstance(x.get('sales_growth'), (int, float)): gbk.setdefault(b, []).append(x['sales_growth'])
    kr_sec = sector_scores('kr', S, {b: round(st.median(v), 1) for b, v in gbk.items() if len(v) >= 3})
    ev_kr = {}
    for e in ((E.get('kr') or {}).get('items') or []):
        ev_kr.setdefault(e['code'], []).append({'date': e['date'], 'kind': e['kind'], 'title': e.get('title')})
    both = {y['code'] for y in (D.get('both') or [])}
    q_rel, v_rel, flow = {}, {}, {}
    for c, x in KI.items():
        if isinstance(x.get('opm_recent'), (int, float)) and isinstance(x.get('sector_opm'), (int, float)): q_rel[c] = x['opm_recent'] - x['sector_opm']
        if isinstance(x.get('ev_rel'), (int, float)) and x['ev_rel'] > 0: v_rel[c] = -x['ev_rel']
        elif isinstance(x.get('per'), (int, float)) and x['per'] > 0 and isinstance(x.get('sectorPer'), (int, float)) and 0 < x['sectorPer'] < 200: v_rel[c] = -(x['per'] / x['sectorPer'])
        bd = lambda s_: int(str(s_).split('/')[0]) if s_ and '/' in str(s_) else 0
        flow[c] = bd(x.get('f_buydays')) + bd(x.get('p_buydays')) + (5 if c in both else 0)
    Q, V, ROE = pct_map(q_rel), pct_map(v_rel), pct_map({c: x.get('roe_ann') for c, x in KI.items()})
    G1, G2 = pct_map({c: x.get('sales_growth') for c, x in KI.items()}), pct_map({c: x.get('eps_growth') for c, x in KI.items()})
    T6, T1 = pct_map({c: x.get('excess_r6') for c, x in KI.items()}), pct_map({c: x.get('excess_r1') for c, x in KI.items()})
    DD, FL = pct_map({c: x.get('dd_close') for c, x in KI.items()}), pct_map(flow)
    mem = members(P, 'kr')
    rows = []
    for c, x in KI.items():
        sc = (kr_sec.get(x['_b']) or {}).get('score', 50.0)
        parts = {'수익성': round(0.7 * p(Q, c) + 0.3 * p(ROE, c), 1), '성장': round(0.6 * p(G1, c) + 0.4 * p(G2, c), 1), '가치': p(V, c),
                 '추세': round(0.4 * p(T6, c) + 0.3 * p(T1, c) + 0.3 * (100 if x.get('daily_aligned') else 30), 1), '안정': p(DD, c), '돈의 흐름': p(FL, c)}
        evs = ev_kr.get(c, [])
        adj = (5 if any(e['kind'] in POS_KR for e in evs) else 0) - (8 if any(e['kind'] in NEG_KR for e in evs) else 0)
        s_ = 0.75 * (0.30 * parts['수익성'] + 0.20 * parts['가치'] + 0.15 * parts['성장'] + 0.15 * parts['추세'] + 0.20 * parts['안정']) + 0.25 * sc + adj
        a_ = 0.75 * (0.30 * parts['성장'] + 0.25 * parts['추세'] + 0.15 * parts['수익성'] + 0.10 * parts['가치'] + 0.20 * parts['돈의 흐름']) + 0.25 * sc + adj
        rows.append({'code': c, 'name': x.get('name'), 'bucket': x['_b'] or x.get('sector'), 'sector_score': sc,
                     'stable': round(s_, 1), 'aggressive': round(a_, 1), 'parts': parts, 'member': mem.get(c),
                     'events': evs, 'both_flow': c in both,
                     'facts': {k: x.get(k) for k in ('mcap', 'per', 'pbr', 'opm_recent', 'sector_opm', 'sales_growth', 'eps_growth', 'roe_ann', 'ev_ebit', 'sector_ev_ebit', 'r1', 'r6', 'dd_close', 'f_buydays', 'p_buydays')}})
    # TOP 20 국장 종목 중 흑자 목록(kr_fund)에 없는 것(적자 성장주 등)도 후보에 남긴다 — 숫자는 Claude가 번들로 본다
    for c, t in mem.items():
        if c not in KI:
            rows.append({'code': c, 'name': None, 'bucket': None, 'sector_score': None, 'stable': None, 'aggressive': None, 'parts': {},
                         'member': t, 'events': ev_kr.get(c, []), 'both_flow': c in both, 'facts': {}, 'no_numbers': True})
    for c, evs in ev_kr.items():                                    # 흑자 목록 밖이라도 중대 공시가 난 기업은 꼭 볼 것으로
        if c not in KI and c not in mem and any(e['kind'] in BIG_KR for e in evs):
            rows.append({'code': c, 'name': None, 'bucket': None, 'sector_score': None, 'stable': None, 'aggressive': None, 'parts': {},
                         'member': None, 'events': evs, 'both_flow': c in both, 'facts': {}, 'no_numbers': True})
    out['sectors']['kr'] = sorted(kr_sec.values(), key=lambda r: r['score'], reverse=True)
    idx350 = {x['code'] for k in ('kospi', 'kosdaq') for x in (J('lists/mcap.json').get(k) or [])}
    for r in rows:                                                   # 새 후보 자격 — 지수 구성 종목, 안정형은 시총 1조원 이상
        if r['member']: continue
        if r['code'] not in idx350:
            r['stable'] = r['aggressive'] = None if not r.get('no_numbers') else r['stable']
        elif (r['facts'].get('mcap') or 0) < 10000 and r['stable'] is not None:
            r['stable_note'] = '시총 1조원 미만 — 안정형 후보 제외'; r['stable_rank_off'] = True
    out['kr'] = pick(rows, mem, must=lambda r: (r['code'] in idx350 or r.get('no_numbers')) and (any(e['kind'] in BIG_KR for e in r['events']) or r.get('both_flow')))
    nm = {x['code']: x['name'] for k in ('kospi', 'kosdaq') for x in (J('lists/mcap.json').get(k) or [])}
    for r in out['kr']:
        r['name'] = r['name'] or nm.get(r['code'])

    json.dump(out, open(os.path.join(ROOT, 'lists', 'candidates.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    for mk in ('us', 'kr'):
        L = out[mk]; tags = {}
        for r in L:
            for g in r['why_pick']:
                tags[g] = tags.get(g, 0) + 1
        print('%s 후보 %d개 %s · 업종 상위: %s' % (mk, len(L), tags, ', '.join('%s %.0f' % (s['name'], s['score']) for s in out['sectors'][mk][:5])))
    return 0


def pick(rows, mem, must):
    """후보 고르기 — 지금 TOP 20 전부 + 안정형·적극형 새 후보 상위 30씩 + 꼭 볼 것(최대 15)"""
    keep = {}
    for r in rows:
        if r['member']:
            keep[r['code']] = (r, ['지금 TOP 20'])
    new = [r for r in rows if not r['member'] and r['stable'] is not None]
    for key, lab in (('stable', '안정형 점수 상위'), ('aggressive', '적극형 점수 상위')):
        pool = [r for r in new if not (key == 'stable' and r.get('stable_rank_off'))]
        for r in sorted(pool, key=lambda r: r[key], reverse=True)[:30]:
            keep.setdefault(r['code'], (r, []))[1].append(lab)
    extra = [r for r in rows if r['code'] not in keep and must(r)]
    extra.sort(key=lambda r: max(r['stable'] or 0, r['aggressive'] or 0), reverse=True)
    for r in extra[:15]:
        keep[r['code']] = (r, ['꼭 볼 것(사건·애널·13F·동시 수급)'])
    out = []
    for r, why in keep.values():
        r = dict(r); r['why_pick'] = why
        out.append(r)
    out.sort(key=lambda r: max(r['stable'] or 0, r['aggressive'] or 0), reverse=True)
    return out


if __name__ == '__main__':
    raise SystemExit(main())
