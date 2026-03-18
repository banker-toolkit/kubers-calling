"""
KUBERS CALLING — rca_console.py
=================================
RCA Console. Port 5002. Run independently:
    python rca_console.py

Shows live interrogation transcripts, incident history,
approve/reject controls, and knowledge base viewer.
Wires into pipeline_monitor.py divergence detection.
"""

import os, sys, json, threading, logging
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request, Response
import requests as _req

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("rca_console")

# ── Import RCA agent ──────────────────────────────────────────────────
try:
    from rca.rca_agent import RCAAgent, make_incident
    _agent = RCAAgent()
except Exception as e:
    log.warning("RCA agent import failed: %s", e)
    _agent = None

# ── Active investigations (running in background threads) ─────────────
_active = {}   # incident_id → {"status": "running"|"done", "incident": dict}
_lock   = threading.Lock()


# ═════════════════════════════════════════════════════════════════════
# API
# ═════════════════════════════════════════════════════════════════════

@app.route("/api/trigger", methods=["POST"])
def api_trigger():
    """Trigger a new investigation. Called by pipeline_monitor on divergence."""
    if not _agent:
        return jsonify({"error": "RCA agent not available"}), 500
    data    = request.get_json(silent=True) or {}
    itype   = data.get("type",    "DIVERGENCE")
    tickers = data.get("tickers", [])
    evidence= data.get("evidence",{})

    if isinstance(tickers, str):
        tickers = [t.strip() for t in tickers.split(",") if t.strip()]

    incident = make_incident(itype, tickers, evidence)
    iid      = incident["id"]

    with _lock:
        _active[iid] = {"status": "running", "incident": incident}

    def run():
        try:
            result = _agent.investigate(incident)
            with _lock:
                _active[iid] = {"status": "done", "incident": result}
        except Exception as e:
            with _lock:
                _active[iid]["status"] = "error"
                _active[iid]["error"]  = str(e)

    threading.Thread(target=run, daemon=True).start()
    log.info("Investigation started: %s", iid)
    return jsonify({"incident_id": iid, "status": "running"})


@app.route("/api/incidents")
def api_incidents():
    """All incidents — active + historical."""
    hist = _agent.get_all_incidents() if _agent else []
    with _lock:
        active_list = list(_active.values())

    # Merge: active overrides history for same ID
    hist_map = {i["id"]: i for i in hist}
    for a in active_list:
        hist_map[a["incident"]["id"]] = a["incident"]

    result = sorted(hist_map.values(), key=lambda x: x.get("timestamp",""), reverse=True)
    return jsonify(result)


@app.route("/api/incident/<incident_id>")
def api_incident(incident_id):
    with _lock:
        if incident_id in _active:
            return jsonify(_active[incident_id])
    inc = _agent.get_incident(incident_id) if _agent else None
    if inc:
        return jsonify({"status": "done", "incident": inc})
    return jsonify({"error": "not found"}), 404


@app.route("/api/approve", methods=["POST"])
def api_approve():
    data = request.get_json(silent=True) or {}
    iid  = data.get("incident_id", "")
    if _agent and _agent.approve_fix(iid):
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "not found"}), 404


@app.route("/api/kb")
def api_kb():
    if not _agent:
        return jsonify({})
    return jsonify(_agent.kb)


@app.route("/api/kb/patterns")
def api_kb_patterns():
    if not _agent:
        return jsonify([])
    return jsonify(_agent.kb.get("patterns", []))


@app.route("/")
def index():
    return DASHBOARD_HTML


# ═════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ═════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kubers · RCA Console</title>
<style>
:root{
  --bg:#f4f3f0;--surf:#fff;--surf2:#f0efe9;
  --bd:#e2dfd8;--bd2:#ccc9bf;
  --t0:#181816;--t1:#484843;--t2:#8a8a82;
  --green:#166534;--gbg:#dcfce7;--gbd:#86efac;
  --red:#991b1b;--rbg:#fee2e2;--rbd:#fca5a5;
  --amber:#92400e;--abg:#fef3c7;--abd:#fcd34d;
  --blue:#1e3a5f;--bbg:#dbeafe;--bbd:#93c5fd;
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  --mono:'SF Mono','Fira Code','Cascadia Code',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t0);font-family:var(--sans);font-size:13px}

.hdr{background:var(--surf);border-bottom:1px solid var(--bd);height:52px;
  padding:0 20px;display:flex;align-items:center;gap:14px;
  position:sticky;top:0;z-index:100}
.logo{font-size:15px;font-weight:700;letter-spacing:.3px}
.logo em{color:var(--t2);font-style:normal}
.sep{width:1px;height:22px;background:var(--bd)}
.stat{display:flex;flex-direction:column;gap:1px}
.slbl{font-size:9px;color:var(--t2);text-transform:uppercase;letter-spacing:.07em}
.sval{font-size:14px;font-weight:600;color:var(--t0)}
.ml{margin-left:auto;display:flex;align-items:center;gap:10px}
.clk{font-family:var(--mono);font-size:11px;color:var(--t2)}
.btn{background:none;border:1px solid var(--bd2);color:var(--t1);
  padding:5px 11px;font-size:11px;border-radius:5px;cursor:pointer}
.btn:hover{border-color:var(--t0);color:var(--t0)}
.btn-trig{background:var(--rbg);border-color:var(--rbd);color:var(--red)}
.btn-trig:hover{background:var(--red);color:#fff;border-color:var(--red)}

.layout{display:grid;grid-template-columns:300px 1fr;height:calc(100vh - 52px);overflow:hidden}

/* left panel */
.left{background:var(--surf);border-right:1px solid var(--bd);
  display:flex;flex-direction:column;overflow:hidden}
.panel-hdr{padding:10px 14px;border-bottom:1px solid var(--bd);
  font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
  color:var(--t2);display:flex;align-items:center}
.panel-hdr .ct{margin-left:auto;background:var(--surf2);border:1px solid var(--bd);
  font-size:9px;padding:1px 6px;border-radius:10px;color:var(--t1)}

.inc-list{flex:1;overflow-y:auto}
.inc-item{padding:10px 14px;border-bottom:1px solid var(--bd);cursor:pointer;
  transition:background .1s}
.inc-item:hover{background:var(--surf2)}
.inc-item.active{background:var(--bbg);border-left:3px solid var(--blue)}
.inc-item.active .inc-id{color:var(--blue)}
.inc-id{font-family:var(--mono);font-size:9px;color:var(--t2);margin-bottom:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.inc-tickers{font-weight:600;font-size:12px;margin-bottom:4px}
.inc-meta{display:flex;align-items:center;gap:6px}
.inc-pill{font-size:9px;font-weight:600;padding:1px 6px;border-radius:10px}
.p-run{background:var(--bbg);color:var(--blue);border:1px solid var(--bbd)}
.p-done{background:var(--gbg);color:var(--green);border:1px solid var(--gbd)}
.p-pat{background:var(--abg);color:var(--amber);border:1px solid var(--abd)}
.p-low{background:var(--rbg);color:var(--red);border:1px solid var(--rbd)}
.p-open{background:var(--surf2);color:var(--t1);border:1px solid var(--bd)}
.inc-time{font-size:10px;color:var(--t2);margin-left:auto}
.spin{display:inline-block;width:8px;height:8px;border:1.5px solid var(--bbd);
  border-top-color:var(--blue);border-radius:50%;animation:spin .7s linear infinite}
@keyframes spin{to{transform:rotate(360deg)}}

/* right panel */
.right{overflow-y:auto;padding:16px 20px;display:flex;flex-direction:column;gap:14px}
.placeholder{display:flex;align-items:center;justify-content:center;
  height:100%;color:var(--t2);font-size:13px}

/* incident detail */
.inc-header{display:flex;flex-direction:column;gap:4px}
.inc-title{font-size:17px;font-weight:700}
.inc-sub{font-size:11px;color:var(--t2);font-family:var(--mono)}

/* confidence bar */
.conf-wrap{display:flex;align-items:center;gap:10px}
.conf-bar{flex:1;height:8px;background:var(--surf2);border-radius:4px;overflow:hidden;
  border:1px solid var(--bd)}
.conf-fill{height:100%;border-radius:4px;transition:width .4s}
.conf-lbl{font-size:12px;font-weight:600;min-width:36px;text-align:right}
.conf-words{font-size:11px;color:var(--t2)}

/* verdict card */
.verdict{border-radius:8px;padding:13px 15px;border:1px solid var(--bd)}
.verdict.v-pat{background:var(--abg);border-color:var(--abd)}
.verdict.v-ok{background:var(--gbg);border-color:var(--gbd)}
.verdict.v-low{background:var(--rbg);border-color:var(--rbd)}
.verdict.v-open{background:var(--surf2)}
.v-lbl{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.07em;
  color:var(--t2);margin-bottom:6px}
.v-pat-badge{display:inline-block;background:var(--amber);color:#fff;
  font-size:10px;font-weight:600;padding:2px 8px;border-radius:3px;margin-bottom:6px}
.v-text{font-size:13px;line-height:1.6;color:var(--t0)}
.v-fix{margin-top:8px;padding:8px 10px;background:rgba(255,255,255,.5);
  border-radius:5px;font-size:11px;color:var(--t1);line-height:1.55;
  border-left:3px solid var(--amber)}

/* transcript */
.transcript-title{font-size:10px;font-weight:700;text-transform:uppercase;
  letter-spacing:.07em;color:var(--t2);display:flex;align-items:center;gap:8px}
.round-card{background:var(--surf);border:1px solid var(--bd);border-radius:8px;
  overflow:hidden}
.round-hdr{padding:7px 12px;border-bottom:1px solid var(--bd);
  display:flex;align-items:center;gap:8px;background:var(--surf2)}
.round-num{font-size:10px;font-weight:700;color:var(--t1)}
.round-conf{font-size:10px;font-weight:600}
.round-body{padding:10px 12px;font-size:12px;line-height:1.65;
  color:var(--t1);white-space:pre-wrap}

/* KB patterns */
.kb-pat{background:var(--surf);border:1px solid var(--bd);border-radius:8px;
  padding:12px 14px;display:flex;flex-direction:column;gap:4px}
.kb-pat-id{font-family:var(--mono);font-size:9px;color:var(--t2)}
.kb-pat-title{font-weight:600;font-size:13px}
.kb-pat-meta{display:flex;gap:10px;font-size:10px;color:var(--t2);flex-wrap:wrap}
.kb-pat-cause{font-size:11px;color:var(--t1);line-height:1.5;margin-top:4px;
  padding-top:6px;border-top:1px solid var(--bd)}
.fixed{color:var(--green)}.pending{color:var(--amber)}

/* approve buttons */
.actions{display:flex;gap:8px}
.btn-approve{background:var(--gbg);border:1px solid var(--gbd);color:var(--green);
  padding:6px 14px;border-radius:5px;font-size:12px;font-weight:600;cursor:pointer}
.btn-approve.on{background:var(--green);color:#fff;border-color:var(--green)}
.btn-approve:hover:not(.on){background:var(--green);color:#fff;border-color:var(--green)}
.btn-reject{background:var(--rbg);border:1px solid var(--rbd);color:var(--red);
  padding:6px 14px;border-radius:5px;font-size:12px;cursor:pointer}
.btn-reject:hover{background:var(--red);color:#fff}

/* tabs */
.tabs{display:flex;gap:0;border-bottom:1px solid var(--bd);margin-bottom:0}
.tab{padding:7px 14px;font-size:12px;cursor:pointer;border-bottom:2px solid transparent;
  color:var(--t2)}
.tab.on{color:var(--t0);font-weight:600;border-bottom-color:var(--t0)}
.tab-pane{display:none}
.tab-pane.on{display:flex;flex-direction:column;gap:10px;padding-top:14px}
</style>
</head>
<body>

<div class="hdr">
  <div class="logo">Kubers <em>RCA Console</em></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Incidents</div><div class="sval" id="hInc">—</div></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Patterns</div><div class="sval" id="hPat">—</div></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Active</div><div class="sval" id="hAct">—</div></div>
  <div class="ml">
    <span class="clk" id="clk">--:--:--</span>
    <button class="btn btn-trig" onclick="triggerManual()">+ Trigger investigation</button>
    <button class="btn" onclick="loadAll()">↻</button>
  </div>
</div>

<div class="layout">
  <!-- left: incident list -->
  <div class="left">
    <div class="panel-hdr">
      Investigations
      <span class="ct" id="incCount">0</span>
    </div>
    <div class="inc-list" id="incList">
      <div style="padding:20px;color:var(--t2);font-size:11px;text-align:center">Loading…</div>
    </div>
  </div>

  <!-- right: detail panel -->
  <div class="right" id="detail">
    <div class="placeholder">Select an investigation to view details</div>
  </div>
</div>

<script>
let _incidents=[], _selected=null, _approved=new Set(), _pollTimer=null;

function esc(s){
  return String(s||'')
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function confColor(c){
  if(c==null) return 'var(--t2)';
  return c>=.9?'var(--green)':c>=.6?'var(--amber)':'var(--red)';
}
function confWords(c){
  if(c==null) return '';
  if(c>=.9) return 'High confidence';
  if(c>=.6) return 'Moderate confidence';
  return 'Low confidence';
}

// ── INCIDENT LIST ─────────────────────────────────────────────
function loadAll(){
  fetch('/api/incidents').then(r=>r.json()).then(list=>{
    _incidents=list;
    document.getElementById('incCount').textContent=list.length;
    const active=list.filter(i=>i.status==='investigating'||i.status==='running').length;
    const pats=list.filter(i=>i.status==='pattern_matched').length;
    document.getElementById('hInc').textContent=list.length;
    document.getElementById('hAct').textContent=active;

    fetch('/api/kb/patterns').then(r=>r.json()).then(p=>{
      document.getElementById('hPat').textContent=p.length;
    }).catch(()=>{});

    const el=document.getElementById('incList');
    if(!list.length){
      el.innerHTML='<div style="padding:20px;color:var(--t2);font-size:11px;text-align:center">No investigations yet.</div>';
      return;
    }

    el.innerHTML=list.map(inc=>{
      const s=inc.status||'open';
      const running=s==='investigating'||s==='running';
      const pillCls=
        running?'p-run':
        s==='pattern_matched'?'p-pat':
        s==='complete'?'p-done':
        s==='low_confidence'?'p-low':'p-open';
      const pillTxt=
        running?'Running':
        s==='pattern_matched'?'Pattern match':
        s==='complete'?'Complete':
        s==='low_confidence'?'Low conf':'Open';
      const tickers=(inc.tickers||[]).join(', ')||inc.type||'?';
      const ts=(inc.timestamp||'').slice(11,16);
      const conf=inc.confidence!=null?Math.round(inc.confidence*100)+'%':'';
      const isSel=_selected===inc.id;
      return `<div class="inc-item${isSel?' active':''}" onclick="selectInc('${inc.id}')">
        <div class="inc-id">${inc.id.slice(-24)}</div>
        <div class="inc-tickers">${esc(tickers)}</div>
        <div class="inc-meta">
          <span class="inc-pill ${pillCls}">${running?`<span class="spin"></span> `:''} ${pillTxt}</span>
          <span class="inc-time">${ts}${conf?' · '+conf:''}</span>
        </div>
      </div>`;
    }).join('');

    if(_selected) selectInc(_selected, true);
  }).catch(()=>{});
}

// ── INCIDENT DETAIL ───────────────────────────────────────────
function selectInc(id, silent=false){
  _selected=id;
  if(!silent) loadAll();

  const inc=_incidents.find(i=>i.id===id);
  if(!inc) return;

  const det=document.getElementById('detail');
  const running=inc.status==='investigating'||inc.status==='running';

  if(running){
    det.innerHTML=`
      <div class="inc-header">
        <div class="inc-title">${esc((inc.tickers||[]).join(', ')||inc.type)}</div>
        <div class="inc-sub">${inc.id}</div>
      </div>
      <div style="display:flex;align-items:center;gap:10px;color:var(--blue)">
        <span class="spin" style="width:14px;height:14px;border-width:2px"></span>
        <span style="font-size:13px">Investigating — Claude is analysing evidence…</span>
      </div>`;
    clearTimeout(_pollTimer);
    _pollTimer=setTimeout(()=>selectInc(id), 2000);
    return;
  }

  const conf=inc.confidence||0;
  const confPct=Math.round(conf*100);
  const vClass=inc.status==='pattern_matched'?'v-pat':inc.status==='low_confidence'?'v-low':conf>=.6?'v-ok':'v-open';
  const rounds=inc.rounds||[];
  const approved=inc.approved||_approved.has(id);

  // confidence bar color
  const cfill=confColor(conf);

  det.innerHTML=`
    <div class="inc-header">
      <div class="inc-title">${esc((inc.tickers||[]).join(', ')||inc.type)} <span style="font-size:13px;color:var(--t2);font-weight:400">— ${esc(inc.type||'')}</span></div>
      <div class="inc-sub">${inc.id} · ${(inc.timestamp||'').slice(0,19).replace('T',' ')}</div>
    </div>

    <div class="conf-wrap">
      <div class="conf-bar"><div class="conf-fill" style="width:${confPct}%;background:${cfill}"></div></div>
      <div class="conf-lbl" style="color:${cfill}">${confPct}%</div>
      <div class="conf-words">${confWords(conf)}</div>
    </div>

    <div class="verdict ${vClass}">
      <div class="v-lbl">Root cause verdict</div>
      ${inc.pattern_match?`<div class="v-pat-badge">⚡ Pattern match: ${esc(inc.pattern_match)}</div><br>`:''}
      <div class="v-text">${esc(inc.rca||'No conclusion reached.')}</div>
      ${inc.proposed_fix?`<div class="v-fix"><strong>Proposed fix:</strong><br>${esc(inc.proposed_fix)}</div>`:''}
    </div>

    <div class="actions">
      <button class="btn-approve${approved?' on':''}" id="btnApprove" onclick="doApprove()">
        ${approved?'✓ Approved':'Approve fix'}
      </button>
      <button class="btn-reject" onclick="doReject()">Flag for review</button>
    </div>

    <div class="tabs">
      <div class="tab on" onclick="showTab('transcript',this)">
        Interrogation transcript ${rounds.length?`(${rounds.length} rounds)`:''}
      </div>
      <div class="tab" onclick="showTab('kb',this)">Knowledge base</div>
    </div>

    <div class="tab-pane on" id="tab-transcript">
      ${rounds.length
        ? rounds.map(r=>{
            const rc=confColor(r.confidence);
            const rp=r.confidence!=null?Math.round(r.confidence*100):null;
            return `<div class="round-card">
              <div class="round-hdr">
                <span class="round-num">Round ${r.round}</span>
                ${rp!=null?`<span class="round-conf" style="color:${rc}">${rp}% confidence</span>`:''}
              </div>
              <div class="round-body">${esc(r.response||'')}</div>
            </div>`;
          }).join('')
        : inc.status==='pattern_matched'
          ? `<div style="font-size:12px;color:var(--t2);padding:10px">
              Pattern matched from knowledge base — no interrogation needed.<br>
              Matched: <b>${esc(inc.pattern_match||'')}</b>
             </div>`
          : `<div style="font-size:12px;color:var(--t2);padding:10px">No transcript available.</div>`
      }
    </div>

    <div class="tab-pane" id="tab-kb"></div>
  `;

  // Load KB into the tab
  fetch('/api/kb/patterns').then(r=>r.json()).then(pats=>{
    const el=document.getElementById('tab-kb');
    if(!el) return;
    if(!pats.length){
      el.innerHTML='<div style="font-size:12px;color:var(--t2)">No patterns in knowledge base yet.</div>';
      return;
    }
    el.innerHTML=pats.map(p=>`
      <div class="kb-pat">
        <div class="kb-pat-id">${p.id}</div>
        <div class="kb-pat-title">${esc(p.title||'')}</div>
        <div class="kb-pat-meta">
          <span>Category: ${p.category||'—'}</span>
          <span>Confidence: ${Math.round((p.confidence||0)*100)}%</span>
          <span>Seen: ${p.recurrence_count||1}×</span>
          <span>Confirmed: ${p.confirmed_date||'—'}</span>
          <span class="${p.fix_date?'fixed':'pending'}">
            ${p.fix_date?'✓ Fixed '+p.fix_date:'⏳ Pending fix'}
          </span>
        </div>
        <div class="kb-pat-cause">${esc(p.root_cause||'')}</div>
      </div>`).join('');
  }).catch(()=>{});
}

function showTab(name, el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  document.querySelectorAll('.tab-pane').forEach(p=>p.classList.remove('on'));
  el.classList.add('on');
  const pane=document.getElementById('tab-'+name);
  if(pane) pane.classList.add('on');
}

// ── APPROVE / REJECT ──────────────────────────────────────────
function doApprove(){
  if(!_selected) return;
  fetch('/api/approve',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({incident_id:_selected})
  }).then(r=>r.json()).then(d=>{
    if(d.ok){
      _approved.add(_selected);
      const btn=document.getElementById('btnApprove');
      if(btn){btn.classList.add('on');btn.textContent='✓ Approved';}
    }
  });
}

function doReject(){
  alert('Flagged for manual review. Check rca/incident_log.json.');
}

// ── MANUAL TRIGGER ────────────────────────────────────────────
function triggerManual(){
  const tickers=prompt('Ticker(s) to investigate (comma-separated):','');
  if(!tickers) return;
  fetch('/api/trigger',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type:'MANUAL',tickers:tickers,evidence:{}})
  }).then(r=>r.json()).then(d=>{
    _selected=d.incident_id;
    loadAll();
  });
}

// ── BOOT ─────────────────────────────────────────────────────
function tickClk(){document.getElementById('clk').textContent=new Date().toTimeString().slice(0,8);}
loadAll(); tickClk();
setInterval(loadAll, 3000); setInterval(tickClk, 1000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    log.info("RCA Console starting on http://localhost:5002")
    app.run(host="0.0.0.0", port=5002, debug=False, use_reloader=False)