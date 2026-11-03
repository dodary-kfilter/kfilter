# 기업 노트

매일 판단(tools/daily_judge.md 4단계)이 쓰는 기업별 분석 노트. 한 번 깊이 본 기업은 여기에 남겨, 다음 날엔 바뀐 것만 다시 본다.

- `notes/kr/종목코드.md` — 국장 (예: notes/kr/005930.md)
- `notes/us/티커.md` — 미장 (예: notes/us/NVDA.md)
- 틀: 무엇으로 돈 버나 · 경쟁력 · 숫자(기준일·출처) · 다음 계기 · 무너뜨릴 위험 · 개성 · 판단 기록
- 첫 줄 아래 `업데이트: YYYY-MM-DD (이유)` — 수집기(daily_collect.py)가 후보 표의 '노트' 칸에 이 날짜를 보여 준다
- 루틴은 claude/judge-* 브랜치로 올리고, judge_merge.yml이 notes/kr|us/*.md를 main으로 옮긴다
