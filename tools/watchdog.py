# -*- coding: utf-8 -*-
"""점검 담당 — 자동 갱신이 빠졌는지 자료의 나이로 검사하고, 오래된 수집만 다시 돌린다 (2026-11-02)

사용: python3 tools/watchdog.py          # 오래된 것을 찾아 그 워크플로를 다시 실행
      python3 tools/watchdog.py --dry    # 찾기만 하고 실행하지 않음
GitHub Actions(.github/workflows/watchdog.yml)가 2시간마다 돌린다. GITHUB_TOKEN(actions: write)으로 workflow_dispatch를 보낸다.

나이 기준(한국시간)
  수급(data.json updated)      평일 10:30~16:30엔 90분 · 평일 17:00 이후엔 그날 15:30 이후 갱신 · 주말은 보지 않음
  시총순(lists/mcap.json)      14시간
  애널(lists/analyst.json)     11:00 이후엔 오늘 날짜 · 그 전엔 어제 날짜까지
  거시(cache/macro.json)       16시간
  매일 판단(top20/picks.json)  08:30 이후엔 오늘 판단 — ROUTINE_TRIGGER_URL·TOKEN 비밀 값이 있으면 Claude 루틴 API 트리거로 다시 돌린다
                               (08:30~14:00 사이 점검 때만 — 하루 최대 서너 번, 그 뒤엔 사이트 빨간 표시만)
루틴 API 트리거: POST {URL}  Authorization: Bearer {TOKEN} · anthropic-beta: experimental-cc-routine-2026-04-01 · 본문 {"text": 왜 다시 돌리는지}
같은 워크플로가 이미 돌고 있거나 최근 60분 안에 다시 실행한 적이 있으면 또 보내지 않는다(같은 실패를 되풀이해 쌓지 않게).
"""
import json, os, sys, datetime as dt, urllib.request, urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
KST = dt.timezone(dt.timedelta(hours=9))
REPO = os.environ.get('GITHUB_REPOSITORY', 'dodary-kfilter/kfilter')
TOKEN = os.environ.get('GITHUB_TOKEN', '')


def rd(p):
    try:
        return json.load(open(os.path.join(ROOT, p), encoding='utf-8'))
    except Exception:
        return {}


def parse(s):
    """'2026-11-02 07:30', '2026-11-02 07:30 KST', '2026-11-02' → 한국시간 datetime"""
    s = str(s or '').replace('KST', '').strip()
    for f in ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return dt.datetime.strptime(s[:len(dt.datetime(2000, 1, 1).strftime(f))], f).replace(tzinfo=KST)
        except Exception:
            continue
    return None


def checks(now):
    out = []
    wk = now.weekday() < 5
    d = parse(rd('data.json').get('updated'))
    stale = False
    if d and wk:
        t = now.strftime('%H:%M')
        if '10:30' <= t <= '16:30':
            stale = (now - d) > dt.timedelta(minutes=90)
        elif t >= '17:00':
            stale = d < now.replace(hour=15, minute=30, second=0, microsecond=0)
    out.append(('수급', 'update.yml', d, stale or d is None and wk))
    m = parse((rd('lists/mcap.json').get('meta') or {}).get('updated'))
    out.append(('시총순', 'mcap.yml', m, m is None or (now - m) > dt.timedelta(hours=14)))
    a = parse(rd('lists/analyst.json').get('updated'))
    need = now.date() if now.strftime('%H:%M') >= '11:00' else now.date() - dt.timedelta(days=1)
    out.append(('애널', 'analyst.yml', a, a is None or a.date() < need))
    g = parse(rd('cache/macro.json').get('collected_at'))
    out.append(('거시', 'macro.yml', g, g is None or (now - g) > dt.timedelta(hours=16)))
    j = parse((rd('top20/picks.json').get('meta') or {}).get('judged_at'))
    out.append(('매일 판단', None, j, now.strftime('%H:%M') >= '08:30' and (j is None or j.date() < now.date())))
    return out


def gh(method, path, body=None):
    req = urllib.request.Request('https://api.github.com/repos/%s%s' % (REPO, path), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/vnd.github+json'})
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, (json.loads(r.read() or b'{}') if r.status != 204 else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def recently_run(wf, now):
    st, j = gh('GET', '/actions/workflows/%s/runs?per_page=5' % wf)
    for r in (j or {}).get('workflow_runs', []):
        if r.get('status') in ('queued', 'in_progress', 'waiting', 'requested', 'pending'):
            return '이미 도는 중'
        if r.get('event') == 'workflow_dispatch':
            t = dt.datetime.strptime(r['created_at'], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc)
            if now - t < dt.timedelta(minutes=60):
                return '60분 안에 다시 실행함'
    return ''


def main(argv):
    dry = '--dry' in argv
    now = dt.datetime.now(KST)
    print('점검 %s KST' % now.strftime('%Y-%m-%d %H:%M'))
    for name, wf, t, stale in checks(now):
        when = t.strftime('%m-%d %H:%M') if t else '없음'
        if not stale:
            print('  정상   %-6s 마지막 %s' % (name, when)); continue
        if not wf:
            url, tok = os.environ.get('ROUTINE_TRIGGER_URL', '').strip(), os.environ.get('ROUTINE_TRIGGER_TOKEN', '').strip()
            window = '08:30' <= now.strftime('%H:%M') <= '14:00'
            if url and tok and window and not dry:
                body = json.dumps({'text': '점검: 오늘(%s) 판단이 %s까지 올라오지 않아 다시 실행합니다. 절차서대로 오늘 판단을 갱신하세요.'
                                   % (now.strftime('%m-%d'), now.strftime('%H:%M'))}).encode()
                try:
                    r = urllib.request.urlopen(urllib.request.Request(url, method='POST', data=body, headers={
                        'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json',
                        'anthropic-beta': 'experimental-cc-routine-2026-04-01'}), timeout=30)
                    sess = (json.loads(r.read() or b'{}') or {}).get('claude_code_session_url', '')
                    print('  오래됨 %-6s 마지막 %s → 루틴 다시 실행 %s %s' % (name, when, r.status, sess))
                except urllib.error.HTTPError as e:
                    print('  오래됨 %-6s 마지막 %s → 루틴 다시 실행 실패 %s %s' % (name, when, e.code, e.read()[:120]))
                except Exception as e:
                    print('  오래됨 %-6s 마지막 %s → 루틴 다시 실행 실패 %s' % (name, when, str(e)[:60]))
            elif url and tok and not window:
                print('  오래됨 %-6s 마지막 %s → 다시 실행 시간대(08:30~14:00) 밖 — 사이트 빨간 표시만' % (name, when))
            else:
                print('  오래됨 %-6s 마지막 %s → 사이트에 빨간 표시(대신 돌릴 수 없음)' % (name, when))
            continue
        why = recently_run(wf, now.astimezone(dt.timezone.utc)) if TOKEN else ''
        if why:
            print('  오래됨 %-6s 마지막 %s → 다시 실행 안 함(%s)' % (name, when, why)); continue
        if dry or not TOKEN:
            print('  오래됨 %-6s 마지막 %s → %s 다시 실행할 차례%s' % (name, when, wf, '' if TOKEN else '(토큰 없음)')); continue
        st, _ = gh('POST', '/actions/workflows/%s/dispatches' % wf, {'ref': 'main'})
        print('  오래됨 %-6s 마지막 %s → %s 다시 실행 %s' % (name, when, wf, '요청함' if st == 204 else '실패 %s' % st))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
