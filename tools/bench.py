#!/usr/bin/env python3
"""리포트 시험대 — 사이트와 같은 프롬프트 + 번들로 API 리포트를 뽑고 시간·토큰·비용을 잰다.

  python3 tools/bench.py --cases 009150:kr_watch,075580:kr_value,ORCL:us_search --efforts high,max [--dry]
  --cases   코드:kind (kind는 tools/bench_prompt.js 참고)
  --efforts API 노력 레벨 low·medium·high·xhigh·max (쉼표로 여럿 — 같은 번들로 비교)
  --dry     API 없이 프롬프트·번들만 만들고 크기를 잰다
결과는 bench_out/ — 리포트 .md, 입력 .txt, metrics.json, summary.md
번들은 종목당 한 번만 받는다. 리포트 방의 '명령 1회'를 미리 실행해 붙인 것이라 도구 호출은 0회다.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

PRICE = {'claude-opus-5': (5, 25), 'claude-sonnet-5': (2, 10), 'claude-haiku-4-5-20251001': (1, 5)}   # 달러 / 백만 토큰
NOTE = ('\n\n[시험대 안내] 위 [★자료] 명령은 이미 실행했고, 아래가 그 출력(번들) 전부다. '
        '명령을 다시 실행하지 말고 이 번들로 바로 리포트를 쓴다. 점검 줄의 번들은 받음, 도구는 0회로 적는다.\n\n')


def make_prompt(kind, key, name=''):
    r = subprocess.run(['node', 'tools/bench_prompt.js', kind, key] + ([name] if name else []),
                       capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise RuntimeError('프롬프트 생성 실패 %s %s — %s' % (kind, key, r.stderr.strip()[-300:]))
    return r.stdout


def make_bundle(kind, key):
    t0 = time.time()
    r = subprocess.run([sys.executable, 'tools/bundle.py', 'us' if kind.startswith('us') else 'kr', key],
                       capture_output=True, text=True, timeout=600)
    err = next((ln for ln in r.stdout.split('\n') if ln.startswith('#오류')), '#오류 (없음 — 출력 확인)')
    return r.stdout, round(time.time() - t0, 1), r.returncode, err[:300]


def call_api(model, effort, text, max_tokens, tries=4):
    body = json.dumps({'model': model, 'max_tokens': max_tokens, 'stream': True,
                       'thinking': {'type': 'adaptive'}, 'output_config': {'effort': effort},
                       'messages': [{'role': 'user', 'content': text}]}).encode()
    for attempt in range(tries):
        req = urllib.request.Request('https://api.anthropic.com/v1/messages', data=body, headers={
            'x-api-key': os.environ['ANTHROPIC_API_KEY'], 'anthropic-version': '2023-06-01',
            'content-type': 'application/json'})
        t0, first, out, usage, stop, blocks = time.time(), None, [], {}, None, {}
        try:
            with urllib.request.urlopen(req, timeout=3600) as r:
                for raw in r:
                    line = raw.decode('utf-8', 'ignore').strip()
                    if not line.startswith('data:'):
                        continue
                    try:
                        ev = json.loads(line[5:].strip())
                    except ValueError:
                        continue
                    typ = ev.get('type')
                    if typ == 'message_start':
                        usage.update((ev.get('message') or {}).get('usage') or {})
                    elif typ == 'content_block_start':
                        bt = (ev.get('content_block') or {}).get('type')
                        blocks[bt] = blocks.get(bt, 0) + 1
                    elif typ == 'content_block_delta' and (ev.get('delta') or {}).get('type') == 'text_delta':
                        if first is None:
                            first = time.time() - t0
                        out.append(ev['delta'].get('text', ''))
                    elif typ == 'message_delta':
                        usage.update(ev.get('usage') or {})
                        stop = (ev.get('delta') or {}).get('stop_reason') or stop
                    elif typ == 'error':
                        raise RuntimeError(json.dumps(ev.get('error'), ensure_ascii=False)[:300])
        except urllib.error.HTTPError as e:
            msg = e.read()[:400].decode('utf-8', 'ignore')
            if e.code in (429, 500, 529) and attempt < tries - 1:     # 한도·과부하면 기다렸다 다시
                time.sleep(int(e.headers.get('retry-after') or 30) + 5)
                continue
            raise RuntimeError('HTTP %s %s' % (e.code, msg))
        total = time.time() - t0
        return ''.join(out), {
            'seconds': round(total), 'think_seconds': round(first) if first is not None else None,
            'write_seconds': round(total - first) if first is not None else None,
            'input_tokens': usage.get('input_tokens'), 'output_tokens': usage.get('output_tokens'),
            'stop_reason': stop, 'blocks': blocks, 'retries': attempt}
    raise RuntimeError('재시도 초과')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--cases', required=True)
    ap.add_argument('--efforts', default='high')
    ap.add_argument('--model', default='claude-opus-5')
    ap.add_argument('--max-tokens', type=int, default=64000)
    ap.add_argument('--parallel', type=int, default=3)
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--out', default='bench_out')
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cases = [c.split(':') for c in a.cases.split(',') if c.strip()]
    efforts = [e.strip() for e in a.efforts.split(',') if e.strip()]
    metrics, inputs = [], {}
    for c in cases:
        key, kind = c[0].strip(), c[1].strip()
        name = c[2].strip() if len(c) > 2 else ''
        p = make_prompt(kind, key, name)
        b, bsec, brc, berr = make_bundle(kind, key)
        inputs[(key, kind)] = p + NOTE + b
        open(os.path.join(a.out, 'input_%s_%s.txt' % (key.replace('.', '-'), kind)), 'w', encoding='utf-8').write(inputs[(key, kind)])
        metrics.append({'case': '%s:%s' % (key, kind), 'stage': 'input', 'prompt_chars': len(p), 'bundle_chars': len(b),
                        'bundle_seconds': bsec, 'bundle_exit': brc, 'bundle_errors': berr})
        print('입력 %s:%s 프롬프트 %d자 번들 %d자 %.1f초 %s' % (key, kind, len(p), len(b), bsec, berr), flush=True)
    if not a.dry:
        if not os.environ.get('ANTHROPIC_API_KEY'):
            print('★ ANTHROPIC_API_KEY가 없다 — 저장소 Settings → Secrets and variables → Actions에 등록')
            json.dump(metrics, open(os.path.join(a.out, 'metrics.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
            return 2
        jobs = [(key, kind, eff) for key, kind, *_ in cases for eff in efforts]

        def run(job):
            key, kind, eff = job
            try:
                txt, m = call_api(a.model, eff, inputs[(key.strip(), kind.strip())], a.max_tokens)
            except Exception as e:
                return job, None, {'error': str(e)[:400]}
            return job, txt, m
        with ThreadPoolExecutor(max(1, min(a.parallel, len(jobs)))) as ex:
            for (key, kind, eff), txt, m in ex.map(run, jobs):
                row = {'case': '%s:%s' % (key.strip(), kind.strip()), 'stage': 'report', 'effort': eff, 'model': a.model}
                row.update(m)
                if txt is not None:
                    fn = 'report_%s_%s_%s.md' % (key.strip().replace('.', '-'), kind.strip(), eff)
                    open(os.path.join(a.out, fn), 'w', encoding='utf-8').write(txt)
                    pin, pout = PRICE.get(a.model, (0, 0))
                    row.update({'report_chars': len(txt),
                                'cost_usd': round((m['input_tokens'] or 0) * pin / 1e6 + (m['output_tokens'] or 0) * pout / 1e6, 3)})
                metrics.append(row)
                print('리포트 %s %s → %s' % (row['case'], eff, json.dumps(m, ensure_ascii=False)), flush=True)
    json.dump(metrics, open(os.path.join(a.out, 'metrics.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    lines = ['| 경우 | 레벨 | 전체초 | 생각초 | 작성초 | 입력토큰 | 출력토큰 | 비용$ | 종료 | 리포트자수 |', '|' + '---|' * 10]
    for r in metrics:
        if r['stage'] == 'report':
            lines.append('| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |' % (
                r['case'], r['effort'], r.get('seconds'), r.get('think_seconds'), r.get('write_seconds'), r.get('input_tokens'),
                r.get('output_tokens'), r.get('cost_usd'), r.get('stop_reason') or r.get('error', '')[:60], r.get('report_chars')))
    open(os.path.join(a.out, 'summary.md'), 'w', encoding='utf-8').write('\n'.join(lines) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
