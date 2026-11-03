# -*- coding: utf-8 -*-
"""국장 공시 사건 — 코스피 200·코스닥 150 기업의 최근 3일 공시를 사건 종류로 나눈다 (깔때기 3·4단계 재료, 2026-11-03)

사용: python3 tools/kr_events_fetch.py   → lists/events.json의 kr (30초 안팎)
자료: 다음 금융 공시 목록(종목별 최근 40건)
판단을 바꿀 만한 공시만 남긴다 — 합병·분할·인수·양수도·공급계약·증자·감자·자사주·배당·실적·최대주주·소송·투자 등.
같은 날 같은 종류는 하나로 묶는다(정정 공시가 여러 건 붙는 경우).
"""
import json, os, re, sys, time, datetime as dt, urllib.request
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
KST = dt.timezone(dt.timedelta(hours=9))
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128.0 Safari/537.36'
H = {'User-Agent': UA, 'Referer': 'https://finance.daum.net/', 'Accept': 'application/json'}
# (정규식, 종류) — 위에서부터 먼저 맞는 것
KINDS = [
    (r'합병', '합병'), (r'분할', '회사 분할'), (r'타법인주식.*취득|출자증권.*취득|주식.*양수|인수', '다른 회사 인수'),
    (r'타법인주식.*처분|출자증권.*처분|주식.*양도', '다른 회사 지분 처분'), (r'영업양수|영업양도|자산양수|자산양도|양수도', '사업·자산 양수도'),
    (r'해지|철회|취소', '계약 해지·철회'), (r'단일판매|공급계약', '대규모 공급계약'), (r'유상증자', '유상증자'), (r'무상증자', '무상증자'), (r'감자', '감자'),
    (r'전환사채|신주인수권부사채|교환사채', '주식 연계 채권 발행'), (r'자기주식.*소각|주식소각', '자사주 소각'),
    (r'자기주식.*취득|자사주.*취득', '자사주 매입'), (r'자기주식.*처분', '자사주 처분'),
    (r'현금.*배당|현물배당|배당', '배당'), (r'잠정.*실적|영업\(잠정\)|실적', '실적 발표'),
    (r'최대주주.*변경|경영권', '최대주주 변경'), (r'소송|가압류|가처분', '소송'), (r'신규시설투자|시설투자', '시설 투자'),
    (r'공개매수', '공개매수'), (r'상장폐지|관리종목|거래정지|매매거래정지', '상장 유지 문제'),
    (r'조회공시|풍문|보도', '조회 공시(풍문·보도 해명)'), (r'투자판단.*주요경영사항|주요경영사항', '투자 판단 주요 경영사항'),
]
SKIP = re.compile(r'기업설명회|IR|대규모기업집단|임원ㆍ주요주주|주식등의대량보유|최대주주등소유주식변동|특수관계인|자율공시\)?$|기타 경영사항')


def kind_of(title):
    t = re.sub(r'^\S+\(주\)\s*|^\(주\)\S+\s*', '', title)
    if SKIP.search(t) and not re.search(r'합병|인수|공급계약|소각', t):
        return None
    for pat, k in KINDS:
        if re.search(pat, t):
            return k
    return None


def fetch(code):
    url = 'https://finance.daum.net/api/disclosures?symbolCode=A%s&perPage=40&page=1' % code
    for i in range(3):
        try:
            return (json.loads(urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=15).read()) or {}).get('data') or []
        except Exception:
            time.sleep(1 + i)
    return None


def main():
    t0 = time.time()
    M = json.load(open(os.path.join(ROOT, 'lists', 'mcap.json'), encoding='utf-8'))
    uni = {x['code']: x for key in ('kospi', 'kosdaq') for x in (M.get(key) or [])}
    since = (dt.datetime.now(KST).date() - dt.timedelta(days=3)).isoformat()
    codes = list(uni)
    with ThreadPoolExecutor(8) as ex:
        res = list(ex.map(fetch, codes))
    evs, miss = [], []
    for code, rows in zip(codes, res):
        if rows is None:
            miss.append(code); continue
        seen = set()
        for r in rows:
            d = (r.get('createdAt') or '')[:10]
            if d < since:
                continue
            k = kind_of(r.get('title') or '')
            if not k or (d, k) in seen:
                continue
            seen.add((d, k))
            u = uni[code]
            evs.append({'code': code, 'name': u.get('name'), 'sector': u.get('sector'), 'date': d,
                        'time': (r.get('createdAt') or '')[11:16], 'kind': k, 'title': r.get('title')})
    if len(miss) > len(codes) * 0.4:
        print('★ 공시를 절반 가까이 못 받음 — 저장하지 않음', len(miss)); return 1
    evs.sort(key=lambda e: (e['date'], e['time']), reverse=True)
    ep = os.path.join(ROOT, 'lists', 'events.json')
    E = json.load(open(ep, encoding='utf-8')) if os.path.exists(ep) else {}
    E['kr'] = {'updated': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M'), 'since': since, 'items': evs, 'missing': miss}
    json.dump(E, open(ep, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    from collections import Counter
    print('저장: lists/events.json kr — %d개 기업 중 사건 %d건(%s 이후) · 못 받음 %d · %.0f초'
          % (len(codes), len(evs), since, len(miss), time.time() - t0))
    print(' 종류:', Counter(e['kind'] for e in evs).most_common(10))
    return 0


if __name__ == '__main__':
    sys.exit(main())
