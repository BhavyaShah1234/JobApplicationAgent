"""The one-page front end: plain HTML and JavaScript, no external files. Everything from the agent's data is put on the page with
`textContent`, never as HTML, so a scraped job title cannot inject markup."""

PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Job agent</title>
<style>
:root{--bg:#f7f7f5;--fg:#1d1d1b;--mut:#6b6b66;--card:#fff;--line:#e2e2dc;--acc:#1f5fbf;--ok:#1b7a3d;--warn:#a15c00;--bad:#b3261e}
@media (prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecece8;--mut:#9a9a93;--card:#202020;--line:#33332f;--acc:#7aa7ee;--ok:#5fcf84;--warn:#e0a24a;--bad:#f0837c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
header{display:flex;flex-wrap:wrap;gap:12px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line);background:var(--card)}
h1{font-size:17px;margin:0}nav{display:flex;flex-wrap:wrap;gap:6px}
nav button{border:1px solid var(--line);background:var(--bg);color:var(--fg);padding:6px 11px;border-radius:7px;cursor:pointer;font:inherit}
nav button[aria-selected=true]{background:var(--acc);color:#fff;border-color:var(--acc)}
.badge{background:var(--bad);color:#fff;border-radius:10px;padding:0 7px;font-size:12px;margin-left:5px}
main{padding:16px;max-width:1200px;margin:0 auto}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px 12px}.card b{font-size:22px;display:block}.card span{color:var(--mut);font-size:13px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top;font-size:14px}th{color:var(--mut);font-weight:600;font-size:12px;text-transform:uppercase}
.mut{color:var(--mut)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}code{background:var(--bg);padding:1px 5px;border-radius:4px;word-break:break-all}
.box{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px;margin-bottom:12px}
input,select,textarea{font:inherit;color:var(--fg);background:var(--bg);border:1px solid var(--line);border-radius:7px;padding:6px 8px}textarea{width:100%}
.act{background:var(--acc);color:#fff;border:0;border-radius:7px;padding:7px 14px;cursor:pointer;font:inherit}.log{max-height:60vh;overflow:auto;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:8px;font:13px/1.4 ui-monospace,monospace;white-space:pre-wrap}
a{color:var(--acc)}@media (max-width:640px){main{padding:8px}table{display:block;overflow-x:auto}td code{white-space:nowrap}}
</style></head><body>
<header><h1>Job agent</h1><nav id="tabs" role="tablist"></nav><span id="stamp" class="mut" style="margin-left:auto"></span></header>
<main id="main"><p class="mut">Loading…</p></main>
<script>
const NOVNC = __NOVNC__;
const TABS = [["overview","Overview"],["questions","Questions"],["review","Review"],["queue","Queue"],["runs","Runs"],["outcomes","Outcomes"],["start","Start a run"]];
let tab = (location.hash||"#overview").slice(1), timer = null, counts = {};
const $ = (s,el=document)=>el.querySelector(s);
function h(tag, attrs={}, ...kids){const e=document.createElement(tag);for(const[k,v]of Object.entries(attrs)){if(k==="class")e.className=v;else if(k.startsWith("on"))e.addEventListener(k.slice(2),v);else e.setAttribute(k,v)}
  for(const k of kids.flat()){if(k==null)continue;e.append(k.nodeType?k:document.createTextNode(String(k)))}return e}
async function api(path){const r=await fetch(path,{credentials:"same-origin"});if(r.status===401){document.body.textContent="Signed out: open the address that `jobagent dashboard` printed.";throw new Error("401")}return r.json()}
async function post(path,body){const r=await fetch(path,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-Dashboard":"1"},body:JSON.stringify(body)});return {ok:r.ok,body:await r.json()}}
function table(cols,rows,cell){return h("table",{},h("thead",{},h("tr",{},cols.map(c=>h("th",{},c)))),h("tbody",{},rows.length?rows.map(r=>h("tr",{},cell(r).map(c=>h("td",{},c)))):h("tr",{},h("td",{colspan:cols.length,class:"mut"},"Nothing here yet."))))}
const when=t=>t?t.replace("T"," ").replace("Z",""):"";
const cls=s=>/fail|block|error|reject/.test(s)?"bad":/review|warn|unknown|pending/.test(s)?"warn":/submit|confirm|ok|worked|filled/.test(s)?"ok":"";
function tabs(){const nav=$("#tabs");nav.replaceChildren(...TABS.map(([id,label])=>{const b=h("button",{role:"tab","aria-selected":String(tab===id),onclick:()=>{tab=id;location.hash=id;draw()}},label);
  if(id==="questions"&&counts.questions)b.append(h("span",{class:"badge"},counts.questions));if(id==="review"&&counts.waiting)b.append(h("span",{class:"badge",style:"background:var(--acc)"},counts.waiting));return b}))}
const views={
 async overview(m){const o=await api("/api/overview");counts=o;tabs();
  const card=(n,l)=>h("div",{class:"card"},h("b",{},n),h("span",{},l));
  const sum=d=>Object.entries(d||{}).map(([k,v])=>`${v} ${k}`).join(" · ")||"none";
  m.append(h("div",{class:"cards"},card(o.questions,"questions waiting for you"),card(o.waiting,"applications to review"),card(o.ready,"of them ready"),card(o.runs_active,"runs active")),
   h("div",{class:"box"},h("div",{},h("b",{},"Queue: "),sum(o.jobs))),h("div",{class:"box"},h("div",{},h("b",{},"Applications: "),sum(o.applications))),
   h("div",{class:"box"},h("div",{},h("b",{},"Outcomes: "),sum(o.outcomes))),
   NOVNC?h("p",{},"Browser view: ",h("a",{href:NOVNC,target:"_blank",rel:"noopener"},NOVNC)):null);},
 async questions(m){const q=await api("/api/hitl");if(!q.length)return m.append(h("p",{class:"mut"},"The agent is not waiting on anything."));
  for(const x of q){const ta=h("textarea",{rows:"2",placeholder:x.default?`default: ${x.default}`:"your answer"});const msg=h("span",{class:"mut"});
   m.append(h("div",{class:"box"},h("div",{},h("b",{},"Question "+x.id),h("span",{class:"mut"},"  "+when(x.created))),h("p",{},x.prompt),
    x.secret?h("p",{class:"warn"},"This asks for a secret. Answer it in a terminal, never here: ",h("code",{},x.terminal_command)):
    h("div",{},ta,h("div",{},h("button",{class:"act",onclick:async()=>{const r=await post("/api/hitl/answer",{id:x.id,value:ta.value});msg.textContent=r.ok?"Sent.":(r.body.error||"failed");if(r.ok)setTimeout(draw,600)}},"Send answer"),msg))));}},
 async review(m){const r=await api("/api/review");m.append(h("p",{class:"mut"},"Nothing is sent from this page. Sending an application needs your typed approval in a terminal, per application."),
  table(["#","Company","Title","Ready","Waiting h","Blockers","Open its review (copy into a terminal)"],r,x=>[x.id,x.company,x.title,h("span",{class:x.ready?"ok":"warn"},x.ready?"yes":"no"),x.age_hours??"-",
   h("div",{},x.blockers.length?x.blockers.slice(0,3).map(b=>h("div",{class:"warn"},b)):"-"),h("code",{},x.review_command)]))},
 async queue(m){const r=await api("/api/queue");m.append(table(["Company","Title","Status","Role","Score","Updated","Note"],r,x=>[x.company,x.title,h("span",{class:cls(x.status)},x.status),x.role_id||"",x.match_score??"",when(x.updated_at),h("span",{class:"mut"},x.notes||"")]))},
 async runs(m){const r=await api("/api/runs");const box=h("div");m.append(table(["Step","Job","Started","Lines","Errors","Last line",""],r,x=>[x.step,x.job,when(x.started),x.lines,h("span",{class:x.errors?"bad":""},x.errors),
   h("span",{class:"mut"},(x.running?"● running  ":"")+x.message),h("button",{class:"act",onclick:async()=>{const l=await api("/api/run/"+x.id);box.replaceChildren(h("div",{class:"log"},l.map(e=>`${e.ts.slice(11,19)} ${e.level.padEnd(5)} [${e.step}] ${e.msg}`).join("\n")))}},"Log")]),box)},
 async outcomes(m){const o=await api("/api/outcomes");m.append(h("div",{class:"box"},Object.entries(o.counts).map(([k,v])=>`${v} ${k}`).join(" · ")||"No outcomes recorded yet."),
  table(["Received","Stage","Company","Title","Subject"],o.recent,x=>[when(x.received_at),h("span",{class:cls(x.stage)},x.stage),x.company,x.title,h("span",{class:"mut"},x.subject||"")]))},
 async start(m){const inp=h("textarea",{rows:"4",placeholder:"One per line: job URLs, company names, or a CSV/XLSX file name"});const thr=h("select",{},["scrape","tailor","apply"].map(v=>h("option",{value:v,selected:v==="apply"},v)));
  const lim=h("input",{type:"number",min:"1",max:"20",value:"5",style:"width:70px"});const msg=h("p",{class:"mut"});
  m.append(h("div",{class:"box"},h("p",{},"Starts ",h("code",{},"jobagent run")," in the background. It goes as far as the choice below and stops at the review screen: it cannot submit."),inp,
   h("p",{},"Go as far as ",thr," · jobs per company input ",lim),h("button",{class:"act",onclick:async()=>{const r=await post("/api/run",{inputs:inp.value.split("\n").map(s=>s.trim()).filter(Boolean),through:thr.value,limit:Number(lim.value)});
    msg.textContent=r.ok?`Started (pid ${r.body.pid}). ${r.body.note}`:(r.body.error||"failed");msg.className=r.ok?"ok":"bad"}},"Start"),msg))}};
async function draw(){clearTimeout(timer);const m=$("#main");const t=tab;tabs();const fresh=h("div");try{await views[t]?.(fresh)}catch(e){if(String(e).includes("401"))return;fresh.append(h("p",{class:"bad"},"Could not load: "+e))}
  if(t===tab){m.replaceChildren(fresh);$("#stamp").textContent="updated "+new Date().toLocaleTimeString()}
  if(t!=="questions"&&t!=="start")timer=setTimeout(draw,6000);else timer=setTimeout(()=>{if(!document.querySelector("textarea:focus"))draw()},8000)}
api("/api/overview").then(o=>{counts=o;draw()}).catch(()=>{});
</script></body></html>"""
