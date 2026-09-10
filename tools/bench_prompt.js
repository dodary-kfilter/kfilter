#!/usr/bin/env node
// 시험대 — index.html의 실제 템플릿으로 한 경우의 프롬프트를 뽑는다(사이트에서 복사하는 것과 같은 글).
// 사용: node tools/bench_prompt.js <kind> <코드|티커> [종목명]
//   kind: kr_watch(보유·관심) kr_both(동시수급 개별) kr_value(저가매수 개별) kr_momentum(모멘텀 개별)
//         kr_search(검색-국내, 종목명 필요) us_search(검색-미국) us_watch(관심-미국)
const fs=require('fs'), path=require('path'), os=require('os');
const ROOT=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(ROOT,'index.html'),'utf8');
const js=(html.match(/<script[^>]*>([\s\S]*?)<\/script>/)||[])[1];
const stub=fs.readFileSync(path.join(__dirname,'_stub.js'),'utf8');
const [kind,key,name]=process.argv.slice(2);
if(!kind||!key){ console.error('사용: node tools/bench_prompt.js <kind> <코드|티커> [종목명]'); process.exit(1); }
const tail=`
const _fs=require('fs'), _path=require('path');
DATA=JSON.parse(_fs.readFileSync(_path.join(${JSON.stringify(ROOT)},'data.json'),'utf8'));
const K=${JSON.stringify(kind)}, KEY=${JSON.stringify(key)}, NM=${JSON.stringify(name||'')};
const pick=a=>(a||[]).find(x=>String(x.code)===KEY);
let t=null;
if(K==='kr_watch'||K==='us_watch') t=buildPrompt(pick(DATA.portfolio));
else if(K==='kr_both') t=buildPrompt(pick(DATA.both));
else if(K==='kr_value') t=buildValueOne(pick(DATA.value_pick));
else if(K==='kr_momentum') t=buildMomentumOne(pick(DATA.momentum));
else if(K==='kr_search') t=buildSearchOne(KEY,NM);
else if(K==='us_search') t=buildSearchUS(KEY);
if(!t){ console.error('프롬프트 생성 실패 — 목록에 대상이 없다: '+K+' '+KEY); process.exit(2); }
process.stdout.write(t);
`;
const tmp=path.join(os.tmpdir(),`kf_bench_${process.pid}.js`);
fs.writeFileSync(tmp, stub+'\n'+js+'\n'+tail);
const r=require('child_process').spawnSync(process.execPath,[tmp],{encoding:'utf8',maxBuffer:64*1024*1024});
try{fs.unlinkSync(tmp);}catch(_){}
if(r.status!==0){ process.stderr.write((r.stderr||'').split('\n').filter(l=>/Error|실패/.test(l)).slice(0,3).join('\n')+'\n'); process.exit(r.status||1); }
process.stdout.write(r.stdout);
