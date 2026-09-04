#!/usr/bin/env node
// 프롬프트 검증 — index.html에서 15경로를 생성해 구조 규칙을 검사한다.
// 사용: node tools/verify_prompts.js [--dump DIR]   (저장소 어디서든)
// 프롬프트를 고칠 때마다 돌린다. ★가 하나라도 있으면 올리지 마라.
const fs=require('fs'), path=require('path'), os=require('os');
const ROOT=path.resolve(__dirname,'..');
const html=fs.readFileSync(path.join(ROOT,'index.html'),'utf8');
const js=(html.match(/<script[^>]*>([\s\S]*?)<\/script>/)||[])[1];
if(!js){ console.error('★ script 추출 실패'); process.exit(1); }
const stub=fs.readFileSync(path.join(__dirname,'_stub.js'),'utf8');
const check=fs.readFileSync(path.join(__dirname,'_check.js'),'utf8');
const tmp=path.join(os.tmpdir(),`kf_verify_${process.pid}.js`);
fs.writeFileSync(tmp, stub+'\n'+js+'\n'+check);
process.env.KF_ROOT=ROOT;
process.env.KF_DUMP=process.argv.includes('--dump')?process.argv[process.argv.indexOf('--dump')+1]:'';
const r=require('child_process').spawnSync(process.execPath,[tmp],{encoding:'utf8',env:process.env});
process.stdout.write(r.stdout||'');
if(r.status!==0 && !(r.stdout||'').includes('★')){
  const err=(r.stderr||'').split('\n').find(l=>/Error/.test(l))||'(원인 미상)';
  console.log('★ 스크립트 로드 단계에서 실패 — 프롬프트 상수 정의 자체가 깨졌다');
  console.log('  '+err.trim());
}
try{fs.unlinkSync(tmp);}catch(_){}
process.exit(r.status||0);
