# -*- coding: utf-8 -*-
"""자료 갱신(사이트 버튼) — 서버에서 끝까지 한다 (2026-11-14)

왜: 버튼의 '수집이 끝나면 판단을 부른다'를 브라우저가 하고 있었다. 폰 화면을 끄거나 창을 닫으면 브라우저가 멈춰
    수집만 되고 판단은 불리지 않았다(11/14 04:12 누름 — 수집 7가지는 끝났는데 판단 요청이 없었다).
그래서 이 일을 GitHub 서버(.github/workflows/refresh_all.yml)로 옮겼다. 버튼은 이 워크플로 하나만 부른다.

순서
  1. 수집 7가지(수급·시총순·애널·거시·깔때기 재료·TOP 20 가격·13F)를 실행하고, 모두 끝날 때까지 20초마다 본다(최대 20분)
  2. 판단을 부른다 — 노크: 루틴이 안에 있으면(문패 claude/running-…, 60분 안) 5분마다 다시 노크(최대 60분)
     문이 비었을 때 그동안 끝난 판단이 새 자료 뒤의 것이면 다시 부르지 않는다
  3. 부르면 깨운 시각을 status/watchdog.json에 남긴다(워크플로가 커밋) — 점검 담당이 겹쳐 깨우지 않게
환경: GITHUB_TOKEN(actions·contents 쓰기), ROUTINE_TRIGGER_URL·ROUTINE_TRIGGER_TOKEN(비밀 값), JUDGE(true/false), FIRE_REASON
"""
import json, os, sys, time, datetime as dt, urllib.request, urllib.error

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import watchdog as W                                   # gh·running_lock·parse·KST를 같이 쓴다

FLOWS = ['update.yml', 'mcap.yml', 'analyst.yml', 'macro.yml', 'materials.yml', 'top20.yml', 'f13.yml']
KST = W.KST


def judged_at():
    try:
        req = urllib.request.Request('https://api.github.com/repos/%s/contents/top20/picks.json' % W.REPO,
                                     headers={'Authorization': 'Bearer ' + W.TOKEN, 'Accept': 'application/vnd.github.raw'})
        return W.parse(json.loads(urllib.request.urlopen(req, timeout=30).read()).get('meta', {}).get('judged_at'))
    except Exception:
        return None


def collect():
    t0 = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=30)
    sent = [f for f in FLOWS if W.gh('POST', '/actions/workflows/%s/dispatches' % f, {'ref': 'main'})[0] == 204]
    print('수집 요청 %d/%d: %s' % (len(sent), len(FLOWS), ', '.join(sent)), flush=True)
    deadline = time.time() + 20 * 60
    while time.time() < deadline:
        time.sleep(20)
        st, j = W.gh('GET', '/actions/runs?event=workflow_dispatch&per_page=60&created=%3E' + t0.strftime('%Y-%m-%dT%H:%M:%SZ'))
        latest = {}
        for r in (j or {}).get('workflow_runs', []):
            f = os.path.basename(r.get('path') or '')
            if f in sent and f not in latest:
                latest[f] = r                                     # 최신순이라 처음 것이 이번 실행
        done = {f: r.get('conclusion') for f, r in latest.items() if r.get('status') == 'completed'}
        print('  %s 끝 %d/%d %s' % (dt.datetime.now(KST).strftime('%H:%M:%S'), len(done), len(sent),
                                   ' '.join('%s:%s' % (f.split('.')[0], c) for f, c in done.items())), flush=True)
        if len(done) >= len(sent):
            bad = [f for f, c in done.items() if c != 'success']
            if bad: print('실패한 수집:', bad, flush=True)
            return
    print('20분 안에 다 끝나지 않음 — 받은 만큼으로 판단을 부른다', flush=True)


def judge(t_done):
    url, tok = os.environ.get('ROUTINE_TRIGGER_URL', '').strip(), os.environ.get('ROUTINE_TRIGGER_TOKEN', '').strip()
    if not (url and tok):
        print('루틴 트리거 비밀 값이 없음 — 판단은 부르지 못함'); return
    deadline = time.time() + 60 * 60
    while True:
        inside = W.running_lock(dt.datetime.now(KST))
        if not inside:
            break
        if time.time() > deadline:
            print('60분 동안 루틴이 안에 있음 — 이번엔 부르지 않음(다음 예약·점검에 맡김)'); return
        print('  안에 실행이 있음(%s) — 5분 뒤 다시 노크' % inside, flush=True)
        time.sleep(300)
    j = judged_at()
    if j and j >= t_done:
        print('그사이 끝난 판단(%s)이 새 자료 뒤의 것 — 다시 부르지 않음' % j.strftime('%m-%d %H:%M')); return
    now = dt.datetime.now(KST)
    text = os.environ.get('FIRE_REASON', '').strip() or ('판단 — 사용자가 사이트에서 자료 갱신을 눌렀습니다(%s KST). 방금 새로 받은 자료로 가볍게 다시 판단하세요'
                                                         '(국면·업종·편입편출·추천·비중, 깊이 보기는 시장마다 3개까지, 노트는 쓰지 않음).' % now.strftime('%Y-%m-%d %H:%M'))
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, method='POST', data=json.dumps({'text': text}).encode(), headers={
            'Authorization': 'Bearer ' + tok, 'Content-Type': 'application/json', 'anthropic-version': '2023-06-01',
            'anthropic-beta': 'experimental-cc-routine-2026-04-01'}), timeout=30)
        sess = (json.loads(r.read() or b'{}') or {}).get('claude_code_session_url', '')
        print('판단 다시 부름 %s %s' % (r.status, sess))
        prev = W.rd('status/watchdog.json')
        os.makedirs(os.path.join(ROOT, 'status'), exist_ok=True)
        json.dump({'last_fire': now.strftime('%Y-%m-%d %H:%M'), 'session': sess, 'forced': True, 'by': '자료 갱신 버튼',
                   'auto_date': prev.get('auto_date')}, open(os.path.join(ROOT, 'status', 'watchdog.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    except urllib.error.HTTPError as e:
        print('판단 부르기 실패 %s %s' % (e.code, e.read()[:150]))
    except Exception as e:
        print('판단 부르기 실패 %s' % str(e)[:100])


def main():
    print('자료 갱신 시작 %s KST' % dt.datetime.now(KST).strftime('%Y-%m-%d %H:%M'), flush=True)
    collect()
    t_done = dt.datetime.now(KST)
    if os.environ.get('JUDGE', 'true').lower() == 'true':
        judge(t_done)
    return 0


if __name__ == '__main__':
    sys.exit(main())
