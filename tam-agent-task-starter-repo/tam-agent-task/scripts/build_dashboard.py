"""Build a self-contained triage dashboard (single HTML file, no server, no deps).

Reads outputs/*.json and embeds them into outputs/dashboard.html so the page
opens with a double-click and works fully offline (browsers cannot fetch local
files over file://, so we inline the data). Run after run.py:

    python scripts/build_dashboard.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "outputs"
REVIEW = [l.strip() for l in (ROOT / "data" / "review_set.txt").read_text().splitlines() if l.strip()]


def load():
    data = []
    for tid in REVIEW:
        p = OUT / f"{tid}.json"
        if p.exists():
            data.append(json.loads(p.read_text()))
    return data


HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zycus TAM — Agentic Ticket Triage</title>
<style>
:root{--z:#e4002b;--ink:#1a1f2b;--mut:#6b7280;--line:#e5e7eb;--bg:#f6f7f9;--card:#fff}
*{box-sizing:border-box}body{margin:0;font:14px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,Arial;color:var(--ink);background:var(--bg)}
header{background:var(--ink);color:#fff;padding:14px 22px;display:flex;align-items:center;gap:14px}
.logo{font-weight:800;font-size:20px;letter-spacing:.5px}.logo b{color:var(--z)}
header .sub{color:#aeb4c0;font-size:12px}
.wrap{display:flex;min-height:calc(100vh - 52px)}
.list{width:300px;border-right:1px solid var(--line);background:var(--card);overflow:auto}
.row{padding:11px 16px;border-bottom:1px solid var(--line);cursor:pointer}
.row:hover{background:#fafafa}.row.active{background:#fff5f6;border-left:3px solid var(--z)}
.row .tid{font-weight:700}.row .meta{color:var(--mut);font-size:12px;margin-top:2px}
.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700}
.p1{background:#fde8e8;color:#b91c1c}.p2{background:#fff4e5;color:#b45309}
.p3{background:#eef2ff;color:#3730a3}.p4{background:#ecfdf5;color:#047857}
.main{flex:1;padding:22px;overflow:auto}
.hd{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.hd h2{margin:0;font-size:18px}
.badges{margin:10px 0 0;display:flex;gap:8px;flex-wrap:wrap}
.badge{background:#f1f3f5;border:1px solid var(--line);border-radius:6px;padding:3px 9px;font-size:12px}
.badge.on{background:var(--ink);color:#fff;border-color:var(--ink)}
.badge.sec{background:var(--z);color:#fff;border-color:var(--z)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px}
.card h3{margin:0 0 8px;font-size:12px;text-transform:uppercase;letter-spacing:.6px;color:var(--mut)}
.kb{display:inline-block;background:#eef6ff;color:#1d4ed8;border:1px solid #cfe0fb;border-radius:6px;padding:2px 8px;margin:2px 4px 2px 0;font-size:12px;font-weight:600}
.sig{margin:3px 0;padding-left:16px;position:relative}.sig:before{content:"▹";position:absolute;left:0;color:var(--z)}
.trace{list-style:none;margin:0;padding:0}
.step{position:relative;padding:0 0 14px 22px;border-left:2px solid var(--line);margin-left:6px}
.step:last-child{border-left-color:transparent}
.step:before{content:"";position:absolute;left:-7px;top:2px;width:12px;height:12px;border-radius:50%;background:#fff;border:2px solid var(--z)}
.step .act{font-weight:700}.step .act.pol{color:var(--z)}
.step .obs{color:var(--mut);font-size:12.5px;white-space:pre-wrap}
.step .src{font-size:11px;background:#fff5f6;color:var(--z);border:1px solid #f3c7cd;border-radius:4px;padding:0 5px;margin-left:6px}
.reply{white-space:pre-wrap;background:#fbfbfc;border:1px dashed var(--line);border-radius:8px;padding:10px}
.conf{font-weight:700}
.foot{color:var(--mut);font-size:11px;padding:8px 22px}
</style></head>
<body>
<header><div class="logo"><b>Zycus</b> TAM</div>
<div class="sub">Agentic Ticket Triage — review set · click a ticket to see the agent's reasoning</div></header>
<div class="wrap">
  <div class="list" id="list"></div>
  <div class="main" id="main"></div>
</div>
<div class="foot" id="foot"></div>
<script>
const DATA = __DATA__;
const pcls = p => ({P1:'p1',P2:'p2',P3:'p3',P4:'p4'}[p]||'p3');
const esc = s => (s==null?'':String(s)).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
function badges(d){
  const b=[];
  if(d.category==='security') b.push('<span class="badge sec">SECURITY</span>');
  b.push(`<span class="badge">${esc(d.category)}</span>`);
  b.push(`<span class="badge">→ ${esc(d.route_to)}</span>`);
  if(d.escalate) b.push('<span class="badge on">escalate</span>');
  if(d.notify_tam) b.push('<span class="badge on">notify TAM</span>');
  if(d.needs_clarification) b.push('<span class="badge">needs info</span>');
  if(d.duplicate_of) b.push(`<span class="badge">dup of ${esc(d.duplicate_of)}</span>`);
  return b.join(' ');
}
function render(d){
  const kb=(d.kb_references||[]).map(k=>`<span class="kb">${esc(k)}</span>`).join('')||'<span style="color:#9ca3af">none cited</span>';
  const sig=(d.account_signals||[]).map(s=>`<div class="sig">${esc(s)}</div>`).join('')||'<span style="color:#9ca3af">none</span>';
  const steps=(d.reasoning_trace||[]).map(s=>{
    const pol=('policy_source' in s);
    return `<li class="step"><div class="act ${pol?'pol':''}">${s.step}. ${esc(s.action)}${pol?`<span class="src">${esc(s.policy_source)}</span>`:''}</div><div class="obs">${esc(s.observation).slice(0,400)}</div></li>`;
  }).join('');
  document.getElementById('main').innerHTML=`
    <div class="hd"><span class="pill ${pcls(d.priority)}">${esc(d.priority)}</span><h2>${esc(d.ticket_id)}</h2>
      <span style="color:var(--mut)">· account ${esc(d.account_id||'unresolved')} · <span class="conf">conf ${d.confidence}</span></span></div>
    <div class="badges">${badges(d)}</div>
    <div class="grid">
      <div class="card"><h3>Agent reasoning trace (${(d.reasoning_trace||[]).length} steps)</h3><ul class="trace">${steps}</ul></div>
      <div>
        <div class="card"><h3>KB references</h3>${kb}</div>
        <div class="card" style="margin-top:16px"><h3>Account signals</h3>${sig}</div>
        <div class="card" style="margin-top:16px"><h3>Customer reply</h3><div class="reply">${esc(d.customer_reply)}</div></div>
        <div class="card" style="margin-top:16px"><h3>Internal note</h3><div class="reply">${esc(d.internal_note)}</div></div>
      </div>
    </div>`;
}
function list(){
  document.getElementById('list').innerHTML=DATA.map((d,i)=>`
    <div class="row" data-i="${i}"><div class="tid"><span class="pill ${pcls(d.priority)}">${esc(d.priority)}</span> ${esc(d.ticket_id)}</div>
    <div class="meta">${esc(d.category)} → ${esc(d.route_to)}${d.category==='security'?' · 🔒':''}</div></div>`).join('');
  document.querySelectorAll('.row').forEach(r=>r.onclick=()=>{
    document.querySelectorAll('.row').forEach(x=>x.classList.remove('active'));
    r.classList.add('active'); render(DATA[+r.dataset.i]);
  });
}
const sec=DATA.filter(d=>d.category==='security').length;
document.getElementById('foot').textContent=`${DATA.length} review tickets · ${sec} security-classified · reasoning traces include deterministic policy enforcements with their KB source`;
list(); if(DATA.length){document.querySelector('.row').classList.add('active'); render(DATA[0]);}
</script></body></html>"""


def main():
    data = load()
    html = HTML.replace("__DATA__", json.dumps(data))
    dest = OUT / "dashboard.html"
    dest.write_text(html, encoding="utf-8")
    print(f"Wrote {dest} ({len(data)} tickets). Open it in a browser.")


if __name__ == "__main__":
    main()
