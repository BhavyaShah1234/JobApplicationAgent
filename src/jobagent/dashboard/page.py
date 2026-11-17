"""The one-page front end: plain HTML and JavaScript, no external files. Everything from the agent's data is put on the page with
`textContent`, never as HTML, so a scraped job title cannot inject markup."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job agent</title>
<style>
:root{--bg:#f6f6f3;--fg:#1c1c1a;--mut:#6d6d67;--card:#fff;--line:#e3e3dd;--soft:#efefe9;--acc:#2158c4;--accsoft:#e6eefc;--ok:#1a7a3e;--oksoft:#e2f4e8;--warn:#a65f00;--warnsoft:#fdf0d9;--bad:#b3261e;--badsoft:#fbe5e3;--shadow:0 1px 2px rgba(0,0,0,.05)}
@media (prefers-color-scheme:dark){:root{--bg:#141413;--fg:#ecece7;--mut:#9b9b93;--card:#1e1e1c;--line:#33332f;--soft:#2a2a27;--acc:#7da9f0;--accsoft:#1f2c47;--ok:#63d089;--oksoft:#16301f;--warn:#e3a44c;--warnsoft:#3a2a10;--bad:#f1867f;--badsoft:#3b1b19;--shadow:none}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
header{position:sticky;top:0;z-index:5;display:flex;flex-wrap:wrap;gap:10px 14px;align-items:center;padding:10px 20px;border-bottom:1px solid var(--line);background:var(--card)}
h1{font-size:16px;margin:0;letter-spacing:.2px}nav{display:flex;flex-wrap:wrap;gap:4px}
nav button{border:0;background:transparent;color:var(--mut);padding:7px 12px;border-radius:8px;cursor:pointer;font:inherit;font-weight:500}
nav button:hover{background:var(--soft);color:var(--fg)}nav button[aria-selected=true]{background:var(--accsoft);color:var(--acc);font-weight:600}
.badge{display:inline-block;min-width:19px;text-align:center;background:var(--bad);color:#fff;border-radius:10px;padding:0 6px;font-size:12px;margin-left:6px;font-weight:600}
.badge.info{background:var(--acc)}.live{margin-left:auto;color:var(--mut);font-size:13px;display:flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--ok);display:inline-block}.dot.on{animation:pulse 1.4s infinite}@keyframes pulse{50%{opacity:.25}}
main{padding:20px;max-width:1240px;margin:0 auto}h2{font-size:13px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut);margin:26px 0 10px;font-weight:700}h2:first-child{margin-top:0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card,.box,.job{background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}
.card{padding:12px 14px}.card b{font-size:26px;display:block;line-height:1.1}.card span{color:var(--mut);font-size:13px}.card.hot{border-color:var(--bad)}.card.hot b{color:var(--bad)}
.box{padding:14px;margin-bottom:12px}.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
th,td{text-align:left;padding:9px 12px;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}tr:last-child td{border-bottom:0}th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.4px;background:var(--soft)}
tbody tr:hover td{background:var(--soft)}code{background:var(--soft);padding:2px 6px;border-radius:5px;word-break:break-all;font-size:13px}
.chip{display:inline-block;padding:1px 9px;border-radius:20px;font-size:12px;font-weight:600;background:var(--soft);color:var(--mut);white-space:nowrap}
.chip.ok{background:var(--oksoft);color:var(--ok)}.chip.warn{background:var(--warnsoft);color:var(--warn)}.chip.bad{background:var(--badsoft);color:var(--bad)}.chip.run{background:var(--accsoft);color:var(--acc)}
input,select,textarea{font:inherit;color:var(--fg);background:var(--card);border:1px solid var(--line);border-radius:8px;padding:7px 10px}textarea{width:100%}input:focus,textarea:focus,select:focus{outline:2px solid var(--acc);outline-offset:-1px}
.act{background:var(--acc);color:#fff;border:0;border-radius:8px;padding:7px 15px;cursor:pointer;font:inherit;font-weight:600}.act.sec{background:var(--soft);color:var(--fg)}.act.sm{padding:3px 10px;font-size:13px}
.tools{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px;align-items:center}.tools input{min-width:240px}
.job{padding:14px 16px;margin-bottom:12px}.jh{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:baseline}.jt{font-weight:650;font-size:16px}.jc{color:var(--mut)}.jr{margin-left:auto;color:var(--mut);font-size:13px;display:flex;gap:10px;align-items:center}
.bar{height:8px;background:var(--soft);border-radius:8px;overflow:hidden;margin:12px 0 10px}.bar>i{display:block;height:100%;background:var(--acc);border-radius:8px;transition:width .5s}.bar.done>i{background:var(--ok)}.bar.failed>i,.bar.blocked>i,.bar.stalled>i{background:var(--bad)}
.steps{display:flex;flex-wrap:wrap;gap:8px}.step{flex:1 1 150px;min-width:140px;border:1px solid var(--line);border-radius:10px;padding:8px 10px;background:var(--bg)}
.step .n{font-size:13px;font-weight:600;display:flex;gap:7px;align-items:center}.step .s{font-size:12px;color:var(--mut);margin-top:2px;word-break:break-word}
.step.done{background:var(--oksoft);border-color:transparent}.step.done .n{color:var(--ok)}.step.running{border-color:var(--acc);background:var(--accsoft)}.step.running .n{color:var(--acc)}
.step.failed,.step.blocked,.step.stalled{border-color:var(--bad);background:var(--badsoft)}.step.failed .n,.step.blocked .n,.step.stalled .n{color:var(--bad)}.step.pending{opacity:.65}
.ico{width:16px;display:inline-block;text-align:center}.spin{animation:pulse 1.2s infinite}
.mini{height:5px;background:var(--line);border-radius:5px;overflow:hidden;margin-top:6px}.mini>i{display:block;height:100%;background:var(--acc)}
.now{margin-top:10px;font-size:14px}.now b{font-weight:650}.trail{margin:10px 0 0;padding:0;list-style:none;font-size:13px;color:var(--mut);border-top:1px dashed var(--line);padding-top:8px}.trail li{padding:1px 0}
.log{max-height:62vh;overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:8px 10px;font:13px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;word-break:break-word}
.log div.error{color:var(--bad);font-weight:600}.log div.warn{color:var(--warn)}.log div.ok{color:var(--ok)}.log div.dim{color:var(--mut)}.log div.progress{color:var(--acc)}
.empty{text-align:center;color:var(--mut);padding:26px 10px;background:var(--card);border:1px dashed var(--line);border-radius:12px}
a{color:var(--acc)}.cmd{display:flex;gap:6px;align-items:flex-start}.cmd code{flex:1}
@media (max-width:640px){main{padding:12px}header{padding:8px 12px}table{display:block;overflow-x:auto}td code{white-space:nowrap}.live{margin-left:0}.step{flex-basis:46%}}
</style></head><body>
<header><h1>Job agent</h1><nav id="tabs" role="tablist"></nav><span class="live"><span class="dot" id="dot"></span><span id="stamp"></span></span></header>
<main id="main"><p class="mut">Loading…</p></main>
<script>
const NOVNC = __NOVNC__;
const TABS = [["overview","Overview"],["jobs","Jobs"],["questions","Questions"],["review","Review"],["queue","Queue"],["runs","Runs"],["outcomes","Outcomes"],["start","Start a run"]];
let tab = (location.hash||"#overview").slice(1), timer = null, counts = {}, RO = false, filters = {queue:"", runs:"", jobs:"all"}, openJob = {}, openLog = null;
const $ = (s,el=document)=>el.querySelector(s);
function h(tag, attrs={}, ...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==="class")e.className=v;else if(k.startsWith("on"))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v)}
  for(const k of kids.flat()){if(k==null||k===false)continue;e.append(k.nodeType?k:document.createTextNode(String(k)))}return e}
async function api(path){const r=await fetch(path,{credentials:"same-origin"});if(r.status===401){document.body.textContent="Signed out: open the address that `jobagent dashboard` printed.";throw new Error("401")}return r.json()}
async function post(path,body){const r=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Dashboard":"1"},body:JSON.stringify(body)});return {ok:r.ok,body:await r.json()}}
function table(cols,rows,cell){return h("table",{},h("thead",{},h("tr",{},cols.map(c=>h("th",{},c)))),h("tbody",{},rows.length?rows.map(r=>h("tr",{},cell(r).map(c=>h("td",{},c)))):h("tr",{},h("td",{colspan:cols.length,class:"mut"},"Nothing here."))))}
const parse=t=>t?new Date(t.replace(" ","T").replace(/(\+00:00)?Z?$/,"Z")):null;
function ago(t){const d=parse(t);if(!d)return"";const s=Math.max(0,(Date.now()-d)/1000);if(s<60)return"just now";if(s<3600)return Math.round(s/60)+" min ago";if(s<86400)return Math.round(s/3600)+" h ago";return Math.round(s/86400)+" d ago"}
const dur=s=>s<90?`${s}s`:s<5400?`${Math.round(s/60)} min`:`${(s/3600).toFixed(1)} h`;
function chip(text,kind){return h("span",{class:"chip "+(kind||cls(text))},text)}
function cls(s){s=String(s||"");return /fail|block|error|reject|stalled/.test(s)?"bad":/review|warn|waiting|unknown|pending|needs/.test(s)?"warn":/running/.test(s)?"run":/submit|confirm|ok|worked|filled|done|finished|applied|queued|scraped|interview|offer/.test(s)?"ok":""}
function copy(text){const b=h("button",{class:"act sec sm",onclick:async()=>{try{await navigator.clipboard.writeText(text);b.textContent="Copied"}catch(e){b.textContent="Select + copy"}setTimeout(()=>b.textContent="Copy",1500)}},"Copy");return b}
function cmd(text){return h("div",{class:"cmd"},h("code",{},text),copy(text))}
function tabs(){RO=!!counts.read_only;const nav=$("#tabs");nav.replaceChildren(...TABS.filter(([id])=>!(RO&&id==="start")).map(([id,label])=>{const b=h("button",{role:"tab","aria-selected":String(tab===id),onclick:()=>{tab=id;location.hash=id;draw()}},label);
  if(id==="questions"&&counts.questions)b.append(h("span",{class:"badge"},counts.questions));if(id==="review"&&counts.waiting)b.append(h("span",{class:"badge info"},counts.waiting));
  if(id==="jobs"&&counts.jobs_active)b.append(h("span",{class:"badge info"},counts.jobs_active));return b}))}
const ICON={done:"✓",running:"●",pending:"○",failed:"✕",blocked:"⏸",stalled:"!",skipped:"–"};
function stepCard(s){const el=h("div",{class:"step "+s.status});el.append(h("div",{class:"n"},h("span",{class:"ico"+(s.status==="running"?" spin":"")},ICON[s.status]||"○"),s.label));
  if(s.status==="running"||s.status==="stalled"||s.status==="failed"||s.status==="blocked"){
    const it=s.i!=null&&s.n?` ${s.i} of ${s.n}`:(s.i!=null?` ${s.i}`:"");
    if(s.step)el.append(h("div",{class:"s"},s.step+it));
    if(s.detail)el.append(h("div",{class:"s"},s.detail));
    if(s.i!=null&&s.n&&s.status==="running")el.append(h("div",{class:"mini"},h("i",{style:`width:${Math.min(100,Math.round(100*s.i/s.n))}%`})))}
  else if(s.status==="done"&&s.detail)el.append(h("div",{class:"s"},s.detail));
  return el}
function jobCard(j){const key=j.job||j.url;const done=j.state==="done";
  const cur=j.stages.find(s=>s.id===j.current);
  const stateText={running:"running",done:"finished",failed:"failed",blocked:"stopped",stalled:"stalled",waiting:"waiting"}[j.state]||j.state;
  const head=h("div",{class:"jh"},h("span",{class:"jt"},j.title||"(untitled)"),j.company?h("span",{class:"jc"},j.company):null,chip(stateText,j.state==="running"?"run":cls(j.state)),
    h("span",{class:"jr"},h("span",{},j.active?"running for "+dur(j.elapsed_s):"took "+dur(j.elapsed_s)),h("span",{},"updated "+ago(j.updated)),
      j.url?h("a",{href:j.url,target:"_blank",rel:"noopener noreferrer"},"posting"):null));
  const card=h("div",{class:"job"},head,h("div",{class:"bar "+j.state},h("i",{style:`width:${done?100:j.percent}%`})),h("div",{class:"steps"},j.stages.map(stepCard)));
  if(cur&&j.state==="running"){const it=cur.i!=null&&cur.n?` — ${cur.step||cur.label}, step ${cur.i} of ${cur.n}`:(cur.step?` — ${cur.step}`:"");
    card.append(h("div",{class:"now"},h("b",{},cur.label),it,cur.detail?h("span",{class:"mut"},"  ·  "+cur.detail):null))}
  const trail=h("ul",{class:"trail"},j.trail.slice().reverse().map(e=>h("li",{},`${ago(e.ts)} · ${e.stage}${e.label?" · "+e.label:""}${e.i!=null&&e.n?` ${e.i}/${e.n}`:""} · ${e.status}${e.detail?" — "+e.detail:""}`)));
  const toggle=h("button",{class:"act sec sm",onclick:()=>{openJob[key]=!openJob[key];trail.hidden=!openJob[key];toggle.textContent=openJob[key]?"Hide history":"History"}},openJob[key]?"Hide history":"History");
  trail.hidden=!openJob[key];card.append(h("div",{style:"margin-top:10px"},toggle),trail);return card}
function empty(text){return h("div",{class:"empty"},text)}
const views={
 async overview(m){const o=await api("/api/overview");counts=o;tabs();const jobs=await api("/api/pipeline");
  const card=(n,l,hot)=>h("div",{class:"card"+(hot&&n?" hot":"")},h("b",{},n),h("span",{},l));
  const sum=d=>Object.entries(d||{}).map(([k,v])=>h("span",{style:"margin-right:12px"},chip(k),` ${v}`));
  if(RO)m.append(h("div",{class:"box"},"Read-only view: you can look at everything here, but answering questions and starting runs is done in a terminal."));
  m.append(h("div",{class:"cards"},card(o.questions,"questions waiting for you",true),card(o.waiting,"applications to review"),card(o.ready,"of them ready to send"),card(o.jobs_active||0,"jobs working now")));
  const live=jobs.filter(j=>j.active);m.append(h("h2",{},"Working now"));
  if(live.length)live.forEach(j=>m.append(jobCard(j)));else m.append(empty("Nothing is running. Start a run from the Start tab or `jobagent run` in a terminal."));
  const recent=jobs.filter(j=>!j.active).slice(0,4);if(recent.length){m.append(h("h2",{},"Recently finished"));recent.forEach(j=>m.append(jobCard(j)))}
  m.append(h("h2",{},"Totals"),h("div",{class:"box"},h("div",{},h("b",{},"Queue  "),sum(o.jobs))),h("div",{class:"box"},h("div",{},h("b",{},"Applications  "),sum(o.applications))),
   h("div",{class:"box"},h("div",{},h("b",{},"Outcomes  "),sum(o.outcomes))),NOVNC?h("p",{},"Browser view: ",h("a",{href:NOVNC,target:"_blank",rel:"noopener"},NOVNC)):null)},
 async jobs(m){const all=await api("/api/pipeline");const f=filters.jobs;
  const pick=(id,label)=>h("button",{class:"act sm "+(f===id?"":"sec"),onclick:()=>{filters.jobs=id;draw()}},label);
  m.append(h("div",{class:"tools"},pick("all","All"),pick("active","Working now"),pick("attention","Needs attention"),pick("done","Finished")));
  const rows=all.filter(j=>f==="all"||(f==="active"&&j.active)||(f==="done"&&j.state==="done")||(f==="attention"&&["failed","blocked","stalled"].includes(j.state)));
  if(!rows.length)return m.append(empty("No jobs in this view yet."));rows.forEach(j=>m.append(jobCard(j)))},
 async questions(m){const q=await api("/api/hitl");if(!q.length)return m.append(empty("The agent is not waiting on anything."));
  for(const x of q){const ta=h("textarea",{rows:"2",placeholder:x.default?`default: ${x.default}`:"your answer"});const msg=h("span",{class:"mut"});
   m.append(h("div",{class:"box"},h("div",{},h("b",{},"Question "+x.id),h("span",{class:"mut"},"  "+ago(x.created))),h("p",{},x.prompt),
    (x.secret||RO)?h("div",{},h("p",{class:"warn"},x.secret?"This asks for a secret. Answer it in a terminal, never here:":"Answer it in a terminal:"),cmd(x.terminal_command)):
    h("div",{},ta,h("div",{style:"margin-top:8px"},h("button",{class:"act",onclick:async()=>{const r=await post("/api/hitl/answer",{id:x.id,value:ta.value});msg.textContent=r.ok?"  Sent.":("  "+(r.body.error||"failed"));if(r.ok)setTimeout(draw,600)}},"Send answer"),msg))))}},
 async review(m){const r=await api("/api/review");m.append(h("p",{class:"mut"},"Nothing is sent from this page. Sending an application needs your typed approval in a terminal, per application."));
  if(!r.length)return m.append(empty("No application is waiting for your review."));
  m.append(table(["#","Company","Title","Ready","Waiting","Blockers","Open its review (copy into a terminal)"],r,x=>[x.id,x.company,x.title,chip(x.ready?"ready":"not ready",x.ready?"ok":"warn"),x.age_hours!=null?x.age_hours+" h":"-",
   h("div",{},x.blockers.length?x.blockers.slice(0,3).map(b=>h("div",{class:"warn"},b)):"-"),cmd(x.review_command)]))},
 async queue(m){const r=await api("/api/queue");const inp=h("input",{type:"search",placeholder:"Filter by company, title or status",value:filters.queue,oninput:e=>{filters.queue=e.target.value;fill()}});
  const wrap=h("div");const fill=()=>{const q=filters.queue.toLowerCase();const rows=r.filter(x=>!q||`${x.company} ${x.title} ${x.status} ${x.role_id} ${x.notes}`.toLowerCase().includes(q));
   wrap.replaceChildren(table(["Company","Title","Status","Role","Score","Updated","Note"],rows,x=>[x.company,x.title,chip(x.status),x.role_id||"",x.match_score??"",ago(x.updated_at),h("span",{class:"mut"},x.notes||"")]))};
  m.append(h("div",{class:"tools"},inp,h("span",{class:"mut"},`${r.length} postings`)),wrap);fill()},
 async runs(m){const r=await api("/api/runs");const box=h("div");const inp=h("input",{type:"search",placeholder:"Filter by step, job or message",value:filters.runs,oninput:e=>{filters.runs=e.target.value;fill()}});
  const wrap=h("div");
  const show=async(id,lvl)=>{openLog=id;const l=await api("/api/run/"+id);const only=lvl==="problems"?l.filter(e=>["error","warn"].includes(e.level)):l;
   box.replaceChildren(h("div",{class:"tools"},h("b",{},id),h("button",{class:"act sec sm",onclick:()=>show(id,"all")},"All"),h("button",{class:"act sec sm",onclick:()=>show(id,"problems")},"Problems only")),
    h("div",{class:"log"},only.map(e=>h("div",{class:e.level},`${e.ts.slice(11,19)} ${e.level.padEnd(8)} [${e.step}] ${e.msg}`))))};
  const fill=()=>{const q=filters.runs.toLowerCase();const rows=r.filter(x=>!q||`${x.step} ${x.job} ${x.message}`.toLowerCase().includes(q));
   wrap.replaceChildren(table(["Step","Job","Started","State","Lines","Errors","Last line",""],rows,x=>[x.step,x.job,ago(x.started),chip(x.state==="waiting"?`waiting ${Math.round(x.quiet_s/60)} min`:x.state,x.state==="running"?"run":x.state==="finished"?"ok":x.state==="stalled"?"bad":"warn"),x.lines,
    x.errors?chip(String(x.errors),"bad"):"0",h("span",{class:"mut"},x.message),h("button",{class:"act sec sm",onclick:()=>show(x.id,"all")},"Log")]))};
  m.append(h("div",{class:"tools"},inp,h("span",{class:"mut"},`${r.length} recent runs`)),wrap,box);fill();if(openLog&&r.some(x=>x.id===openLog))show(openLog,"all")},
 async outcomes(m){const o=await api("/api/outcomes");m.append(h("div",{class:"box"},Object.entries(o.counts).map(([k,v])=>h("span",{style:"margin-right:12px"},chip(k),` ${v}`)).concat(Object.keys(o.counts).length?[]:["No outcomes recorded yet."])),
  table(["Received","Stage","Company","Title","Subject"],o.recent,x=>[ago(x.received_at),chip(x.stage),x.company,x.title,h("span",{class:"mut"},x.subject||"")]))},
 async start(m){const inp=h("textarea",{rows:"4",placeholder:"One per line: job URLs, company names, or a CSV/XLSX file name"});const thr=h("select",{},["scrape","tailor","apply"].map(v=>h("option",{value:v,selected:v==="apply"},v)));
  const lim=h("input",{type:"number",min:"1",max:"20",value:"5",style:"width:70px"});const msg=h("p",{class:"mut"});
  m.append(h("div",{class:"box"},h("p",{},"Starts ",h("code",{},"jobagent run")," in the background. It goes as far as the choice below and stops at the review screen: it cannot submit."),inp,
   h("p",{},"Go as far as ",thr,"  ·  jobs per company input ",lim),h("button",{class:"act",onclick:async()=>{const r=await post("/api/run",{inputs:inp.value.split("\n").map(s=>s.trim()).filter(Boolean),through:thr.value,limit:Number(lim.value)});
    msg.textContent=r.ok?`Started (pid ${r.body.pid}). ${r.body.note}`:(r.body.error||"failed");msg.className=r.ok?"ok":"bad";if(r.ok){setTimeout(()=>{tab="jobs";location.hash="jobs";draw()},1800)}}},"Start"),msg))}};
async function draw(){clearTimeout(timer);const m=$("#main");const t=tab;tabs();const fresh=h("div");try{await views[t]?.(fresh)}catch(e){if(String(e).includes("401"))return;fresh.append(h("p",{class:"bad"},"Could not load: "+e))}
  if(t===tab){m.replaceChildren(fresh);$("#stamp").textContent=(counts.jobs_active?"live · ":"")+"updated "+new Date().toLocaleTimeString();$("#dot").className="dot"+(counts.jobs_active?" on":"")}
  later(t==="questions"||t==="start"||t==="runs"||t==="queue"?8000:(counts.jobs_active?3000:7000))}
function busy(){const a=document.activeElement;return (a&&["TEXTAREA","INPUT","SELECT"].includes(a.tagName))||[...document.querySelectorAll("main textarea")].some(x=>x.value)}
function later(ms){clearTimeout(timer);timer=setTimeout(()=>busy()?later(ms):draw(),ms)}   // never rebuild the page under someone who is typing
api("/api/overview").then(o=>{counts=o;draw()}).catch(()=>{});
</script></body></html>"""
