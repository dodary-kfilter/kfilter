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
 ['목적',       t=>t.includes('이 리포트를 읽는 사람은 본인의 자금으로 투자해 수익을 내려 하며, 판단을 빠르고 정확하게 받아 보려 합니다.') && t.includes('그 판단에서 나온 기대수익과 액션을 담습니다.') && t.includes('시장의 판단은 비교 대상으로만 삼고, 결론은 본인의 판단으로 정합니다.')],
 ['자료설명',   t=>t.includes('자료에 없는 사실은 지어내지 않습니다. 필요한 정보가 자료에 없으면 웹 검색으로 확인할 수 있습니다.') && t.includes('페이지 원문은 그 가운데 필요한 내용을 자세히 담고 있습니다.')],
 ['자료대상',   t=>{ if(!t.includes('시장 뉴스 제목')) return false; if(t.includes('후보 전체의 개요')) return t.includes('상세 자료에는 본질에 쓸 사업 요약') && t.includes('배경에 쓸 시장·금리·환율·유가') && t.includes('상세 자료는 종목당 4KB 안팎');
                   if(/이 (지수|섹터)에 대한/.test(t)) return /위 명령은 배경에 쓸 (코스피·코스닥 지수의|미국 주요 지수)|위 명령은 본질에 쓸 구성종목/.test(t);
                   return /python3 - us /.test(t) ? t.includes('위 명령은 배경에 쓸 미국 시장') : t.includes('위 명령은 본질에 쓸 사업 요약') && t.includes('부문별 매출·수주'); }],
 ['시간',       t=>{ const s=['이 리포트는 5분 안에 완성되어야 합니다. 본문은 3,000자 안에서 씁니다.'];
                   const b=['이 리포트는 후보 수와 관계없이 10분 안에 완성되어야 합니다. 본문은 6,000자 안에서 씁니다.'];
                   return t.includes('후보 전체의 개요') ? (b.every(x=>t.includes(x)) && !t.includes('5분 안에')) : (s.every(x=>t.includes(x)) && !t.includes('10분 안에')); }],
 ['액션넷',     t=>['지금 매수','가격 대기','매수하지 않음'].every(w=>t.includes(w)) && !t.includes('중립')],
 ['전개순서',   t=>t.includes('후보 전체의 개요')
   ? /\n1\. 본질 — 무엇으로 돈을 버는 회사인가\n2\. 특징 — 지금 값을 가르는 이 회사의 상태\n3\. 기타 — 눈에 띄지만 판단을 바꾸지 않는 것\n4\. 전망 — 앞으로 어떻게 진행될지에 대한 본인의 판단\n5\. 결론 — 종합적 판단, 기대수익, 액션\n/.test(t)
   : /\n1\. 본질 — [^\n]+\n2\. 배경 — 이 (회사가|대상이) 놓인 판\n3\. 특징 — 지금 값을 가르는 이 (회사|대상)의 상태\n4\. 기타 — 눈에 띄지만 판단을 바꾸지 않는 것\n5\. 전망 — 앞으로 어떻게 진행될지에 대한 본인의 판단\n6\. 결론 — 종합적 판단, 기대수익, 액션\n/.test(t)],
 ['배분',       t=>t.includes('기타는 한 줄로 끝납니다. 시간과 분량은 특징과 전망에 씁니다.')
   && t.includes('자료 명령은 한 번에 5~20초, 검색은 결과를 읽는 데까지 한 번에 1분 남짓, 쓰는 데는 1,000자에 20초 남짓 걸립니다.')
   && (t.includes('후보 전체의 개요') === t.includes('맨 앞에 후보 전체에 걸리는 배경을 한 문단으로 씁니다.'))],
 ['선별배경',   t=>/선별되었습니다|동시에 순매수한 종목/.test(t) === t.includes('선별 조건은 후보를 고른 배경일 뿐입니다.')],
 ['기록블록',   t=>/\n---\ndate: \(오늘 날짜 YYYY-MM-DD\)\nprompt: v\d+\nstart: [^\n]+\ncode: [^\n]+\nname: [^\n]+\nprice: \([^\n]+\)\ntarget: \([^\n]+\)\nentry: \([^\n]+\)\ngrade: \((지금 매수\/가격 대기\/매수하지 않음\/매도|지금 매수\/가격 대기\/매도)\)\n---$/.test(t) && !/\nend: /.test(t)],
 ['단위',       t=>t.includes('이 섹터에 대한') ? t.includes('price: (ETN 현재가, 숫자만)') : t.includes('이 지수에 대한') ? t.includes('price: (현재 지수 수준, 숫자만)') : t.includes('price: (현재가, 숫자만)')],
 ['옛문구없음', t=>!/얻을 것이 잃을 것보다|값을 부를|이 종목에 맞는 구성으로|절대PER과 동종|구분이 필요합니다|직접 확인이 필요합니다|재무제표 밖에 있는|자료에 없는 값은|한 차례|이 목록 안에서의 비교|같은 방식으로 판단|배수 하나로|전체를 놓고|따라오는 모습|시장과 같은 견해라면|명령은 한 번만|가장 먼저 아래 명령|\[대상 \d+종목\]|\[스캔 지표|report-data\/|계측|수급 파일에 이미 있다|— 두세 줄|웹 검색으로 확인하십시오|종합적 판단으로 기대수익을 정하고|알기 어렵습니다|이 자리에서 모두 제시|과거, 현재, 그리고 앞으로|그것의 현재 상태|지금 판단을 가르는 요인|과거와 현재|향후 전망 —|잘못 파악하면|본질은 자료의 사업 요약에서/.test(t)],
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
