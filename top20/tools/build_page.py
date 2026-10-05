# -*- coding: utf-8 -*-
"""매수 후보 TOP 20 페이지 생성기
사용법: python build_page.py [top20.json 경로] [출력 html 경로]
"""
import json
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'top20.json')
DST = sys.argv[2] if len(sys.argv) > 2 else 'top20.html'
FULL = json.load(open(SRC))
M = FULL['meta']
J = {'kr': FULL['kr'], 'us': FULL['us']}

css = r'''
:root{
  box-sizing:border-box;
  padding-top:env(safe-area-inset-top,0px);
  padding-bottom:env(safe-area-inset-bottom,0px);
  --bg:#F3F5F8; --panel:#FFFFFF; --ink:#18202E; --sub:#5C6678; --line:#DCE1E8;
  --up:#D3312B; --down:#2459C9; --tag:#18202E; --tag-ink:#FFFFFF; --hi:rgba(211,49,43,.05);
  --font:"IBM Plex Sans KR", system-ui, -apple-system, "Apple SD Gothic Neo", "Malgun Gothic", sans-serif;
}
@media (prefers-color-scheme: dark){
  :root:not([data-theme="light"]){
    --bg:#10141C; --panel:#171C26; --ink:#E6E9EF; --sub:#9AA3B2; --line:#2A3140;
    --up:#F0625B; --down:#6F9BFF; --tag:#E6E9EF; --tag-ink:#10141C; --hi:rgba(240,98,91,.08);
  }
}
:root[data-theme="dark"]{
  --bg:#10141C; --panel:#171C26; --ink:#E6E9EF; --sub:#9AA3B2; --line:#2A3140;
  --up:#F0625B; --down:#6F9BFF; --tag:#E6E9EF; --tag-ink:#10141C; --hi:rgba(240,98,91,.08);
}
html{scroll-padding-top:env(safe-area-inset-top,0px)}
*,*::before,*::after{box-sizing:inherit}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);font-size:15px;line-height:1.55;font-variant-numeric:tabular-nums}
main{max-width:1180px;margin:0 auto;padding:24px 16px 48px}
header h1{font-size:28px;line-height:1.2;margin:0 0 6px;font-weight:700;letter-spacing:-.01em}
header p{margin:0;color:var(--sub);max-width:72ch}
.tabs{display:flex;gap:4px;margin:16px 0 0;border-bottom:1px solid var(--line);position:sticky;top:0;background:var(--bg);z-index:5}
.tabs button{font:inherit;font-size:17px;font-weight:500;background:none;border:0;border-bottom:3px solid transparent;color:var(--sub);padding:10px 16px;cursor:pointer;margin-bottom:-1px}
.tabs button[aria-selected="true"]{color:var(--ink);font-weight:700;border-bottom-color:var(--up)}
[role="tabpanel"][hidden]{display:none}
.intro{margin:14px 0 0;color:var(--sub);max-width:80ch}
.formula{margin:14px 0 4px;padding:12px 14px;background:var(--panel);border:1px solid var(--line);border-radius:10px}
.formula .eq{font-size:16px}
.formula .eq .u{color:var(--up)} .formula .eq .d{color:var(--down)}
.formula ul{margin:8px 0 0;padding-left:18px;color:var(--sub);font-size:14px}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:16px 0 12px}
.chip{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:6px 12px;border-radius:999px;cursor:pointer}
.chip[aria-pressed="true"]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.sort{margin-left:auto;display:flex;align-items:center;gap:6px;color:var(--sub);font-size:14px}
.sort select{font:inherit;font-size:14px;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
button:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--down);outline-offset:2px}
.list{display:grid;grid-template-columns:1fr;gap:10px}
@media (min-width:880px){.list{grid-template-columns:1fr 1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:12px 14px;min-width:0}
.card.hi{background:linear-gradient(var(--hi),var(--hi)),var(--panel)}
.top{display:grid;grid-template-columns:auto 1fr auto;gap:2px 10px;align-items:baseline}
.rk{font-size:15px;color:var(--sub);font-weight:500;min-width:1.6em}
.nm{font-weight:700;font-size:17px;overflow-wrap:anywhere}
.evv{font-size:20px;font-weight:700;text-align:right}
.sec{grid-column:2/3;color:var(--sub);font-size:13px}
.barcell{grid-column:3/4;justify-self:end;align-self:center}
.bar{width:96px;height:8px;background:var(--line);border-radius:4px;overflow:hidden;display:flex}
.bar .gain{background:var(--up)} .bar .loss{background:var(--down);margin-left:auto}
.tag{display:inline-block;font-size:11px;font-weight:700;padding:1px 6px;border-radius:4px;background:var(--tag);color:var(--tag-ink);margin-left:6px;vertical-align:2px}
.flag{display:inline-block;font-size:11px;padding:1px 6px;border-radius:4px;border:1px solid var(--line);color:var(--sub);margin-left:4px;vertical-align:2px}
.grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-top:10px}
.cell{background:var(--bg);border-radius:8px;padding:6px 8px;min-width:0}
.cell .k{display:block;font-size:12px;color:var(--sub);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.cell .v{display:block;font-size:15px;font-weight:700;overflow-wrap:anywhere}
.pos{color:var(--up)} .neg{color:var(--down)}
.memo{margin:8px 0 0;font-size:14px;color:var(--sub)}
.card details{margin-top:6px}
.card summary{cursor:pointer;font-size:13px;color:var(--sub)}
.card dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:8px 0 0;font-size:14px}
.card dt{color:var(--sub)} .card dd{margin:0;overflow-wrap:anywhere}
.ref{margin-top:12px;color:var(--sub);font-size:14px}
section.notes{margin-top:24px;max-width:80ch}
section.notes h2{font-size:19px;margin:0 0 8px}
section.notes p{margin:0 0 10px}
.panel>details{margin-top:16px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.panel>details summary{cursor:pointer;font-weight:500}
.panel>details ul{margin:10px 0 0;padding-left:18px;color:var(--sub);font-size:14px}
footer{margin-top:24px;color:var(--sub);font-size:13px}
@media (max-width:420px){
  .grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  header h1{font-size:24px}
  .sort{margin-left:0}
}
'''
css += r'''
details.card>summary{list-style:none;cursor:pointer;display:block}
details.card>summary::-webkit-details-marker{display:none}
.hint{display:flex;justify-content:flex-end;margin-top:6px;font-size:13px;color:var(--sub)}
.hint::after{content:"자세히 보기"}
details.card[open] .hint::after{content:"접기"}
.more{margin-top:12px;padding-top:12px;border-top:1px dashed var(--line);display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px 16px}
.more h3{font-size:14px;margin:0 0 6px}
.more table{width:100%;border-collapse:collapse;font-size:13.5px}
.more td{padding:3px 0;border-bottom:1px solid var(--line);vertical-align:top}
.more td:last-child{text-align:right;padding-left:10px;font-weight:500}
.more tr.sum td{border-bottom:0;font-weight:700}
.full{grid-column:1/-1}
.links a{color:var(--down);font-size:14px}
.pbar{width:96px;height:8px;border-radius:4px;overflow:hidden;display:flex;background:var(--line)}
.pbar .u{background:var(--up)} .pbar .d{background:var(--down)}
.line{margin:8px 0 0;font-size:14px;color:var(--sub)}
.line b{color:var(--ink)}
.own{margin:6px 0 0;font-size:14px}
.own .k{color:var(--sub)}
.cm{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 0;padding:0;list-style:none}
.cm li{font-size:13px;border:1px solid var(--line);border-radius:6px;padding:2px 8px;background:var(--bg)}
.act{display:inline-block;font-size:12px;font-weight:700;padding:2px 8px;border-radius:999px;border:1px solid var(--ink);margin-left:6px;vertical-align:2px}
.act.buy{background:var(--up);border-color:var(--up);color:#fff}
.act.hold{background:var(--ink);color:var(--bg)}
.act.wait{color:var(--sub);border-color:var(--line)}
.act.out{color:var(--down);border-color:var(--down)}
.kn{font-weight:500;font-size:15px;color:var(--sub)}
.act.pass{background:var(--down);border-color:var(--down);color:#fff}
.excl{margin-top:18px;background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.excl>summary{cursor:pointer;font-weight:500}
.xlist{display:grid;grid-template-columns:1fr;gap:10px;margin-top:10px}
@media (min-width:880px){.xlist{grid-template-columns:1fr 1fr}}
.why{margin:0;padding-left:18px;font-size:14px;color:var(--sub)}
.scen{margin:12px 0;padding:12px 14px;background:var(--panel);border:1px solid var(--line);border-radius:10px;font-size:14px;color:var(--sub)}
.scen b{color:var(--ink)}
.expandall{font:inherit;font-size:14px;border:1px solid var(--line);background:var(--panel);color:var(--ink);padding:6px 12px;border-radius:8px;cursor:pointer}
.evv small{display:block;font-size:12px;font-weight:500;color:var(--sub);text-align:right}
'''

def panel(pid, tid, hidden, scen, chips, opts, notes):
    return ('<section id="%s" class="panel" role="tabpanel" aria-labelledby="%s"%s>' % (pid, tid, ' hidden' if hidden else '')
            + '<div class="scen">' + scen + '</div>'
            + '<div class="controls" role="group" aria-label="보기 선택">' + chips
            + '<button class="expandall" type="button">모두 펼치기</button>'
            + '<label class="sort">정렬 <select aria-label="정렬 기준">' + opts + '</select></label></div>'
            + '<div class="list"></div>'
            + '<details class="excl"><summary>이번에 제외한 종목 보기</summary><p class="line">아래 종목은 숫자와 별개로 회계 신뢰, 지나친 가격, 꼭대기 차트, 전략 밖 업종 같은 이유로 매수 후보에서 뺐습니다.</p><div class="xlist"></div></details>'
            + notes + '</section>')

def mk(L): return ''.join('<option value="%s">%s</option>' % (v, l) for v, l in L)
common = [('base', '기본 순서(문턱 통과 → 오를 폭)'), ('pup', '상승 확률 높은 순'), ('E', '기대수익 높은 순'), ('eup', '오르면 평균 큰 순'), ('edn', '내리면 평균 손실 작은 순'),
          ('swing', '3개월 흔들림 작은 순'), ('mcap', '시가총액 큰 순'), ('mcap_a', '시가총액 작은 순')]
kr_opts = mk(common + [('own', '외국인 지분율 높은 순'), ('a60', '외국인 지분율 60일 증가 큰 순'), ('per', 'PER 낮은 순')])
us_opts = mk(common + [('si', '공매도 감소 큰 순'), ('per', '선행 PER 낮은 순'), ('est', '추정치 순상향 많은 순')])
chips_kr = ('<button class="chip" data-filter="all" aria-pressed="true">전체 20</button>'
            '<button class="chip" data-filter="pass" aria-pressed="false">문턱 통과</button>'
            '<button class="chip" data-filter="buy" aria-pressed="false">매수·보유</button>'
            '<button class="chip" data-filter="mine" aria-pressed="false">5형제와 보유</button>'
            '')
chips_us = ('<button class="chip" data-filter="all" aria-pressed="true">전체 20</button>'
            '<button class="chip" data-filter="pass" aria-pressed="false">문턱 통과</button>'
            '<button class="chip" data-filter="buy" aria-pressed="false">매수·보유</button>'
            '<button class="chip" data-filter="mine" aria-pressed="false">보유</button>'
            '')
scen_kr = M['scen_kr']

scen_us = M['scen_us']

notes_kr = M['notes_kr']

notes_us = M['notes_us']


js = r'''
const KN = {"ONTO": "온투 이노베이션", "MRVL": "마벨 테크놀로지", "LRCX": "램리서치", "AMAT": "어플라이드 머티리얼즈", "AMD": "에이엠디", "NVDA": "엔비디아", "TSM": "TSMC", "COHR": "코히런트", "KLIC": "쿨리케앤소파", "KLAC": "케이엘에이", "CRDO": "크레도 테크놀로지", "ALAB": "아스테라랩스", "FLEX": "플렉스", "MCHP": "마이크로칩 테크놀로지", "APH": "앰페놀", "ADI": "아날로그디바이스", "TSEM": "타워 세미컨덕터", "LITE": "루멘텀", "CLS": "셀레스티카", "DELL": "델 테크놀로지스", "SNDK": "샌디스크", "HOOD": "로빈후드", "ARM": "암 홀딩스", "SMCI": "슈퍼마이크로컴퓨터", "GDDY": "고대디", "IT": "가트너", "MKSI": "MKS 인스트루먼츠", "WDAY": "워크데이"};
const fmtPct = v => (v>0?'+':'') + v + '%';
const f1 = v => (v>0?'+':'') + v.toFixed(1) + '%';
const won = v => Math.round(v).toLocaleString('ko-KR') + '원';
const usd = v => '$' + Number(v).toFixed(2);
function tbl(rows){ return '<table>'+rows.map(x=>'<tr'+(x[2]?' class="sum"':'')+'><td>'+x[0]+'</td><td>'+x[1]+'</td></tr>').join('')+'</table>'; }
function cell(k,v,cls){ return '<div class="cell"><span class="k">'+k+'</span><span class="v '+(cls||'')+'">'+v+'</span></div>'; }
function actBadge(a){
  let c='wait', l='대기';
  if(a.startsWith('제외')){c='out';l='제외';}
  else if(a.startsWith('보유')){c='hold';l='보유';}
  else if(a.startsWith('소량')){l='소량';}
  else if(a.startsWith('후보')){l='후보';}
  else if(a.indexOf('매수')>=0){c='buy';l='매수';}
  return '<span class="act '+c+'">'+l+'</span>';
}
function card(r, i, market){
  const money = market==='kr' ? won : usd;
  const tag = (r.tag && !(r.tag==='보유' && r.act.startsWith('보유')))?'<span class="tag">'+r.tag+'</span>':'';
  const idxName = {SOXX:'반도체 지수',KOSDAQ:'코스닥',KOSPI:'코스피',SPY:'S&P500'}[r.idx];
  const sub = market==='kr' ? r.sector+', 시총 '+Math.round(r.mcap).toLocaleString('ko-KR')+'억 원' : r.desc+(r.mcap?', 시총 '+Math.round(r.mcap).toLocaleString('ko-KR')+'억 달러':'');
  const pbar = '<div class="pbar" aria-hidden="true"><span class="u" style="width:'+r.Pup+'%"></span><span class="d" style="width:'+r.Pdn+'%"></span></div>';
  const pass = (r.pass && !r.act.startsWith('제외')) ? '<span class="act pass">문턱 통과</span>' : '';
  const title = market==='us' ? r.name+' <span class="kn">'+(KN[r.name]||'')+'</span>' : r.name;
  const top = '<div class="top"><span class="rk">'+i+'</span><span class="nm">'+title+tag+actBadge(r.act)+pass+'</span><span class="evv pos">상승 '+r.Pup+'%</span>'+
              '<span></span><span class="sec">'+sub+'</span><span class="barcell">'+pbar+'</span></div>';
  const grid = '<div class="grid">'+cell('상승 확률',r.Pup+'%','pos')+cell('하락 확률',r.Pdn+'%','neg')+cell('오르면 평균',f1(r.Eup),'pos')+cell('내리면 평균',f1(r.Edn),'neg')+'</div>';
  const line = '<p class="line">기대수익 <b>'+f1(r.E)+'</b>, 3개월 흔들림 <b>±'+r.swing+'%</b>('+(r.volsrc==='옵션 내재 변동성'?'옵션 시장 예상':'지난 120일')+'), 베타 <b>'+r.beta.toFixed(2)+'</b> ('+idxName+' 기준)</p>';
  const lv = r.mw;
  const w30txt = lv.lv_w30==null ? '30주선 자료 없음' : (lv.lv_w30<=0 ? '30주선 <b>'+lv.lv_w30.toFixed(1)+'%</b> 아래(지지)' : '30주선 <b>+'+lv.lv_w30.toFixed(1)+'%</b> 위(저항)');
  const levels = '<p class="line">전고점까지 <b>'+(lv.lv_hi>0?'+':'')+lv.lv_hi.toFixed(1)+'%</b>, '+w30txt+', 최근 13주 저점 <b>'+(lv.lv_low==null?'-':lv.lv_low.toFixed(1)+'%')+'</b></p>';
  const own = (market==='kr') ? (r.own==null ? '<p class="own"><span class="k">외국인 지분율</span> 자료 없음</p>' :
      '<p class="own"><span class="k">외국인 지분율</span> '+r.own60.toFixed(2)+'% → '+r.own20.toFixed(2)+'% → <b>'+r.own.toFixed(2)+'%</b> <span class="k">(60일 전 → 20일 전 → 지금, '+(r.a60>0?'+':'')+r.a60.toFixed(2)+'%p)</span></p>') : '';
  const cm = '<ul class="cm">'+r.cm.map(c=>'<li>'+c+'</li>').join('')+'</ul>';
  const sc = r.sc.map(s=>[s.lab+' '+s.p+'%, 지수 '+(s.m>0?'+':'')+s.m+'%', '예상 '+f1(s.r)+', 상승 확률 '+s.pu+'%']);
  const calc = tbl(sc.concat([['베타',r.beta.toFixed(2)],['고유 기대 (내 판단)',fmtPct(r.alpha)],['흔들림 출처', r.volsrc+(r.iv?' (연 '+r.iv+'%)':'')],['고유 흔들림 (3개월)','±'+r.idio+'%'],
               ['상승 확률 / 하락 확률', r.Pup+'% / '+r.Pdn+'%', true],['오르면 평균 / 내리면 평균', f1(r.Eup)+' / '+f1(r.Edn), true],['기대수익', f1(r.E), true]]));
  const mw = r.mw;
  const chart = tbl([
    ['20개월선 위 연속', mw.run20==null?'자료 부족':mw.run20+'개월째'],
    ['20개월선 대비', mw.ext20==null?'-':fmtPct(mw.ext20)],
    ['3년 수익률', mw.r36==null?'-':fmtPct(mw.r36)],
    ['월 평균 흔들림', mw.mrng==null?'-':mw.mrng+'%'],
    ['최근 1년 최대 낙폭', mw.mdd+'%'],
    ['30주선', mw.w30==null?'-':money(mw.w30)+' (현재가가 '+(mw.w30pos>=0? mw.w30pos+'% 위':Math.abs(mw.w30pos)+'% 아래')+')'],
    ['10주선 방향', mw.w10up==null?'-':(mw.w10up?'상승':'하락')],
    ['최근 8주 저점이 높아진 주', mw.hl==null?'-':mw.hl+'/8']
  ]);
  const exit = tbl([['10일선 (단기 도망선)', r.ma10?money(r.ma10):'-'],['하루 급락 기준 (평소의 2배)','−'+r.drop2+'%'],['30주선 (추세 이탈선, 주봉 종가)', mw.w30==null?'-':money(mw.w30)],
                    ['최근 1년 종가 고점', money(r.peak)+' ('+r.peak_date+')']]);
  let fund;
  if(market==='kr'){
    fund = tbl([['반기 영업이익', r.hg],['분기 영업이익(억 원)', (r.op||[]).map(v=>Math.round(v).toLocaleString('ko-KR')).join(' → ')],['PER', r.per==null?'없음':r.per.toFixed(1)+'배'],
                ['외국인 지분율 60일 전 / 20일 전 / 지금', r.own==null?'자료 없음':r.own60.toFixed(2)+'% / '+r.own20.toFixed(2)+'% / '+r.own.toFixed(2)+'%'],
                ['기관 20일 순매매', (r.ins>0?'+':'')+r.ins.toLocaleString('ko-KR')+'만 주']]);
  } else {
    fund = tbl([['반기 영업이익', r.hg],['내년 EPS 성장', r.fg],['선행 PER', r.per==null?'-':r.per.toFixed(1)+'배'],['추정치 상향 / 하향', r.up+'건 / '+r.dn+'건'],
                ['공매도 1개월 변화', r.si==null?'-':fmtPct(r.si)],['기관 보유 (분기 공시)', r.inst==null?'-':((r.inst>0?'순증 +':'순감 ')+(r.inst/1e6).toFixed(1)+'백만 주')],
                ['분기 영업이익(백만 달러)', (r.op||[]).length?r.op.map(v=>v.toLocaleString('ko-KR')).join(' → '):'자료 없음']]);
  }
  const why = '<ul class="why">'+r.why.map(w=>'<li>'+w+'</li>').join('')+'</ul>';
  return '<details class="card'+(r.tag?' hi':'')+'"><summary>'+top+grid+line+levels+own+cm+'<p class="memo">'+r.act+'</p><span class="hint"></span></summary>'+
    '<div class="more"><div><h3>3개월 예측 계산</h3>'+calc+'</div><div><h3>판단 근거</h3>'+why+'<h3 style="margin-top:12px">실적·수급</h3>'+fund+'</div>'+
    '<div><h3>월봉·주봉</h3>'+chart+'</div><div><h3>도망선</h3>'+exit+'</div>'+
    '<div class="full links"><a href="'+r.link+'" target="_blank" rel="noopener">'+(market==='kr'?'다음 금융':'야후 파이낸스')+'에서 '+r.name+(market==='us'&&KN[r.name]?' ('+KN[r.name]+')':'')+' 열기</a></div></div></details>';
}
const KEY = {base:['rank',1],pup:['Pup',-1],E:['E',-1],eup:['Eup',-1],edn:['Edn',-1],swing:['swing',1],mcap:['mcap',-1],mcap_a:['mcap',1],own:['own',-1],a60:['a60',-1],per:['per',1],si:['si',1],est:['est',-1]};
function setup(id, pack, market){
  const data=pack.top;
  const p=document.getElementById(id), list=p.querySelector('.list'), sel=p.querySelector('select'), exp=p.querySelector('.expandall');
  data.concat(pack.out).forEach(r=>{ r.est=(r.up||0)-(r.dn||0); });
  const st={key:'base',filter:'all',open:false};
  function render(){
    let a=data.slice();
    if(st.filter==='buy') a=a.filter(r=>r.act.indexOf('매수')>=0||r.act.startsWith('보유'));
    if(st.filter==='mine') a=a.filter(r=>r.tag);
    if(st.filter==='pass') a=a.filter(r=>r.pass);
    const [k,dir]=KEY[st.key];
    a.sort((x,y)=>{const vx=x[k],vy=y[k]; if(vx==null&&vy==null) return 0; if(vx==null) return 1; if(vy==null) return -1; return ((vx-vy)*dir) || (y.E-x.E);});
    list.innerHTML=a.map((r,i)=>card(r,(st.key==='base'&&st.filter==='all')?r.rank:(i+1),market)).join('')||'<p class="memo">이 조건에 맞는 종목이 없습니다.</p>';
    if(st.open) list.querySelectorAll('details').forEach(d=>d.open=true);
  }
  p.querySelectorAll('.chip').forEach(c=>c.addEventListener('click',()=>{st.filter=c.dataset.filter;p.querySelectorAll('.chip').forEach(x=>x.setAttribute('aria-pressed',x===c?'true':'false'));render();}));
  sel.addEventListener('change',()=>{st.key=sel.value;render();});
  exp.addEventListener('click',()=>{st.open=!st.open;exp.textContent=st.open?'모두 접기':'모두 펼치기';list.querySelectorAll('details').forEach(d=>d.open=st.open);});
  render();
  p.querySelector('.xlist').innerHTML = pack.out.map((r,i)=>card(r,'제외',market)).join('');
}
setup('p-kr', JD.kr, 'kr');
setup('p-us', JD.us, 'us');
const tabs=[...document.querySelectorAll('[role="tab"]')];
function show(t){
  tabs.forEach(x=>{const on=x===t;x.setAttribute('aria-selected',on?'true':'false');x.tabIndex=on?0:-1;document.getElementById(x.getAttribute('aria-controls')).hidden=!on;});
  history.replaceState(null,'',t.id==='t-us'?'#us':'#kr');
}
tabs.forEach((t,i)=>{t.addEventListener('click',()=>show(t));t.addEventListener('keydown',e=>{if(e.key==='ArrowRight'||e.key==='ArrowLeft'){const n=tabs[(i+(e.key==='ArrowRight'?1:tabs.length-1))%tabs.length];show(n);n.focus();}});});
if(location.hash==='#us') show(document.getElementById('t-us'));
'''

html = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
        '<title>매수 후보 TOP 20</title>'
        '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
        '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+KR:wght@400;500;700&display=swap" rel="stylesheet">'
        '<style>' + css + '</style></head><body><main>'
        '<header><h1>매수 후보 TOP 20</h1><p>앞으로 3개월(한 분기) 판단이고, 10월 2일 종가 기준입니다. 상승 확률로 먼저 거르고, 통과한 종목 안에서 오를 폭을 봅니다. 카드를 누르면 계산과 근거, 월봉·주봉, 도망선, 실적·수급이 펼쳐집니다.</p></header>'
        '<nav class="tabs" role="tablist" aria-label="시장 선택">'
        '<button role="tab" id="t-kr" aria-controls="p-kr" aria-selected="true">국장</button>'
        '<button role="tab" id="t-us" aria-controls="p-us" aria-selected="false" tabindex="-1">미장</button></nav>'
        + panel('p-kr', 't-kr', False, scen_kr, chips_kr, kr_opts, notes_kr)
        + panel('p-us', 't-us', True, scen_us, chips_us, us_opts, notes_us)
        + '<footer>자료: 국장은 kfilter 스캔과 다음 금융 일봉·투자자 자료, 미장은 나스닥(재무·추정치·공매도·기관 보유)과 야후 파이낸스(가격)입니다. 베타와 흔들림은 최근 120거래일, 월봉·주봉은 야후 파이낸스 기준입니다. 모두 10월 2일 장 마감 기준입니다.</footer>'
        '</main><script>const JD=' + json.dumps(J, ensure_ascii=False) + ';' + js + '</script></body></html>')
open(DST, 'w').write(html)
print('written', len(html))
