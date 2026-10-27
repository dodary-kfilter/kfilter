# -*- coding: utf-8 -*-
"""시총순 탭 자료 — 시가총액 큰 순서로 코스피 상위 200·코스닥 상위 150, S&P 500 전 종목·나스닥 100 전 종목 → lists/mcap.json

사용(저장소 루트): python3 tools/mcap_fetch.py            # lists/mcap.json을 새로 쓴다
                 python3 tools/mcap_fetch.py --dry      # 쓰지 않고 요약만
GitHub Actions(.github/workflows/mcap.yml)가 하루 두 번(한국시간 07:20 미장 마감 뒤, 16:40 국장 마감 뒤) 돌린다.

자료
  - 국장: 다음 금융 시가총액 순위(코스피·코스닥) — 보통주만(ETF·ETN·우선주·스팩·리츠 제외, cache/fundamentals.json의 주식 목록 기준)
          외국인 지분율은 다음 순위표 값 (PER·PBR은 싣지 않음 — 펀더멘털 캐시의 주당순이익이 최신 실적과 어긋날 수 있어 숫자가 오해를 부른다)
  - 미장: 구성 종목은 S&P 500(위키백과 원문)·나스닥 100(나스닥 API), 시총·가격·등락·업종은 나스닥 스크리너(미국 상장 전 종목 한 번에)
          한글 이름은 lists/names_ko.json에 있으면 붙인다
  - 한 시장이라도 너무 적게 받히면(정상의 70% 미만) 그 시장은 이전 파일 값을 그대로 둔다 — 빈 목록으로 덮지 않는다
"""
import json, os, re, sys, time, datetime as dt, urllib.request

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
OUT = os.path.join(ROOT, 'lists', 'mcap.json')
FUND = os.path.join(ROOT, 'cache', 'fundamentals.json')
NAMES = os.path.join(ROOT, 'lists', 'names_ko.json')
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'
KST = dt.timezone(dt.timedelta(hours=9))
N = {'kospi': 200, 'kosdaq': 150}


def get(url, hdr=None, as_json=True, tries=3):
    h = {'User-Agent': UA}; h.update(hdr or {})
    for i in range(tries):
        try:
            b = urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=40).read()
            return json.loads(b) if as_json else b.decode('utf-8', 'replace')
        except Exception:
            time.sleep(2 * (i + 1))
    return None


def num(s):
    try:
        return float(str(s).replace('$', '').replace(',', '').replace('%', '').strip())
    except Exception:
        return None


def kr(fd):
    out = {}
    for key, mk in (('kospi', 'KOSPI'), ('kosdaq', 'KOSDAQ')):
        got = []
        for page in range(1, 6):
            j = get('https://finance.daum.net/api/trend/market_capitalization?page=%d&perPage=200&fieldName=marketCap'
                    '&order=desc&market=%s&pagination=true' % (page, mk), {'Referer': 'https://finance.daum.net/'})
            rows = (j or {}).get('data') or []
            for r in rows:
                sc = r.get('symbolCode') or ''; name = r.get('name') or ''; f = fd.get(sc) or {}
                if not f or not sc[1:].endswith('0') or '스팩' in name or '리츠' in name or f.get('sector') == '부동산':
                    continue                                   # 주식 목록에 없는 ETF·ETN, 우선주, 스팩, 리츠는 뺀다
                px = r.get('tradePrice')
                got.append({'code': sc[1:], 'name': name, 'mcap': r.get('marketCap'), 'price': px,
                            'chg': round((r.get('changeRate') or 0) * 100 * (-1 if r.get('change') == 'FALL' else 1), 2),
                            'sector': f.get('sector') or '', 'foreign': round(num(r.get('foreignRatio')) * 100, 1) if num(r.get('foreignRatio')) is not None else None})
                if len(got) >= N[key]:
                    break
            if len(got) >= N[key] or not rows:
                break
        for i, r in enumerate(got, 1):
            r['rank'] = i
        out[key] = got
    return out


def clean(nm):
    return re.sub(r'\s+(Common Stock|Class [A-C] (Common|Ordinary|Capital) Stock.*|Ordinary Shares.*|American Depositary.*|Common Shares.*)$', '', nm or '').strip()


def us(names):
    t = get('https://en.wikipedia.org/w/index.php?title=List_of_S%26P_500_companies&action=raw',
            {'User-Agent': 'kfilter/1.0 (github.com/dodary-kfilter)'}, as_json=False) or ''
    seg = t[t.find('{|'):t.find('|}', t.find('{|'))]
    sp = {}
    for row in seg.split('|-')[1:]:
        m = re.search(r'\{\{\s*(?:NyseSymbol|NasdaqSymbol|Nyse symbol|Nasdaq symbol|BZX link|Cboe symbol)\s*\|\s*([A-Z.]+)', row, re.I)
        if not m:
            continue
        cells = [c.strip() for c in row.split('||') if c.strip()]
        name = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', cells[1] if len(cells) > 1 else '').strip(' |')
        sp[m.group(1).upper()] = (name, cells[2].strip(' |') if len(cells) > 2 else '')
    j = get('https://api.nasdaq.com/api/quote/list-type/nasdaq100', {'Accept': 'application/json'})
    nq = {r['symbol']: r for r in (((j or {}).get('data') or {}).get('data') or {}).get('rows') or []}
    s = get('https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=10000&download=true', {'Accept': 'application/json'})
    scr = {}
    for r in ((s or {}).get('data') or {}).get('rows') or []:
        scr[re.sub(r'[/\-]', '.', (r.get('symbol') or '').strip().upper())] = r
    def row(tk, name, sector):
        r = scr.get(tk) or {}
        return {'code': tk, 'name': name or clean(r.get('name')) or tk, 'name_ko': names.get(tk, ''),
                'mcap': num(r.get('marketCap')), 'price': num(r.get('lastsale')), 'chg': num(r.get('pctchange')),
                'sector': sector or r.get('sector') or '', 'industry': r.get('industry') or ''}
    out = {'sp500': [row(tk, nm, sec) for tk, (nm, sec) in sp.items()],
           'ndx': [row(tk, (sp.get(tk) or ('', ''))[0] or clean(r.get('companyName')), (sp.get(tk) or ('', ''))[1]) for tk, r in nq.items()]}
    for k in out:
        out[k].sort(key=lambda z: -(z['mcap'] or 0))
        for i, z in enumerate(out[k], 1):
            z['rank'] = i
    return out


def main(argv):
    dry = '--dry' in argv
    fd = (json.load(open(FUND, encoding='utf-8')).get('data') or {}) if os.path.exists(FUND) else {}
    names = json.load(open(NAMES, encoding='utf-8')) if os.path.exists(NAMES) else {}
    old = json.load(open(OUT, encoding='utf-8')) if os.path.exists(OUT) else {}
    got = {}
    got.update(kr(fd)); got.update(us(names))
    need = {'kospi': 200, 'kosdaq': 150, 'sp500': 490, 'ndx': 98}
    kept = []
    for k, n in need.items():
        if len(got.get(k) or []) < 0.7 * n or sum(1 for z in got.get(k) or [] if z.get('mcap')) < 0.7 * n:
            if old.get(k):
                got[k] = old[k]; kept.append(k)                    # 너무 적게 받히면 이전 값 유지
    now = dt.datetime.now(KST)
    out = {'meta': {'updated': now.strftime('%Y-%m-%d %H:%M'), 'kept_old': kept,
                    'rule': '시가총액 큰 순 · 코스피 상위 200·코스닥 상위 150(보통주 — ETF·ETN·우선주·스팩·리츠 제외) · S&P 500·나스닥 100 전 종목',
                    'counts': {k: len(got.get(k) or []) for k in need}}}
    out.update({k: got.get(k) or [] for k in need})
    print('받음', out['meta']['counts'], '· 이전 값 유지', kept or '없음')
    for k in need:
        print(' ', k, ', '.join('%s %s' % ((z.get('name_ko') or z['name'])[:8], z['rank']) for z in out[k][:5]))
    if dry:
        return 0
    if all(not out[k] for k in need):
        sys.exit('★ 하나도 받지 못함 — 쓰지 않음')
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':')); f.write('\n')
    print('저장:', os.path.relpath(OUT, ROOT), '%.0f KB' % (os.path.getsize(OUT) / 1024))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
