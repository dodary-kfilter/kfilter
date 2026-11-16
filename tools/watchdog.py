# -*- coding: utf-8 -*-
"""시계·자료 갱신 버튼이 같이 쓰는 도구 — GitHub API·문패(노크)·시각 읽기 (2026-11-16 정리)

예전 점검 담당(2시간마다 GitHub 예약으로 돌던 검사)은 없앴다. GitHub 예약이 이 저장소에서 제때 안 돌아
점검 자체가 하루 네댓 번만 돌았기 때문이다(11/9~11/15). 점검은 이제 시계(tools/clock.py)가 5분마다 한다.
  - rd(p), parse(s): 자료 파일 읽기, '2026-11-02 07:30(KST)' → 한국시간
  - gh(method, path, body): GitHub API(GITHUB_TOKEN)
  - running_lock(now): 노크 — 루틴이 들어가 있으면(문패 claude/running-YYYYMMDD-HHMM, 60분 안) 그 이름
  - sweep_locks(now): 떼지 못한 문패(2시간 넘은 것) 치우기
루틴 API 트리거: POST {URL}  Authorization: Bearer {TOKEN} · anthropic-version: 2023-06-01 · anthropic-beta: experimental-cc-routine-2026-04-01 · 본문 {"text": 왜 다시 돌리는지}
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


def gh(method, path, body=None):
    req = urllib.request.Request('https://api.github.com/repos/%s%s' % (REPO, path), method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': 'Bearer ' + TOKEN, 'Accept': 'application/vnd.github+json', 'Content-Type': 'application/json'})
    try:
        r = urllib.request.urlopen(req, timeout=20)
        return r.status, (json.loads(r.read() or b'{}') if r.status != 204 else {})
    except urllib.error.HTTPError as e:
        return e.code, {}
    except Exception:
        return 0, {}


def running_lock(now):
    """노크 — 루틴이 들어가 있으면(문패 claude/running-YYYYMMDD-HHMM, 60분 안) 그 이름을 돌려준다. 멈춘 실행의 오래된 문패는 무시"""
    try:
        req = urllib.request.Request('https://api.github.com/repos/%s/branches?per_page=100' % REPO,
                                     headers=dict({'Accept': 'application/vnd.github+json'}, **({'Authorization': 'Bearer ' + TOKEN} if TOKEN else {})))
        names = [b['name'] for b in json.loads(urllib.request.urlopen(req, timeout=20).read())]
    except Exception:
        return None
    for nm in names:
        if not nm.startswith('claude/running-'): continue
        try:
            t = dt.datetime.strptime(nm[len('claude/running-'):][:13], '%Y%m%d-%H%M').replace(tzinfo=KST)
        except Exception:
            continue
        if dt.timedelta(0) <= now - t < dt.timedelta(minutes=60):
            return nm
    return None


def sweep_locks(now, dry=False):
    """떼지 못한 문패(claude/running-…, 2시간 넘은 것)를 치운다 — 루틴이 문패를 못 떼고 끝나는 일이 많다(11/03~11/10에 33개 쌓임)"""
    try:
        req = urllib.request.Request('https://api.github.com/repos/%s/branches?per_page=100' % REPO,
                                     headers=dict({'Accept': 'application/vnd.github+json'}, **({'Authorization': 'Bearer ' + TOKEN} if TOKEN else {})))
        names = [b['name'] for b in json.loads(urllib.request.urlopen(req, timeout=20).read())]
    except Exception:
        return 0
    n = 0
    for nm in names:
        if not nm.startswith('claude/running-'): continue
        try:
            t = dt.datetime.strptime(nm[len('claude/running-'):][:13], '%Y%m%d-%H%M').replace(tzinfo=KST)
        except Exception:
            continue
        if now - t > dt.timedelta(hours=2) and not dry and TOKEN:
            st, _ = gh('DELETE', '/git/refs/heads/' + nm)
            n += 1 if st == 204 else 0
    return n


def main(argv):
    print('점검은 시계(tools/clock.py)가 한다 — 지금 상태를 보려면: python3 tools/clock.py --once')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
