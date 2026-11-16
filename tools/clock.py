# -*- coding: utf-8 -*-
"""시계 — 제시각에 수집을 직접 부르고, 대상마다 '최근 언제 돌았나 · 내용은 언제 것인가'를 확인한다 (2026-11-16)

왜
  GitHub 예약 실행은 이 저장소에서 제대로 돌지 않는다 — 11/9~11/13 수급 스캔은 예약 16번 중 하루 4~5번, 몇 시간씩 늦게 돌았고,
  예약으로 돌던 점검 담당(watchdog)도 하루 13번 중 4~5번만 돌아 빠진 수집을 못 메웠다.
  직접 부른 실행(workflow_dispatch)과 push로 도는 실행은 바로 돈다(11/16 12:01 부른 스캔 → 12:06 끝). 그래서 시계가 직접 부른다.

어떻게 뜨나
  Claude 루틴(07:30·12:30·16:30·21:30, Anthropic 예약이라 제때 돈다)이 문패(claude/running-*)를 달거나 판단(claude/judge-*)을 올리면
  그 push로 이 시계가 뜬다(.github/workflows/clock.yml). 한 번에 최대 5시간 40분 돌고, 끝나기 전에 다음 시계를 직접 띄워 이어 간다.
  시계는 한 번에 하나만 — 이미 돌고 있으면 새로 뜬 쪽이 바로 끝난다.

5분마다
  1. 대상마다 '가장 최근에 지난 회차'를 본다. 내용이 그 회차 것이 아니면 수집을 부른다(내용이 이미 그 회차 것이면 부르지 않는다).
     부른 뒤 기다림(대상마다 15~20분)이 지나도 내용이 그대로면 다시 부른다 — 한 회차에 최대 2번. 그래도 안 되면 '실패'로 남기고 멈춘다.
  2. 출처를 직접 확인한다(15분마다) — 국장 최근 거래일(다음 시세), 국장 수급 최근 날짜(다음 투자자별 매매), 미장 최근 거래일(야후 SPY).
     출처에 우리 것보다 새 날짜가 있으면 회차와 상관없이 부른다(같은 출처 날짜에 최대 3번).
     다음 금융은 그날 외국인·기관 수급을 저녁에야 올린다 — 장중 스캔은 가격만 새로 하고, 수급은 출처에 오늘 것이 나오면 그때 받는다.
  3. 매일 판단은 08:30까지 오늘 판단이 없으면 루틴을 한 번 깨운다(자동은 하루 1번, 안에 루틴이 있으면 노크만).
  4. 결과를 status 브랜치의 health.json에 쓴다 — 사이트가 읽는다(main 기록을 어지럽히지 않게 따로).

한도(폭주 막기): 하루 부르기 60번 · 회차마다 다시 부르기 2번 · 하루 시계 시작 12번 · 루틴 자동 깨우기 하루 1번
사용: python3 tools/clock.py            # 시계(GitHub Actions에서)
      python3 tools/clock.py --once     # 한 번만 확인하고 끝(부르기·쓰기 없이 결과만 출력, --out 파일로 health 저장)
"""
import json, os, re, sys, time, subprocess, datetime as dt, urllib.request, urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
sys.path.insert(0, os.path.join(ROOT, 'lists', 'tools'))
import watchdog as W                                   # gh·running_lock·sweep_locks·parse·rd
import f13_fetch                                       # 13F 확인 기간(watching) — 수집기와 같은 기준

KST = W.KST
TICK = 300                     # 5분
LIFE = 5 * 3600 + 40 * 60      # 한 번에 5시간 40분 — GitHub 작업 한도 6시간 전에 다음 시계로 넘긴다
DAY_CAP, TRIES, STARTS_CAP, SRC_TRIES = 60, 2, 12, 3
FRESH = dt.timedelta(minutes=15)       # 회차 시각보다 이만큼 앞선 내용까지는 그 회차 것으로 본다(버튼으로 방금 받은 경우)
WD, ALL = (0, 1, 2, 3, 4), tuple(range(7))
SCAN_SLOTS = ('09:30', '10:30', '11:30', '12:30', '13:30', '14:30', '15:30', '19:30')

# 대상: 이름, 워크플로, 회차[(요일, 시각)], 기다림(분), 국장 거래일에만
JOBS = {
    'scan':      ('수급', 'update.yml', [(WD, h) for h in SCAN_SLOTS], 20, True),
    'materials': ('깔때기 재료', 'materials.yml', [(ALL, '06:40'), (ALL, '12:10')], 20, False),
    'analyst':   ('애널 추천', 'analyst.yml', [(ALL, '07:00')], 20, False),
    'mcap':      ('시총순', 'mcap.yml', [(ALL, '07:20'), (WD, '16:40')], 15, False),
    'macro':     ('거시', 'macro.yml', [(ALL, '07:20'), (ALL, '23:10')], 15, False),
    'top20':     ('TOP 20 가격', 'top20.yml', [(WD, '16:40')], 15, True),
    'f13':       ('13F', 'f13.yml', [(ALL, '03:00')], 15, False),
}
ORDER = ['scan', 'materials', 'analyst', 'mcap', 'macro', 'top20', 'f13', 'judge']
BUSY = ('queued', 'in_progress', 'waiting', 'requested', 'pending')
HDR_DAUM = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36',
            'Referer': 'https://finance.daum.net/'}


def J(p):
    return W.rd(p)


def hm(t):
    return t.strftime('%m-%d %H:%M') if t else '—'


def md(s):
    s = str(s or '')
    return '%d/%d' % (int(s[5:7]), int(s[8:10])) if re.match(r'^\d{4}-\d{2}-\d{2}', s) else (s or '—')


def at(day, hhmm):
    h, m = hhmm.split(':')
    return dt.datetime(day.year, day.month, day.day, int(h), int(m), tzinfo=KST)


# ── 출처 확인 ──
def get_json(url, headers=None, timeout=15):
    try:
        req = urllib.request.Request(url, headers=headers or {'User-Agent': 'Mozilla/5.0'})
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    except Exception:
        return None


def sources():
    """출처의 최근 날짜 — kr_date(국장 최근 거래일) · flows_date(국장 수급 최근 날짜) · us_date(미장 최근 거래일). 못 받으면 None"""
    out = {'checked': dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M')}
    q = get_json('https://finance.daum.net/api/quotes/A005930?summary=false', HDR_DAUM)
    out['kr_date'] = str(q.get('date'))[:10] if q and q.get('date') else None
    f = get_json('https://finance.daum.net/api/investor/days?symbolCode=A005930&page=1&perPage=2', HDR_DAUM)
    rows = (f or {}).get('data') or []
    out['flows_date'] = str(rows[0].get('date'))[:10] if rows and rows[0].get('date') else None
    y = get_json('https://query1.finance.yahoo.com/v8/finance/chart/SPY?range=5d&interval=1d')
    try:
        r = y['chart']['result'][0]
        m_ = r.get('meta') or {}; tl = r['timestamp']; off = m_.get('gmtoffset') or 0
        reg = (m_.get('currentTradingPeriod') or {}).get('regular') or {}
        ts = tl[-1]
        if reg.get('start') and reg.get('end') and reg['start'] <= time.time() < reg['end'] and len(tl) >= 2:
            ts = tl[-2]          # 미장 장중 — 오늘 봉은 아직 종가가 아니다(11/16 23:36 장 시작 6분 뒤 값을 '종가'로 받은 일)
        out['us_date'] = dt.datetime.fromtimestamp(ts + off, dt.timezone.utc).date().isoformat()
    except Exception:
        out['us_date'] = None
    return out


# ── 내용 확인 — 대상마다 (회차 S의 내용이 맞나, 내용 기준 글, 문제, 출처에 새 자료) ──
def content(key, S, src, now):
    if key == 'scan':
        d = J('data.json'); t = W.parse(d.get('updated')); m_ = d.get('market_date')
        bad = '출처 응답 없음 — 직전 결과를 그대로 둠' if re.search('이전 결과|응답 없음|실패', d.get('supply_note') or '') else None
        new = (('출처(다음)에 %s 수급이 나옴' % md(src['flows_date'])), src['flows_date']) if src.get('flows_date') and m_ and src['flows_date'] > m_ else None
        return (bool(t and S and t >= S - FRESH) and not bad), '가격 %s · 수급 %s' % (hm(t), md(m_)), bad, new
    if key == 'materials':
        a, c = W.parse(J('lists/sectors.json').get('updated')), J('lists/candidates.json')
        b = W.parse(c.get('updated')); ix = J('lists/13f/index.json')
        bad = None
        if ix.get('latest') and c.get('f13_quarter') != ix.get('latest'):
            bad = ('후보 표에 13F가 안 들어감(최신 %s)' % qname(ix['latest']) if not c.get('f13_quarter')
                   else '후보 표가 13F %s를 씀(최신 %s)' % (qname(c['f13_quarter']), qname(ix['latest'])))
        t = min(a, b) if (a and b) else None
        return (bool(t and S and t >= S - FRESH) and not bad), '업종 %s · 후보 %s' % (hm(a), hm(b)), bad, None
    if key == 'analyst':
        d = J('lists/analyst.json'); t = W.parse(d.get('fetched_at')); n, f = len(d.get('analysts') or []), len(d.get('failed') or [])
        bad = '못 받은 애널리스트가 절반 넘음(%d/%d명)' % (f, n) if n and f > n / 2 else None
        return (bool(t and S and t >= S - FRESH) and not bad), '%s%s' % (hm(t), ' · 못 받은 %d명은 전날 자료' % f if f else ''), bad, None
    if key == 'mcap':
        m_ = J('lists/mcap.json').get('meta') or {}; t = W.parse(m_.get('updated'))
        bad = '예전 자료로 둔 시장: %s' % ', '.join(m_.get('kept_old')) if m_.get('kept_old') else None
        return (bool(t and S and t >= S - FRESH) and not bad), hm(t), bad, None
    if key == 'macro':
        d = J('cache/macro.json'); t = W.parse(d.get('collected_at'))
        bad = '못 받은 항목 %d개' % len(d.get('err')) if d.get('err') else None
        return bool(t and S and t >= S - FRESH), hm(t) + (' · ' + bad if bad else ''), None, None
    if key == 'top20':
        mt = J('top20/top20.json').get('meta') or {}
        g = re.search(r'국장 (\d{4}-\d{2}-\d{2}), 미장 (\d{4}-\d{2}-\d{2})', mt.get('basis') or '')
        kr, us = (g.group(1), g.group(2)) if g else (None, None)
        lag = [(x, b_) for x, a_, b_ in (('국장', kr, src.get('kr_date')), ('미장', us, src.get('us_date'))) if a_ and b_ and b_ > a_]
        new = (('출처에 더 새 종가가 있음(%s)' % '·'.join(x for x, _ in lag)), max(b_ for _, b_ in lag)) if lag and now.strftime('%H:%M') >= '15:40' else None
        exp = S.date().isoformat() if S else None                 # 회차 날의 국장 종가 — 그날이 휴장이면 출처의 최근 거래일까지
        if exp and src.get('kr_date') and src['kr_date'] < exp:
            exp = src['kr_date']
        ok = bool(kr) and (not exp or kr >= exp)
        return ok, '국장 %s · 미장 %s 종가' % (md(kr), md(us)), None, new
    if key == 'f13':
        ix = J('lists/13f/index.json'); ck = ix.get('check') or {}; t = W.parse(ck.get('at'))
        w = f13_fetch.watching(now.date())
        q = J('lists/13f/%s.json' % ix.get('latest')) if ix.get('latest') else {}
        have = '%s%s' % (qname(ix.get('latest')), '(잠정)' if q.get('preliminary') else '')
        if not w:
            return True, '반영 %s · 다음 분기 공시 대기' % have, None, None
        src_t = '%s 공시 %s/%s명(%s 확인)' % (qname(ck.get('quarter')), ck.get('filed', '—'), ck.get('tracked', '—'), hm(t)) if ck else '출처 확인 전'
        return bool(t and S and t >= S - FRESH), '반영 %s · %s' % (have, src_t), None, None
    if key == 'judge':
        j = W.parse((J('top20/picks.json').get('meta') or {}).get('judged_at'))
        return bool(j and j.date() >= now.date()), hm(j), None, None
    return True, '—', None, None


def qname(k):
    m = re.match(r'^(\d{4})Q(\d)$', str(k or ''))
    return '%s년 %s분기' % (m.group(1)[2:], m.group(2)) if m else '—'


# ── 일정 ──
def last_slot(key, now):
    """지금까지 지난 회차 중 가장 최근(어제까지 거슬러 본다) — 없으면 None"""
    best = None
    for back in (0, 1, 2, 3):
        day = (now - dt.timedelta(days=back)).date()
        for days, h in JOBS[key][2]:
            S = at(day, h)
            if day.weekday() in days and S <= now and (best is None or S > best):
                best = S
        if best:
            return best
    return None


def next_slot(key, now):
    for ahead in range(0, 8):
        day = (now + dt.timedelta(days=ahead)).date()
        c = sorted(at(day, h) for days, h in JOBS[key][2] if day.weekday() in days and at(day, h) > now)
        if c:
            return c[0]
    return None


# ── GitHub ──
def runs_latest():
    st, j = W.gh('GET', '/actions/runs?per_page=100')
    out = {}
    for r in (j or {}).get('workflow_runs', []):
        f = os.path.basename(r.get('path') or '')
        if f and f not in out:
            out[f] = r
    return out, st


def run_text(r):
    if not r:
        return '기록 없음', None
    t = dt.datetime.strptime(r['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).astimezone(KST)
    s = '진행 중' if r.get('status') in BUSY else {'success': '성공', 'failure': '실패', 'cancelled': '취소', 'skipped': '건너뜀'}.get(r.get('conclusion'), r.get('conclusion') or '?')
    return '%s %s' % (hm(t), s), r.get('status') in BUSY


def dispatch(wf, H):
    if H['day']['dispatches'] >= DAY_CAP:
        return False, '하루 한도(%d번)를 다 써서 부르지 않음' % DAY_CAP
    st, _ = W.gh('POST', '/actions/workflows/%s/dispatches' % wf, {'ref': 'main'})
    if st == 204:
        H['day']['dispatches'] += 1
        return True, ''
    return False, '부르기 실패(%s)' % st


# ── 한 번 보기 ──
def tick(H, now, src, runs, act=True):
    """대상마다 상태를 정하고(필요하면 부른다) H['items']를 새로 쓴다. act=False면 부르지 않는다"""
    today = now.strftime('%Y-%m-%d')
    slots = H.setdefault('slots', {})
    kr_open = (src.get('kr_date') == today) if (src.get('kr_date') and now.strftime('%H:%M') >= '09:05') else (True if now.weekday() < 5 else False)
    H['day']['trading_day'] = kr_open if now.strftime('%H:%M') >= '09:05' else H['day'].get('trading_day')
    items = []
    for key in ORDER:
        if key == 'judge':
            items.append(judge_item(H, now, runs, act)); continue
        name, wf, _, wait, kr_only = JOBS[key]
        S = last_slot(key, now)
        ok, ctext, bad, new = content(key, S, src, now)
        rtext, busy = run_text(runs.get(wf))
        it = {'key': key, 'name': name, 'run': rtext, 'content': ctext, 'slot': hm(S), 'next': hm(next_slot(key, now)), 'why': ''}
        # 출처에 새 자료 — 회차·휴장과 상관없이(같은 출처 날짜에 최대 3번)
        if new:
            new, sdate = new
            rk = '%s@src %s' % (key, sdate)
            rec = slots.setdefault(rk, {'sent': None, 'tries': 0})
            it['why'] = new
            if busy:
                it['state'] = '기다림'
            elif rec['tries'] >= SRC_TRIES:
                it['state'] = '실패'; it['why'] = new + ' — %d번 불러도 그대로' % SRC_TRIES
            elif rec['sent'] and (now - W.parse(rec['sent'])) < dt.timedelta(minutes=wait):
                it['state'] = '기다림'
            elif act:
                sent, err = dispatch(wf, H)
                if sent:
                    rec['sent'], rec['tries'] = now.strftime('%Y-%m-%d %H:%M'), rec['tries'] + 1
                    it['state'] = '기다림'; log('부름 %s — %s (%d번째)' % (name, new, rec['tries']))
                else:
                    it['state'] = '한도' if '한도' in err else '실패'; it['why'] = new + ' — ' + err
            else:
                it['state'] = '늦음'
            items.append(it); continue
        if kr_only and S and S.date() == now.date() and kr_open is False:
            it.update(state='쉼', why='오늘은 국장 휴장'); items.append(it); continue
        if not S:
            it['state'] = '정상' if ok else '기다림'; items.append(it); continue
        rk = '%s@%s' % (key, S.strftime('%Y-%m-%d %H:%M'))
        rec = slots.setdefault(rk, {'sent': None, 'tries': 0, 'state': None})
        if ok:
            rec['state'] = 'ok'; it['state'] = '정상'
        elif rec.get('state') == 'fail':
            it['state'] = '실패'; it['why'] = rec.get('why') or ''
        elif busy:
            it['state'] = '기다림'; it['why'] = '%s 회차 수집이 도는 중' % S.strftime('%H:%M')
        elif rec['sent'] and (now - W.parse(rec['sent'])) < dt.timedelta(minutes=wait):
            it['state'] = '기다림'; it['why'] = '%s 회차를 %s에 불렀음' % (S.strftime('%H:%M'), rec['sent'][11:])
        elif rec['sent'] and rec['tries'] >= TRIES:
            rec['state'] = 'fail'
            rec['why'] = bad or ('%s 회차 — %d번 다시 불러도 내용이 %s 그대로' % (S.strftime('%H:%M'), TRIES, ctext))
            it['state'] = '실패'; it['why'] = rec['why']; log('실패 %s — %s' % (name, rec['why']))
        elif act:
            sent, err = dispatch(wf, H)
            if sent:
                first = rec['sent'] is None
                rec['tries'] += 0 if first else 1
                rec['sent'] = now.strftime('%Y-%m-%d %H:%M')
                it['state'] = '기다림'
                it['why'] = ('%s 회차 부름' if first else '%s 회차 다시 부름(%d/%d)') % ((S.strftime('%H:%M'),) if first else (S.strftime('%H:%M'), rec['tries'], TRIES))
                if bad:
                    it['why'] += ' — ' + bad
                log('부름 %s — %s%s' % (name, it['why'], (' · 문제: ' + bad) if bad else ''))
            else:
                it['state'] = '한도' if '한도' in err else '실패'; it['why'] = err
        else:
            it['state'] = '늦음'; it['why'] = bad or '%s 회차 내용이 아직 없음' % S.strftime('%H:%M')
        items.append(it)
    H['items'] = items
    # 오늘 것만 남긴다(파일이 커지지 않게)
    keep = (now - dt.timedelta(days=1)).strftime('%Y-%m-%d')
    H['slots'] = {k: v for k, v in slots.items() if (re.search(r'\d{4}-\d{2}-\d{2}', k) or [''])[0] >= keep}


def judge_item(H, now, runs, act):
    ok, ctext, _, _ = content('judge', None, {}, now)
    rtext, busy = run_text(runs.get('judge_merge.yml'))
    it = {'key': 'judge', 'name': '매일 판단', 'run': rtext, 'content': ctext, 'slot': '07:30', 'next': '', 'why': ''}
    hmn = now.strftime('%H:%M')
    if ok or hmn < '08:30':
        it['state'] = '정상' if ok else '기다림'
        if not ok: it['why'] = '07:30 루틴 판단을 기다리는 중(08:30까지)'
        return it
    jw = H.setdefault('judge_wake', {})
    wd = J('status/watchdog.json')                                   # 사이트 버튼(refresh_all)이 깨운 기록
    lasts = [W.parse(x) for x in (jw.get('last_fire'), wd.get('last_fire')) if x]
    last = max(lasts) if lasts else None
    url, tok = os.environ.get('ROUTINE_TRIGGER_URL', '').strip(), os.environ.get('ROUTINE_TRIGGER_TOKEN', '').strip()
    it['state'] = '늦음'
    if jw.get('auto_date') == now.strftime('%Y-%m-%d'):
        it['why'] = '오늘 판단이 없음 — 오늘 이미 한 번 깨움(%s), 더 깨우지 않음(사용량)' % (jw.get('last_fire') or '')[11:]
    elif last and now - last < dt.timedelta(minutes=100):
        it['state'] = '기다림'; it['why'] = '%s에 깨운 판단이 도는 중일 수 있음' % last.strftime('%H:%M')
    elif hmn > '14:00':
        it['why'] = '오늘 판단이 없음 — 깨우는 시간(08:30~14:00)이 지남'
    elif not (url and tok):
        it['why'] = '오늘 판단이 없음 — 루틴 트리거 비밀 값이 없어 깨우지 못함'
    elif act:
        inside = W.running_lock(now)
        if inside:
            it['state'] = '기다림'; it['why'] = '루틴이 안에 있음(%s) — 노크만 하고 다음에 다시' % inside
        else:
            text = '판단 — 시계: 오늘(%s) 판단이 %s까지 올라오지 않아 다시 실행합니다. 절차서대로 오늘 판단을 갱신하세요.' % (now.strftime('%m-%d'), hmn)
            try:
                r = urllib.request.urlopen(urllib.request.Request(url, method='POST', data=json.dumps({'text': text}).encode(), headers={
                    'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'anthropic-version': '2023-06-01',
                    'anthropic-beta': 'experimental-cc-routine-2026-04-01'}), timeout=30)
                sess = (json.loads(r.read() or b'{}') or {}).get('claude_code_session_url', '')
                jw.update(last_fire=now.strftime('%Y-%m-%d %H:%M'), auto_date=now.strftime('%Y-%m-%d'), session=sess)
                it['state'] = '기다림'; it['why'] = '%s에 루틴을 깨움(오늘 자동 1번)' % hmn; log('루틴 깨움 %s %s' % (r.status, sess))
            except urllib.error.HTTPError as e:
                it['why'] = '루틴 깨우기 실패 %s' % e.code; log('루틴 깨우기 실패 %s %s' % (e.code, e.read()[:120]))
            except Exception as e:
                it['why'] = '루틴 깨우기 실패 %s' % str(e)[:60]
    else:
        it['why'] = '오늘 판단이 없음'
    return it


# ── 기록(status 브랜치) ──
def load_health():
    try:
        subprocess.run(['git', 'fetch', '-q', 'origin', 'status'], cwd=ROOT, check=True, capture_output=True, timeout=60)
        return json.loads(subprocess.run(['git', 'show', 'FETCH_HEAD:health.json'], cwd=ROOT, check=True,
                                         capture_output=True, timeout=30).stdout or b'{}')
    except Exception:
        return {}


def push_health(H):
    """health.json 하나만 든 커밋을 status 브랜치에 덮어 올린다(기록이 쌓이지 않게)"""
    b = json.dumps(H, ensure_ascii=False, indent=1).encode()
    try:
        blob = subprocess.run(['git', 'hash-object', '-w', '--stdin'], cwd=ROOT, input=b, check=True, capture_output=True).stdout.decode().strip()
        tree = subprocess.run(['git', 'mktree'], cwd=ROOT, input=('100644 blob %s\thealth.json\n' % blob).encode(), check=True, capture_output=True).stdout.decode().strip()
        com = subprocess.run(['git', 'commit-tree', tree, '-m', '시계 %s' % H.get('beat')], cwd=ROOT, check=True, capture_output=True).stdout.decode().strip()
        subprocess.run(['git', 'push', '-q', '-f', 'origin', '%s:refs/heads/status' % com], cwd=ROOT, check=True, capture_output=True, timeout=60)
        return True
    except Exception as e:
        log('기록 올리기 실패 %s' % str(e)[:120]); return False


def pull_main():
    for cmd in (['git', 'fetch', '-q', '--depth', '1', 'origin', 'main'], ['git', 'reset', '-q', '--hard', 'FETCH_HEAD']):   # 시계는 main에 쓰지 않는다
        try:
            subprocess.run(cmd, cwd=ROOT, check=True, capture_output=True, timeout=90)
        except Exception as e:
            log('main 받기 실패 %s' % str(e)[:80]); return False
    return True


def log(s):
    print('%s %s' % (dt.datetime.now(KST).strftime('%H:%M'), s), flush=True)


def other_clocks(my_id):
    out = []
    for stt in ('in_progress', 'queued'):
        st, j = W.gh('GET', '/actions/workflows/clock.yml/runs?status=%s&per_page=20' % stt)
        out += [r['id'] for r in (j or {}).get('workflow_runs', []) if r['id'] != my_id]
    return out


def new_day(H, now):
    d = now.strftime('%Y-%m-%d')
    if (H.get('day') or {}).get('date') != d:
        H['day'] = {'date': d, 'dispatches': 0, 'starts': 0, 'trading_day': None}


def main(argv):
    once = '--once' in argv
    now = dt.datetime.now(KST)
    H = load_health() if not once else (load_health() or {})
    new_day(H, now)
    if once:
        src = sources(); runs, _ = runs_latest()
        tick(H, now, src, runs, act=False)
        for it in H['items']:
            print('%-8s %-4s 실행 %-18s 내용 %-34s %s' % (it['name'], it['state'], it['run'], it['content'], it['why']))
        print('출처:', src)
        if '--out' in argv:                                           # 사이트 미리 보기용
            H['beat'], H['src'] = now.strftime('%Y-%m-%d %H:%M'), src
            json.dump(H, open(argv[argv.index('--out') + 1], 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
        return 0
    my_id = int(os.environ.get('RUN_ID') or 0)
    # 한 번에 하나만 — 먼저 뜬 시계가 있으면 3분 기다려 보고, 그래도 돌고 있으면 이쪽이 끝난다(이어 붙일 때 앞 시계가 끝나는 몇 초를 기다림)
    for _ in range(9):
        older = [i for i in other_clocks(my_id) if i < my_id]
        if not older:
            break
        time.sleep(20)
    else:
        print('이미 시계가 돌고 있음(%s) — 이쪽은 끝냄' % older); return 0
    H = load_health(); new_day(H, now)
    if H['day']['starts'] >= STARTS_CAP:
        print('오늘 시계를 %d번 띄움 — 한도라 더 돌지 않음(다음 날 루틴이 다시 띄운다)' % H['day']['starts']); return 0
    H['day']['starts'] += 1
    H['clock'] = {'run_id': my_id, 'started': now.strftime('%Y-%m-%d %H:%M'), 'event': os.environ.get('RUN_EVENT', '')}
    log('시계 시작 (오늘 %d번째, 부르기 %d/%d)' % (H['day']['starts'], H['day']['dispatches'], DAY_CAP))
    t0, src, src_at, last_push, last_sweep, last_sig = time.time(), {}, 0, 0, 0, None
    while True:
        now = dt.datetime.now(KST)
        new_day(H, now)
        try:
            pull_main()
            if time.time() - src_at > 15 * 60 or not src:
                s2 = sources()
                if s2.get('kr_date') or s2.get('us_date') or s2.get('flows_date'):
                    src, src_at = s2, time.time()
                H['src'] = src
            runs, st = runs_latest()
            if st == 200:
                tick(H, now, src, runs, act=True)
            if time.time() - last_sweep > 3600:
                n = W.sweep_locks(now); last_sweep = time.time()
                if n: log('문패 정리 %d개' % n)
        except Exception as e:
            log('확인 중 오류 %s' % str(e)[:150])
        H['beat'] = now.strftime('%Y-%m-%d %H:%M')
        sig = json.dumps([H.get('items'), H.get('day'), H.get('judge_wake'), H.get('slots')], ensure_ascii=False, sort_keys=True)
        if sig != last_sig or time.time() - last_push > 15 * 60:
            if push_health(H):
                last_push, last_sig = time.time(), sig
        if time.time() - t0 > LIFE:
            push_health(H)
            st, _ = W.gh('POST', '/actions/workflows/clock.yml/dispatches', {'ref': 'main'})
            log('다음 시계로 넘김 %s' % ('요청함' if st == 204 else '실패 %s' % st))
            return 0
        time.sleep(TICK)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
