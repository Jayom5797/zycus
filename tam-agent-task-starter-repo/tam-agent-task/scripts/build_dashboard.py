"""Build a self-contained EVALUATION dashboard from outputs/evaluation.json.

This is a presentation layer over real evaluation artifacts - it does not
evaluate anything itself. Run order:

    python run.py --set review          # produce outputs/*.json + run_log.jsonl
    python scripts/eval.py --json       # produce outputs/evaluation.json
    python scripts/build_dashboard.py   # produce outputs/dashboard.html

If evaluation.json is missing, the dashboard renders a clear "Not run" state
rather than fabricating metrics. All metrics shown are read from the artifact.
Ticket text is HTML-escaped in the browser before rendering.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
EVAL = OUT / "evaluation.json"


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zycus TAM Agent · Evaluation Dashboard</title>
<style>
:root{
  --blue:#1457d6;--blue-ink:#0b2e73;--ink:#111a2b;--mut:#5b6677;--soft:#93a0b4;
  --line:#e6eaf0;--bg:#f7f9fc;--card:#fff;
  --pass:#1a9d5a;--fail:#d13d3d;--warn:#c57a12;--na:#8a95a6;
  --pass-bg:#e9f7ef;--fail-bg:#fdecec;--warn-bg:#fdf3e3;--na-bg:#f0f2f5;
  --shadow:0 1px 2px rgba(11,46,115,.05),0 2px 8px rgba(11,46,115,.04);
}
*{box-sizing:border-box}html,body{margin:0}
body{font:14px/1.5 'Inter',-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:var(--ink);background:var(--bg);-webkit-font-smoothing:antialiased}
.container{max-width:1200px;margin:0 auto;padding:0 20px}
/* header */
header{border-bottom:1px solid var(--line);background:var(--card)}
.hwrap{display:flex;align-items:center;gap:14px;padding:16px 0}
.mark{width:34px;height:34px;border-radius:8px;background:var(--blue);display:grid;place-items:center;color:#fff;font-weight:800;font-size:18px}
.htxt h1{margin:0;font-size:17px;letter-spacing:-.2px}
.htxt .sub{color:var(--mut);font-size:12.5px}
.hmeta{margin-left:auto;text-align:right;font-size:12px;color:var(--mut)}
.status{display:inline-flex;align-items:center;gap:7px;font-weight:700;font-size:13px;padding:5px 12px;border-radius:999px;margin-top:3px}
.s-passed{background:var(--pass-bg);color:var(--pass)}.s-failed{background:var(--fail-bg);color:var(--fail)}
.s-incomplete{background:var(--warn-bg);color:var(--warn)}.s-not_run{background:var(--na-bg);color:var(--na)}
.status .d{width:8px;height:8px;border-radius:50%;background:currentColor}
/* metric cards */
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}
.mc{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px 16px;box-shadow:var(--shadow)}
.mc .k{font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:var(--mut);font-weight:700}
.mc .v{font-size:27px;font-weight:800;letter-spacing:-.6px;margin:5px 0 2px}
.mc .v .pct{font-size:15px;color:var(--soft);font-weight:700}
.mc .bar{height:6px;border-radius:999px;background:#eef1f5;overflow:hidden;margin-top:9px}
.mc .bar>i{display:block;height:100%;border-radius:999px}
.mc .sub{font-size:11.5px;color:var(--mut);margin-top:7px}
.mc.na .v{color:var(--na)}
/* critical banner */
.crit{background:var(--fail-bg);border:1px solid #f6c9c9;color:#9b1c1c;border-radius:10px;padding:11px 15px;margin-bottom:18px;font-weight:600;font-size:13px;display:flex;align-items:center;gap:9px}
/* table */
.section-h{font-size:12px;letter-spacing:.5px;text-transform:uppercase;color:var(--mut);font-weight:700;margin:4px 0 10px}
.tablewrap{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;box-shadow:var(--shadow)}
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:10px 12px;background:#f8fafc;color:var(--mut);font-weight:700;font-size:11px;letter-spacing:.4px;text-transform:uppercase;border-bottom:1px solid var(--line)}
td{padding:11px 12px;border-bottom:1px solid #f1f4f8;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr.trow{cursor:pointer;transition:background .1s}
tr.trow:hover{background:#f7faff}
tr.trow.active{background:#eef4ff;box-shadow:inset 3px 0 0 var(--blue)}
.tid{font-weight:700}
.mono{font-variant-numeric:tabular-nums}
.diff{color:var(--fail);font-weight:700}
.match{color:var(--ink)}
.tag{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:999px;font-size:11.5px;font-weight:700}
.t-pass{background:var(--pass-bg);color:var(--pass)}.t-fail{background:var(--fail-bg);color:var(--fail)}
.t-partial{background:var(--warn-bg);color:var(--warn)}.t-na{background:var(--na-bg);color:var(--na)}
.t-invalid{background:#efe6fb;color:#6b21a8}
.crit-dot{color:var(--fail);font-weight:900;margin-left:4px}
/* inspection panel */
.inspect{margin:20px 0;background:var(--card);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow);overflow:hidden}
.ihead{padding:15px 18px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;flex-wrap:wrap;background:#fbfcfe}
.ihead h2{margin:0;font-size:17px}
.igrid{display:grid;grid-template-columns:1fr 1fr;gap:0}
.ipane{padding:16px 18px}.ipane+.ipane{border-left:1px solid var(--line)}
h4{margin:0 0 10px;font-size:11px;letter-spacing:.5px;text-transform:uppercase;color:var(--mut);font-weight:700}
.kv{display:grid;grid-template-columns:120px 1fr 1fr;gap:4px 10px;font-size:13px;align-items:center}
.kv .hd{font-size:10.5px;text-transform:uppercase;color:var(--soft);font-weight:700}
.kv .f{color:var(--mut)}
.pz{display:inline-flex;align-items:center;justify-content:center;min-width:30px;padding:2px 8px;border-radius:6px;font-size:11px;font-weight:800;color:#fff}
.z-P1{background:var(--fail)}.z-P2{background:var(--warn)}.z-P3{background:var(--blue)}.z-P4{background:var(--pass)}
.badges{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.badge{background:#f1f5fb;border:1px solid var(--line);border-radius:7px;padding:3px 9px;font-size:12px;font-weight:600;color:#374151}
.badge.sec{background:var(--fail);color:#fff;border-color:var(--fail)}
.kb{display:inline-block;background:#eef3fb;color:var(--blue);border:1px solid #d4e2fb;border-radius:6px;padding:2px 8px;margin:0 5px 5px 0;font-size:12px;font-weight:700}
.none{color:var(--soft);font-style:italic}
.sig{margin:4px 0;padding-left:15px;position:relative;font-size:12.5px}.sig:before{content:"▹";position:absolute;left:0;color:var(--blue)}
.trace{list-style:none;margin:0;padding:0}
.step{position:relative;padding:0 0 12px 24px;font-size:12.5px}
.step:before{content:"";position:absolute;left:7px;top:15px;bottom:-1px;width:2px;background:var(--line)}
.step:last-child:before{display:none}
.step .dot{position:absolute;left:2px;top:2px;width:13px;height:13px;border-radius:50%;background:#fff;border:2.5px solid #c2ccd9}
.step.tool .dot{border-color:var(--blue)}
.step.pol .dot{border-color:var(--fail);background:var(--fail)}
.step .act{font-weight:700}.step.pol .act{color:var(--fail)}
.step .src{font-size:10px;background:var(--fail-bg);color:var(--fail);border:1px solid #f6c9c9;border-radius:999px;padding:0 7px;margin-left:5px;font-weight:700}
.step .obs{color:var(--mut);white-space:pre-wrap;margin-top:2px}
details{margin-top:10px}summary{cursor:pointer;color:var(--blue);font-size:12px;font-weight:600}
.reply{white-space:pre-wrap;background:#f8fafc;border:1px solid var(--line);border-left:3px solid var(--blue);border-radius:8px;padding:10px 12px;font-size:12.5px;color:#374151;margin-top:6px}
.cfail{color:var(--fail);font-weight:700;font-size:12px;margin-top:8px}
/* reliability */
.rel{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:4px 0 26px}
.rc{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px;box-shadow:var(--shadow)}
.rc .k{font-size:11px;color:var(--mut);font-weight:700;text-transform:uppercase;letter-spacing:.4px}
.rc .v{font-size:19px;font-weight:800;margin-top:3px}.rc .v small{font-size:12px;color:var(--soft);font-weight:600}
.note{font-size:11.5px;color:var(--mut);margin:0 0 24px}
.empty{padding:40px;text-align:center;color:var(--mut)}
@media(max-width:860px){.cards,.rel{grid-template-columns:repeat(2,1fr)}.igrid{grid-template-columns:1fr}.ipane+.ipane{border-left:none;border-top:1px solid var(--line)}
  table{font-size:12px}th:nth-child(4),td:nth-child(4),th:nth-child(5),td:nth-child(5){display:none}}
</style></head>
<body>
<header><div class="container"><div class="hwrap">
  <div class="mark">Z</div>
  <div class="htxt"><h1>Zycus TAM Agent</h1><div class="sub">Agentic Ticket Triage · Evaluation Dashboard</div></div>
  <div class="hmeta" id="hmeta"></div>
</div></div></header>

<div class="container">
  <div id="crit"></div>
  <div class="cards" id="cards"></div>

  <div class="section-h">Ticket Evaluation · review set</div>
  <div class="tablewrap"><table>
    <thead><tr>
      <th>Ticket</th><th>Priority (exp / act)</th><th>Category (exp / act)</th>
      <th>Route (exp / act)</th><th>Policy</th><th>Evidence</th><th>Result</th>
    </tr></thead><tbody id="tbody"></tbody>
  </table></div>

  <div id="inspect"></div>

  <div class="section-h">Execution &amp; Cost</div>
  <div class="rel" id="rel"></div>
  <p class="note" id="relnote"></p>
</div>

<script>
const EVAL = __DATA__;
const esc = s => (s==null?'':String(s)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = v => Array.isArray(v)?v.join('|'):(v===true?'true':v===false?'false':(v==null?'—':String(v)));
const POL=/^(enforce_|ignore_injected|evidence_guard)/, TOOLS=/^(get_policy|resolve_account|get_account_health|find_related_tickets|search_kb|get_kb_article)$/;

function pctColor(r){ return r>=1?'var(--pass)':r>=0.8?'var(--warn)':'var(--fail)'; }

function renderHeader(){
  const st=EVAL.run_status, map={passed:'PASSED',failed:'FAILED',incomplete:'INCOMPLETE',not_run:'NOT RUN'};
  document.getElementById('hmeta').innerHTML=
    `<div>dataset <b>${esc(EVAL.dataset||'—')}</b></div>`+
    `<div>evaluated ${esc(EVAL.generated_at||'—')}</div>`+
    `<span class="status s-${st}"><span class="d"></span>${map[st]||'UNKNOWN'}</span>`;
}
function metricCard(key,m,unit){
  if(!m||m.den===0){return `<div class="mc na"><div class="k">${key}</div><div class="v">Not available</div><div class="sub">no evaluated tickets</div></div>`;}
  const r=m.num/m.den, pct=Math.round(r*100);
  return `<div class="mc"><div class="k">${key}</div>
    <div class="v">${m.num}/${m.den} <span class="pct">· ${pct}%</span></div>
    <div class="bar"><i style="width:${pct}%;background:${pctColor(r)}"></i></div>
    <div class="sub">${esc(m.label)}</div></div>`;
}
function renderCards(){
  const m=EVAL.metrics;
  document.getElementById('cards').innerHTML=
    metricCard('Overall accuracy',m.accuracy)+
    metricCard('Policy compliance',m.policy)+
    metricCard('Evidence coverage',m.evidence)+
    metricCard('Execution reliability',m.execution);
  const cf=EVAL.critical_fails||[];
  document.getElementById('crit').innerHTML = cf.length
    ? `<div class="crit">⚠ Critical policy failure on ${cf.length} ticket(s): <b>${cf.map(esc).join(', ')}</b> — a mandatory security rule was violated.</div>` : '';
}
function statusTag(s){
  const m={pass:['t-pass','Pass'],fail:['t-fail','Fail'],invalid:['t-invalid','Invalid'],not_evaluated:['t-na','Not evaluated']};
  const [c,l]=m[s]||['t-na',s]; return `<span class="tag ${c}">${l}</span>`;
}
function cell(field,t){
  const f=(t.fields||{})[field];
  if(!f) return '<span class="none">—</span>';
  const same=f.pass, exp=fmt(f.expected), act=fmt(f.actual);
  if(exp===act) return `<span class="match">${esc(act)}</span>`;
  return `<span class="f" style="color:var(--mut)">${esc(exp)}</span> <span class="${same?'match':'diff'}">→ ${esc(act)}</span>`;
}
function famTag(t,fam){
  if(t.status==='not_evaluated') return '<span class="tag t-na">—</span>';
  if(t.status==='invalid') return '<span class="tag t-invalid">Invalid</span>';
  const cs=(t.checks||[]).filter(c=>c.family===fam);
  if(!cs.length) return '<span class="tag t-na">n/a</span>';
  const pass=cs.every(c=>c.pass), crit=cs.some(c=>c.critical&&!c.pass);
  return pass?'<span class="tag t-pass">Pass</span>':`<span class="tag t-fail">Fail${crit?' <span class="crit-dot">●</span>':''}</span>`;
}
function priCell(t){
  const f=(t.fields||{}).priority;
  const act=f?fmt(f.actual):(t.out?t.out.priority:null);
  const exp=f?fmt(f.expected):'—';
  const pz=act?`<span class="pz z-${esc((t.out&&t.out.priority)||'')}">${esc(act)}</span>`:'<span class="none">—</span>';
  const ok=f?f.pass:true;
  return `<span class="f" style="color:var(--mut)">${esc(exp)}</span> ${pz}${ok?'':' <span class="crit-dot">✗</span>'}`;
}
function renderTable(){
  document.getElementById('tbody').innerHTML=EVAL.tickets.map((t,i)=>`
    <tr class="trow" data-i="${i}">
      <td class="tid">${esc(t.ticket_id)}</td>
      <td>${priCell(t)}</td>
      <td>${cell('category',t)}</td>
      <td>${cell('route_to',t)}</td>
      <td>${famTag(t,'policy')}</td>
      <td>${famTag(t,'evidence')}</td>
      <td>${statusTag(t.status)}</td>
    </tr>`).join('');
  document.querySelectorAll('.trow').forEach(r=>r.onclick=()=>{
    document.querySelectorAll('.trow').forEach(x=>x.classList.remove('active'));
    r.classList.add('active'); inspect(EVAL.tickets[+r.dataset.i]);
  });
}
function inspect(t){
  const el=document.getElementById('inspect');
  if(t.status==='not_evaluated'){ el.innerHTML=`<div class="inspect"><div class="empty"><b>${esc(t.ticket_id)}</b> — no output file produced. Status: <b>Not evaluated</b>. Run <code>python run.py --set review</code>.</div></div>`; return; }
  const o=t.out||{};
  const fields=Object.entries(t.fields||{}).map(([k,f])=>`
    <div class="hd">${esc(k)}</div><div class="f">${esc(fmt(f.expected))}</div>
    <div class="${f.pass?'match':'diff'}">${esc(fmt(f.actual))}${f.pass?'':' ✗'}</div>`).join('');
  const kb=(o.kb_references||[]).map(k=>`<span class="kb">📄 ${esc(k)}</span>`).join('')||'<span class="none">none cited</span>';
  const sig=(o.account_signals||[]).map(s=>`<div class="sig">${esc(s)}</div>`).join('')||'<span class="none">none</span>';
  const steps=(o.reasoning_trace||[]).map(s=>{
    const pol=('policy_source' in s)||POL.test(s.action), tool=TOOLS.test(s.action);
    const c=pol?'pol':tool?'tool':'';
    return `<li class="step ${c}"><span class="dot"></span><span class="act">${s.step}. ${esc(s.action)}${('policy_source' in s)?`<span class="src">${esc(s.policy_source)}</span>`:''}</span><div class="obs">${esc(String(s.observation||'')).slice(0,260)}</div></li>`;
  }).join('');
  const failing=(t.checks||[]).filter(c=>!c.pass);
  const conf=(o.confidence!=null)?`${Math.round(o.confidence*100)}% <small style="color:var(--soft)">(uncalibrated)</small>`:'—';
  const badges=[o.category==='security'?'<span class="badge sec">🔒 security</span>':`<span class="badge">${esc(o.category)}</span>`,
    o.escalate?'<span class="badge">escalated</span>':'', o.notify_tam?'<span class="badge">TAM notified</span>':'',
    o.duplicate_of?`<span class="badge">dup of ${esc(o.duplicate_of)}</span>`:'', o.needs_clarification?'<span class="badge">needs info</span>':''].filter(Boolean).join('');
  el.innerHTML=`<div class="inspect">
    <div class="ihead"><span class="pz z-${esc(o.priority||'')}">${esc(o.priority||'?')}</span>
      <h2>${esc(t.ticket_id)}</h2>${statusTag(t.status)}
      <span style="color:var(--mut);font-size:12.5px">account ${esc(o.account_id||'unresolved')} · confidence ${conf} · exec ${esc(t.exec_status||'—')}</span></div>
    <div style="padding:10px 18px 0">${badges}</div>
    <div class="igrid">
      <div class="ipane">
        <h4>Expected vs actual</h4>
        <div class="kv"><div class="hd">field</div><div class="hd">expected</div><div class="hd">actual</div>${fields}</div>
        ${failing.length?`<div class="cfail">Failing checks: ${failing.map(c=>esc(c.name)+(c.critical?' (critical)':'')).join(', ')}</div>`:'<div class="cfail" style="color:var(--pass)">All rubric checks passed</div>'}
        <h4 style="margin-top:16px">Evidence</h4>
        <div>${kb}</div><div style="margin-top:8px">${sig}</div>
      </div>
      <div class="ipane">
        <h4>Execution trace · ${(o.reasoning_trace||[]).length} steps</h4>
        <ul class="trace">${steps}</ul>
        <details><summary>Customer reply &amp; internal note</summary>
          <div class="reply">${esc(o.customer_reply)}</div>
          <div class="reply" style="border-left-color:var(--mut)">${esc(o.internal_note)}</div></details>
        <details><summary>Raw output JSON</summary>
          <pre style="white-space:pre-wrap;font-size:11px;color:#475569">${esc(JSON.stringify(o,null,2))}</pre></details>
      </div>
    </div></div>`;
}
function renderRel(){
  const c=EVAL.cost||{};
  const cell=(k,v,u='')=>`<div class="rc"><div class="k">${k}</div><div class="v">${v==null?'<small>not recorded</small>':esc(v)+ (u?` <small>${u}</small>`:'')}</div></div>`;
  document.getElementById('rel').innerHTML=
    cell('Mean latency',c.mean_seconds,'s/ticket')+
    cell('Prompt tokens',c.prompt_tokens)+
    cell('Completion tokens',c.completion_tokens)+
    cell('Mean LLM calls',c.mean_calls);
  document.getElementById('relnote').textContent=c.note||'';
}
function boot(){
  if(!EVAL||!EVAL.tickets){document.querySelector('.container').innerHTML='<div class="empty">No evaluation.json found. Run <code>python scripts/eval.py --json</code>.</div>';return;}
  renderHeader();renderCards();renderTable();renderRel();
  const firstFail=EVAL.tickets.find(t=>t.status!=='pass')||EVAL.tickets[0];
  if(firstFail){const i=EVAL.tickets.indexOf(firstFail);const row=document.querySelector(`.trow[data-i="${i}"]`);if(row)row.classList.add('active');inspect(firstFail);}
}
boot();
</script></body></html>"""


def main():
    if not EVAL.exists():
        data = {"run_status": "not_run", "tickets": None}
        print("WARNING: outputs/evaluation.json not found. Run: python scripts/eval.py --json")
    else:
        data = json.loads(EVAL.read_text())
    html = HTML.replace("__DATA__", json.dumps(data))
    dest = OUT / "dashboard.html"
    dest.write_text(html, encoding="utf-8")
    status = data.get("run_status", "unknown")
    print(f"Wrote {dest} (run_status={status}).")


if __name__ == "__main__":
    main()
