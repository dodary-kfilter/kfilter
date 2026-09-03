// 프롬프트 전수 감사 — ★화면 버튼이 실제로 부르는 경로 그대로 재현한다.
//
//   왜 필요한가: 2026-08-21에 기록용 블록을 "개별 종목 전 경로"에 붙였다고 판단했으나,
//   테스트를 buildPrompt(..., {ptype:'value'})로 짜서 통과로 나왔다. 실제 버튼은
//   buildValueOne/buildMomentumOne/buildSearchOne/buildSearchUS라는 별도 함수를 타는데
//   그쪽은 안 고쳐져 있었고, 106건 중 85건이 블록 없이 나가고 있었다.
//   → 테스트가 실물과 다르면 통과해도 의미가 없다. 이 파일은 항상 실물 경로만 탄다.
//
// 사용: node tools/audit_prompts.js        (저장소 루트에서)

const fs = require('fs');
const path = require('path');
const ROOT = path.resolve(__dirname, '..');

// ── 브라우저 API 스텁 ──
global.location = { href:'', search:'', hash:'', pathname:'/', reload(){} };
global.localStorage = { getItem:()=>null, setItem(){}, removeItem(){} };
global.sessionStorage = { getItem:()=>null, setItem(){} };
global.history = { replaceState(){}, pushState(){} };
global.requestAnimationFrame = function(){};
const _el = () => ({ textContent:'', value:'', innerHTML:'', className:'', dataset:{},
  classList:{add(){},remove(){},toggle(){},contains:()=>false}, style:{},
  addEventListener(){}, querySelectorAll:()=>[], querySelector:()=>null,
  appendChild(){}, insertAdjacentHTML(){}, remove(){}, focus(){}, select(){} });
global.document = { getElementById:_el, querySelector:()=>null, querySelectorAll:()=>[],
  createElement:_el, addEventListener(){}, body:_el(), documentElement:_el() };
global.window = global;
global.navigator = { clipboard:{ writeText:async()=>{} }, userAgent:'node' };
global.fetch = async () => ({ ok:true, json:async()=>({}), text:async()=>'' });
global.alert = () => {};

// ── index.html에서 최대 script 블록 추출해 평가 ──
const html = fs.readFileSync(path.join(ROOT,'index.html'),'utf8');
const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map(m=>m[1]);
const script = blocks.reduce((a,b)=> b.length>a.length ? b : a, '');
eval(script);

DATA = JSON.parse(fs.readFileSync(path.join(ROOT,'data.json'),'utf8'));

// ── 실물 버튼 경로 재현 ──
//   copyPrompt(ev, code, src) / copyOne(ev, code, isV) / copySearch / copySearchUS
const viaPrompt = (code, src) => {
  const arr = src==='both' ? (DATA.both||[]) : src==='port' ? (DATA.portfolio||[])
            : [...(DATA.portfolio||[]), ...(DATA.both||[])];
  const it = arr.find(x=>x.code===code);
  return it ? buildPrompt(it) : null;
};
const viaOne = (code, isV) => {
  const p = isV ? (DATA.value_pick||[]) : (DATA.momentum||[]);
  const it = p.find(x=>x.code===code);
  return it ? (isV ? buildValueOne(it) : buildMomentumOne(it)) : null;
};

// ── 기록용 블록이 없어야 정상인 것: 지수·섹터·미국지수(매크로라 예측검증 대상 아님) ──
const isMacro = it => it.ptype==='index' || it.ptype==='sector'
                   || (it.ptype==='us' && it.us_kind==='index');

const YAML = ['date:','code:','name:','price:','target:','horizon:','thesis:','grade:'];
const rows = [];
// ★파생(ref) 항목은 기초자산으로 치환해 분석하는 것이 설계다.
//   MVLL(마벨 2배 롱)을 누르면 프롬프트에 MRVL/Marvell이 들어가고 reports/MRVL/에 저장된다.
//   래퍼 코드가 안 나오는 것이 정상이므로 기대값을 ref로 바꿔 검사한다.
//   국내 파생: ref/ref_name  ·  미국 파생: us_ticker/us_target  ·  섹터·지수: basis
//   ★파생이 파생을 가리킬 수 있다(498400 → 122630 → 코스피 지수). 체인을 끝까지 따라간다.
const expect = it => {
  if (it.ref) {
    const base = (DATA.portfolio||[]).find(x=>x.code===it.ref);
    if (base) return expect(base);
    return { code: it.ref, name: it.ref_name || it.name };
  }
  if (it.ptype==='us' && it.us_ticker)
                                  return { code: it.us_ticker, name: it.us_target || it.us_ticker };
  if ((it.ptype==='sector' || it.ptype==='index') && it.basis)
                                  return { code: it.basis, name: it.basis };
  return { code: it.code, name: it.name };
};
const check = (grp, it, text, needBlock) => {
  const exp = expect(it);
  const blk = text ? (text.split('## 기록용 블록')[1] || '') : '';
  const yaml = (blk.match(/\n---\n([\s\S]*?)\n---\n/) || [])[1] || '';
  const cv = ((yaml.match(/code:\s*(.+)/) || [])[1] || '').trim();
  const nv = ((yaml.match(/name:\s*(.+)/) || [])[1] || '').trim();
  const problems = [];
  if (!text) problems.push('프롬프트 null');
  else {
    const has = text.includes('## 기록용 블록');
    if (needBlock && !has) problems.push('기록용 블록 없음');
    if (!needBlock && has) problems.push('블록이 붙으면 안 되는데 있음');
    if (needBlock && has) {
      const n = YAML.filter(k=>yaml.includes(k)).length;
      if (n < 8) problems.push(`yaml ${n}/8`);
      if (cv.includes('${') || nv.includes('${')) problems.push('템플릿 미치환');
      if (cv !== String(exp.code)) problems.push(`code 불일치(${cv} vs ${exp.code})`);
      if (nv !== String(exp.name)) problems.push(`name 불일치(${nv} vs ${exp.name})`);
    }
    // ★매크로(지수·섹터)는 대상 이름으로만 쓰고 티커/코드는 안 쓴다(설계). 이름으로 검사한다.
    const idKey = isMacro(it) ? exp.name : exp.code;
    if (!text.includes(String(idKey))) problems.push(`본문에 ${idKey} 없음`);
  }
  rows.push({ grp, code:it.code, name:it.name, len: text?text.length:0, problems });
};

for (const it of DATA.both || [])       check('동시수급', it, viaPrompt(it.code,'both'), true);
for (const it of DATA.portfolio || [])  check('관심종목', it, viaPrompt(it.code,'port'), !isMacro(it));
for (const it of DATA.value_pick || []) check('저가매수', it, viaOne(it.code,true),  true);
for (const it of DATA.momentum || [])   check('모멘텀',  it, viaOne(it.code,false), true);
check('검색-국내', {code:'051900',name:'LG생활건강'}, buildSearchOne('051900','LG생활건강'), true);
check('검색-미국', {code:'NVDA',name:'NVDA'}, buildSearchUS('NVDA'), true);

// ── 코드 중복 검사: copyPrompt가 엉뚱한 종목을 집을 수 있는 상태인가 ──
const P = new Set((DATA.portfolio||[]).map(x=>x.code));
const B = new Set((DATA.both||[]).map(x=>x.code));
const dup = [...P].filter(c=>B.has(c));

const bad = rows.filter(r=>r.problems.length);
const byGrp = {};
for (const r of rows) {
  byGrp[r.grp] = byGrp[r.grp] || { n:0, bad:0 };
  byGrp[r.grp].n++; if (r.problems.length) byGrp[r.grp].bad++;
}
console.log('■ 프롬프트 전수 감사 (실물 버튼 경로)');
for (const g of Object.keys(byGrp))
  console.log(`  ${g.padEnd(10)} ${String(byGrp[g].n).padStart(3)}건  문제 ${byGrp[g].bad}건`);
console.log(`\n  코드 중복(portfolio∩both): ${dup.length}개 ${dup.length?'★'+dup.join(','):''}`);
console.log(`  ※ 중복이 있어도 카드가 src를 명시하므로 엉뚱한 종목이 복사되지는 않는다.`);
if (bad.length) {
  console.log(`\n■ 문제 ${bad.length}건`);
  for (const r of bad) console.log(`  [${r.grp}] ${r.code} ${String(r.name).slice(0,18)} — ${r.problems.join(' / ')}`);
  process.exit(1);
}
console.log(`\nPASS — ${rows.length}건 전부 통과`);
