#!/usr/bin/env python3
"""번들 기계 검사 — 설계에 쓰지 않은 여러 종목으로 번들이 어디서 깨지는지 본다.
★한 종목에서만 나온 문제로는 번들 규칙을 고치지 않는다. 여기서 반복되는 실패만 고친다.
  사용(저장소 루트): python3 tools/bundle_check.py [추가코드,추가코드] [--part 1/2]
  --part k/n = 표본을 n등분해 k번째만 — 한 번에 오래 걸리는 환경에서 나눠 돌린다
  --us       = 미장: 사이트 미국 종목 사전(index.html)에서 개별 종목을 일정 간격으로 뽑는다. EXCLUDE_US는 설계에 쓴 티커
표본 = data.json의 보유 전부 + 동시수급·저가매수·모멘텀 목록 앞에서 4개씩 + 추가 코드.
EXCLUDE = 번들 설계·수정 때 대조에 쓴 종목. 판정에서 뺀다."""
import json, re, subprocess, sys, time, urllib.request
EXCLUDE = {'440110', '005930', '001820'}
EXCLUDE_US = {'MU'}


def sample_us(k=10):
    h = open('index.html', encoding='utf-8').read()
    tk = [m.group(1) for m in re.finditer(r'\[\s*["\']([A-Z][A-Z.\-]*)["\']\s*,\s*["\']stocks["\']', h)]
    tk = [x for i, x in enumerate(tk) if x not in tk[:i] and x not in EXCLUDE_US]
    step = max(1, len(tk) // k)
    return [(x, '미장') for x in tk[::step][:k]], len(tk)


def one_us(t):
    tk, lab = t
    t0 = time.time()
    r = subprocess.run([sys.executable, 'tools/bundle.py', 'us', tk], capture_output=True, text=True, timeout=180)
    o, dt = r.stdout, time.time() - t0
    def line(tag):
        m = re.search(r'(?m)^' + re.escape(tag) + r'.*$', o)
        return m.group(0) if m else ''
    er = re.search(r'#오류 (.*)', o)
    nerr = 0 if not er or er.group(1) == '없음' else len(json.loads(er.group(1)))
    sec = re.search(r'#SEC 최근 공시 (\d+)건', o); nw = re.search(r'#뉴스 .*?(\d+)건', o)
    sur = re.search(r'EPS서프라이즈[^:]*:(\[.*?\]\])', line('#실적·컨센·목표가'))
    flag = []
    if r.returncode: flag.append('비정상종료')
    if not line('#시세') or '조회 실패' in line('#시세'): flag.append('시세없음')
    if not line('#가격'): flag.append('가격없음')
    if '"손익"' not in line('#재무'): flag.append('재무없음')
    if not sur: flag.append('실적없음')
    if '"기관보유":{}' in line('#기관·공매도·내부자'): flag.append('기관없음')
    if nerr: flag.append('오류%d' % nerr)
    name = re.search(r'==== \S+ (.*) ====', o)
    return '%-5s %-22s %4.1fs %2dK자 SEC%2s 뉴스%s %s' % (tk, (name.group(1) if name else '')[:22], dt, len(o) // 1000,
            sec.group(1) if sec else '-', nw.group(1) if nw else '-', ' '.join(flag) or '정상'), bool(flag)
SECS = ['매출·수주', '재무상태표(발췌)', '손익계산서(발췌)', '기타 재무', '주주', '우발부채·소송', '작성기준일 이후']
H = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.daum.net/'}


def sample(extra):
    d = json.load(open('data.json', encoding='utf-8'))
    def codes(k):
        v = d.get(k)
        return [str(x.get('code') or '') for x in (v if isinstance(v, list) else []) if re.fullmatch(r'\d{6}', str(x.get('code') or ''))]
    seen, pick = set(EXCLUDE), []
    for c in codes('portfolio'):
        if c not in seen: pick.append((c, '보유')); seen.add(c)
    for k, lab in (('both', '동시'), ('value_pick', '저가'), ('momentum', '모멘텀')):
        for c in [c for c in codes(k) if c not in seen][:4]:
            pick.append((c, lab)); seen.add(c)
    for c in extra:
        if c not in seen: pick.append((c, '추가')); seen.add(c)
    return pick


def one(t):
    c, lab = t
    try:
        q = json.loads(urllib.request.urlopen(urllib.request.Request('https://finance.daum.net/api/quotes/A%s?summary=false' % c, headers=H), timeout=15).read())
    except Exception:
        q = {}
    sec, tg = q.get('wicsSectorName') or '', []
    if re.search(r'은행|보험|증권|금융|카드|캐피탈', sec): tg.append('금융')
    if (q.get('netIncome') or 0) < 0: tg.append('적자')
    if (q.get('listingDate') or '')[:10] >= time.strftime('%Y-%m-%d', time.localtime(time.time() - 365 * 86400)): tg.append('신규상장')
    if (q.get('marketCapRank') or 999) <= 30: tg.append('대형')
    if not sec: tg.append('업종없음')
    t0 = time.time()
    r = subprocess.run([sys.executable, 'tools/bundle.py', 'kr', c], capture_output=True, text=True, timeout=180)
    o, dt = r.stdout, time.time() - t0
    blocks, cur, buf = [], None, []
    for ln in o.split('\n') + ['#끝']:
        if ln.startswith(('▶ ', '▷ ', '#')):
            if cur: blocks.append((cur, '\n'.join(buf)))
            cur, buf = (ln if ln.startswith(('▶ ', '▷ ')) else None), []
        elif cur:
            buf.append(ln)
    docs = [b for h, b in blocks if h.startswith('▶')]
    secs = {h[2:].split(' — ')[0]: b for h, b in blocks if h.startswith('▷')}
    has_per = '#정기보고서 ' in o and '안에 없음' not in o
    miss = [s for s in SECS if s not in secs] if has_per else []
    short = [s for s in SECS if s in secs and len(secs[s]) < 60 and '해당' not in secs[s]]
    m = re.search(r'#공시목록 출처 (\S+) .*?\((\d+)건', o)
    er = re.search(r'#오류 (.*)', o)
    nerr = 0 if not er or er.group(1) == '없음' else len(json.loads(er.group(1)))
    return dict(c=c, n=(q.get('name') or '')[:8], lab=lab, tg='·'.join(tg) or '-', file='#수급파일 kfilter' in o, dt=dt, sz=len(o),
                src=(m.group(1)[:4] if m else '-'), cnt=int(m.group(2)) if m else 0, doc=len(docs),
                empty=sum(1 for b in docs if len(b) < 80), per=has_per, miss=miss, short=short, err=nerr, code=r.returncode)


def main():
    args = [a for a in sys.argv[1:]]
    if '--us' in args:
        pick, total = sample_us()
        print('미장 표본 %d개 — 사전 개별 종목 %d개에서 일정 간격 (설계에 쓴 %s 제외)' % (len(pick), total, ','.join(sorted(EXCLUDE_US))))
        bad = 0
        for p in pick:
            ln, b = one_us(p)
            print(ln, flush=True)
            bad += b
            time.sleep(1.5)
        print('문제 종목 %d/%d' % (bad, len(pick)))
        return 0
    part = None
    if '--part' in args:
        i = args.index('--part'); part = tuple(int(v) for v in args[i + 1].split('/')); del args[i:i + 2]
    extra = [x for x in (args[0].split(',') if args else []) if x]
    pick = sample(extra)
    if part:
        k, n = part
        pick = pick[(k - 1) * len(pick) // n: k * len(pick) // n]
    res, bad = [], 0
    for p in pick:                       # 실제 사용처럼 한 종목씩 — 몰아치면 DART 접속 제한이 검사를 오염시킨다
        x = one(p)
        res.append(x)
        time.sleep(1.5)
        flag = []
        if x['code']: flag.append('비정상종료'); bad += 1
        if x['err']: flag.append('오류%d' % x['err'])
        if x['miss']: flag.append('절없음:' + ','.join(x['miss']))
        if x['short']: flag.append('절빈약:' + ','.join(x['short']))
        if x['empty']: flag.append('빈본문%d' % x['empty'])
        print('%s %-8s %-3s [%s] 파일%s %4.1fs %3dK자 공시%3d(%s) 본문%2d 정기%s %s' % (
            x['c'], x['n'], x['lab'], x['tg'], 'O' if x['file'] else 'X', x['dt'], x['sz'] // 1000, x['cnt'], x['src'],
            x['doc'], 'O' if x['per'] else 'X', ' '.join(flag) or '정상'), flush=True)
    per = [x for x in res if x['per']]
    print('\n표본 %d · 비정상종료 %d · 오류 %d · 평균 %.1f초 · 정기보고서 %d/%d · 빈 본문 %d/%d' % (
        len(res), bad, sum(x['err'] for x in res), sum(x['dt'] for x in res) / max(1, len(res)),
        len(per), len(res), sum(x['empty'] for x in res), sum(x['doc'] for x in res)))
    for s in SECS:
        print('   %s: 헤더 %d/%d · 빈약 %d' % (s, sum(1 for x in per if s not in x['miss']), len(per), sum(1 for x in per if s in x['short'])))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
