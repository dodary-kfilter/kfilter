// 브라우저 API 스텁 (verify_prompts.js가 index.html 스크립트 앞에 붙인다)
const DUM=new Proxy({},{get:(t,k)=>{ if(k==='innerHTML'||k==='textContent'||k==='value')return ''; if(k==='style')return {}; if(k==='classList')return {add(){},remove(){},toggle(){}}; if(k==='dataset')return {}; return ()=>DUM; },set:()=>true});
global.navigator={userAgent:'Mozilla/5.0 (iPhone)',clipboard:{writeText:async()=>{}}};
global.document={createElement:()=>DUM,body:DUM,getElementById:()=>DUM,querySelector:()=>DUM,querySelectorAll:()=>[],execCommand:()=>{},addEventListener(){}};
global.window=global; global.location={origin:'',pathname:'',search:'',href:'',replace(){}};
global.fetch=async()=>({ok:false,status:404,json:async()=>({}),text:async()=>''});
global.setTimeout=()=>0; global.addEventListener=()=>{};
