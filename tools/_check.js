// 검사부 (index.html 스크립트 뒤에 붙는다 — build* 함수와 같은 스코프)
const _fs=require('fs'), _path=require('path');
DATA=JSON.parse(_fs.readFileSync(_path.join(process.env.KF_ROOT,'data.json'),'utf8'));
const _D=DATA, _P=_D.portfolio, _pick=t=>_P.find(x=>x.ptype===t);
const CASES=[
 ['01_동시수급-일괄',()=>buildScreenPrompt()],
 ['02_저가매수-일괄',()=>buildValuePrompt()],
 ['03_모멘텀-일괄',()=>buildMomentumPrompt()],
 ['04_동시수급-개별',()=>buildPrompt(_D.both[0])],
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
// 규칙 — 여기 추가하면 앞으로 전 경로에 걸린다
const RULES=[
 ['미치환',        t=>!/\$\{/.test(t)],
 ['이상값',        t=>!/undefined|NaN|\[object Object\]/.test(t)],
 ['실행제약1',     t=>(t.match(/\[★★실행 제약/g)||[]).length===1],
 ['점검줄1',       t=>(t.match(/\[★★마지막 — 점검 한 줄/g)||[]).length===1],
 ['점검뒤',        t=>{const c=t.indexOf('## 기록용 블록'),p=t.indexOf('[★★마지막 — 점검 한 줄');return c<0||p>c;}],
 ['계측없음',      t=>!t.includes('계측')],
 ['단정없음',      t=>!t.includes('수급 파일에 이미 있다')],
 ['두세줄없음',    t=>!t.includes('— 두세 줄')],
 ['파일↔링크',     t=>(t.includes('report-data/'))===(t.includes('수급 파일은 종목당 56~90KB'))],
 ['path↔직전',     t=>(t.includes('직전 리포트'))===(t.includes('prev_track.path'))],
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
