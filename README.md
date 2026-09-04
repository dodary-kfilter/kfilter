# kfilter

## 프롬프트 검증

`index.html`의 프롬프트를 고쳤으면 올리기 전에 반드시 돌린다.

```
node tools/verify_prompts.js            # 15경로 × 규칙 검사. PASS가 아니면 올리지 마라
node tools/verify_prompts.js --dump /tmp/P   # 생성본을 파일로 (내용 검토용)
```

규칙은 `tools/_check.js`의 `RULES`에 있다. 새 결함이 발견되면 거기 한 줄 추가하면 앞으로 전 경로에 걸린다.
