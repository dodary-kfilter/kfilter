// 검사부 (index.html 스크립트 뒤에 붙는다 — build* 함수와 같은 스코프)
const _fs=require('fs'), _path=require('path');
DATA=JSON.parse(_fs.readFileSync(_path.join(process.env.KF_ROOT,'data.json'),'utf8'));
const _D=DATA, _P=_D.portfolio, _pick=t=>_P.find(x=>x.ptype===t);
const CASES=[
 ['01_동시수급-일괄',()=>buildScreenPrompt()],
 ['02_저가매수-일괄',()=>buildValuePrompt()],
 ['03_모멘텀-일괄',()=>buildMomentumPrompt()],
 ['04_동시수급-개별',()=>buildPrompt(_D.both[0]||_D.value_pick[0])],   // 그날 동시수급이 0개면 같은 템플릿을 저가매수 첫 종목으로 검사
 ['05_저가매수-개별',()=>buildValueOne(_D.value_pick[0])],
 ['06_모멘텀-개별',()=>buildMomentumOne(_D.momentum[0])],
 ['07_관심-국내',()=>buildPrompt(_P.find(x=>x.ptype==='stock'&&!x.ref))],
 ['08_관심-파생',()=>buildPrompt(_P.find(x=>x.ref&&x.ptype==='stock'))],
 ['09_관심-미국',()=>buildPrompt(_P.find(x=>x.ptype==='us'&&x.us_kind!=='index'))],
 ['10_관심-지수',()=>buildPrompt(_P.find(x=>x.ptype==='index'&&!x.ref))],
 ['11_관심-지수파생',()=>buildPrompt(_P.find(x=>x.ptype==='index'&&x.ref))],
 ['12_관심-섹터',()=>buildPrompt(_pick('sector'))],
 ['13_관심-미국지수',()=>buildPrompt(_P.find(x=>x.ptype==='us'&&x.us_kind==='index'))],
 ['14_검색-국내',()=>buildSearchOne('005930','삼성전자')],
 ['15_검색-미국',()=>buildSearchUS('NVDA')],
];
// 규칙 — 여기 추가하면 앞으로 전 경로에 걸린다 (v31 통일 틀 기준)
const RULES=[
 ['미치환',     t=>!/\$\{/.test(t)],
 ['이상값',     t=>!/undefined|NaN|\[object Object\]/.test(t)],
 ['자료명령',   t=>{ const n=(t.match(/bundle\.py \| python3 - (kr|us) /g)||[]).length;
                   return t.includes('후보 전체의 개요') ? (n===2 && /--overview\n/.test(t) && t.includes('python3 - kr (종목코드) --brief')) : n===1; }],
 ['목적',       t=>t.includes('이 리포트를 읽는 사람은 본인의 자금으로 투자해 수익을 내려 합니다.') && t.includes('그 판단에서 나온 기대수익과 액션을 담습니다.') && t.includes('시장의 판단은 비교 대상으로만 삼고, 결론은 본인의 판단으로 정합니다.')],
 ['자료설명',   t=>/(주가가|지수가|가격이) 움직인 원인과 앞으로의 전망은 이 자료만으로는 알기 어렵습니다\./.test(t) && t.includes('페이지 원문은 그 가운데 필요한 내용을 자세히 담고 있습니다.') && t.includes('자료에 없는 사실은 지어내지 않습니다.')],
 ['자료대상',   t=>{ if(t.includes('후보 전체의 개요')) return t.includes('상세 자료에는 사업 요약');
                   if(t.includes('이 대상의 성격과 구성')) return /위 명령은 (코스피·코스닥 지수의|\S+ 지수를 따르는|이 섹터를 따르는 ETN)/.test(t);
                   return /python3 - us /.test(t) ? t.includes('위 명령은 종목의 시세와 가격 위치, 분기 재무') : t.includes('위 명령은 종목의 사업 요약'); }],
 ['액션넷',     t=>['지금 매수','가격 대기','매수하지 않음'].every(w=>t.includes(w)) && !t.includes('중립')],
 ['전개순서',   t=>/\n3\. 그것의 현재 상태\n4\. 향후 전망 — (종목|대상)이 앞으로 어떻게 진행될지에 대한 본인의 판단\n5\. 결론 — 종합적 판단, 기대수익, 액션\n/.test(t)],
 ['선별배경',   t=>/선별되었습니다|동시에 순매수한 종목/.test(t) === t.includes('선별 조건은 후보를 고른 배경일 뿐입니다.')],
 ['기록블록',   t=>/\n---\ndate: \(오늘 날짜 YYYY-MM-DD\)\nprompt: v\d+\nstart: [^\n]+\ncode: [^\n]+\nname: [^\n]+\nprice: \([^\n]+\)\ntarget: \([^\n]+\)\nentry: \([^\n]+\)\ngrade: \((지금 매수\/가격 대기\/매수하지 않음\/매도|지금 매수\/가격 대기\/매도)\)\n---$/.test(t) && !/\nend: /.test(t)],
 ['단위',       t=>t.includes('이 섹터에 대한') ? t.includes('price: (ETN 현재가, 숫자만)') : t.includes('이 지수에 대한') ? t.includes('price: (현재 지수 수준, 숫자만)') : t.includes('price: (현재가, 숫자만)')],
 ['옛문구없음', t=>!/얻을 것이 잃을 것보다|값을 부를|이 종목에 맞는 구성으로|절대PER과 동종|구분이 필요합니다|직접 확인이 필요합니다|재무제표 밖에 있는|자료에 없는 값은|한 차례|이 목록 안에서의 비교|같은 방식으로 판단|배수 하나로|전체를 놓고|따라오는 모습|시장과 같은 견해라면|명령은 한 번만|가장 먼저 아래 명령|\[대상 \d+종목\]|\[스캔 지표|report-data\/|계측|수급 파일에 이미 있다|— 두세 줄|웹 검색으로 확인하십시오|종합적 판단으로 기대수익을 정하고/.test(t)],
];
const dump=process.env.KF_DUMP; if(dump) _fs.mkdirSync(dump,{recursive:true});
let bad=0;
console.log('경로'.padEnd(18)+'chars'.padStart(7)+'  '+RULES.map((_,i)=>String(i+1).padStart(2)).join(' '));
for(const [n,f] of CASES){
  let t; try{ t=f(); }catch(e){ console.log(n.padEnd(18)+' ★ERR '+e.message.slice(0,50)); bad++; continue; }
  if(t==null){ console.log(n.padEnd(18)+' ★NULL'); bad++; continue; }
  if(dump) _fs.writeFileSync(_path.join(dump,n+'.txt'),t);
  const r=RULES.map(([,fn])=>fn(t)); if(r.includes(false)) bad++;
  console.log(n.padEnd(18)+String(t.length).padStart(7)+'  '+r.map(x=>x?' O':' ★').join(' '));
}
console.log('규칙: '+RULES.map((r,i)=>`${i+1}=${r[0]}`).join(' · '));
console.log(bad?`★ ${bad}경로 문제`:'PASS');
process.exit(bad?1:0);
