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
<title>RCA Console</title>
<link href="https://fonts.googleapis.com/css2?family=Courier+Prime:wght@400;700&family=Oswald:wght@300;400;600;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg:       #080a0c;
  --bg1:      #0d1117;
  --bg2:      #141b24;
  --bg3:      #1c2535;
  --border:   #1e2d3d;
  --amber:    #f0a500;
  --amber2:   #c47f00;
  --amber-dim:#3d2a00;
  --green:    #00d97e;
  --green-dim:#003d22;
  --red:      #ff3d5a;
  --red-dim:  #3d0010;
  --blue:     #4db8ff;
  --blue-dim: #002a3d;
  --text0:    #cdd9e5;
  --text1:    #7a95ad;
  --text2:    #3d5066;
  --mono:     'Courier Prime', monospace;
  --head:     'Oswald', sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{background:var(--bg);color:var(--text0);font-family:var(--mono);font-size:12px;height:100%;overflow:hidden}

/* grain overlay */
body::before{
  content:'';position:fixed;inset:0;
  background-image:url("data:image/svg+xml,%3Csvg viewBox='0 0 256 256' xmlns='http://www.w3.org/2000/svg'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='4' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.04'/%3E%3C/svg%3E");
  pointer-events:none;z-index:9998;opacity:.6;
}

/* ── LAYOUT ── */
.root{display:grid;grid-template-rows:52px 1fr;height:100vh}
.body{display:grid;grid-template-columns:320px 1fr;overflow:hidden}

/* ── HEADER ── */
.header{
  background:var(--bg1);
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;padding:0 20px;gap:16px;
  position:relative;
}
.logo{
  font-family:var(--head);font-size:20px;font-weight:700;
  letter-spacing:4px;color:var(--amber);
  text-transform:uppercase;
}
.logo span{color:var(--text2);font-weight:300}
.hdiv{width:1px;height:28px;background:var(--border)}
.hstat{display:flex;flex-direction:column;gap:1px}
.hstat .lbl{font-size:7px;letter-spacing:2px;color:var(--text2);text-transform:uppercase}
.hstat .val{font-size:13px;font-weight:700;color:var(--text0);font-family:var(--head)}
.trigger-btn{
  margin-left:auto;
  background:var(--amber-dim);border:1px solid var(--amber2);
  color:var(--amber);font-family:var(--head);font-size:11px;font-weight:600;
  letter-spacing:2px;padding:7px 16px;cursor:pointer;
  text-transform:uppercase;transition:all .2s;
}
.trigger-btn:hover{background:var(--amber);color:var(--bg)}
.clock{font-size:12px;color:var(--text2);letter-spacing:1px;min-width:64px}

/* ── LEFT PANEL — incident list ── */
.left-panel{
  background:var(--bg1);
  border-right:1px solid var(--border);
  display:flex;flex-direction:column;
  overflow:hidden;
}
.panel-hdr{
  padding:12px 16px;
  border-bottom:1px solid var(--border);
  font-family:var(--head);font-size:11px;font-weight:600;
  letter-spacing:3px;color:var(--text1);
  text-transform:uppercase;
  display:flex;align-items:center;gap:8px;
}
.panel-hdr .count{
  margin-left:auto;
  background:var(--bg3);border:1px solid var(--border);
  color:var(--text1);font-size:10px;padding:1px 6px;border-radius:1px;
}
.inc-list{overflow-y:auto;flex:1}
.inc-item{
  padding:10px 16px;
  border-bottom:1px solid var(--border);
  cursor:pointer;transition:background .15s;
  position:relative;
}
.inc-item:hover{background:var(--bg2)}
.inc-item.active{background:var(--bg3);border-left:2px solid var(--amber)}
.inc-item.running{border-left:2px solid var(--blue)}
.inc-item.pattern{border-left:2px solid var(--green)}
.inc-item.low_confidence{border-left:2px solid var(--red)}
.inc-id{font-size:9px;color:var(--text2);letter-spacing:.5px;margin-bottom:3px}
.inc-tickers{font-size:12px;font-weight:700;color:var(--text0);margin-bottom:2px}
.inc-meta{display:flex;align-items:center;gap:6px}
.inc-status{font-size:8px;letter-spacing:1px;padding:2px 5px;border-radius:1px;text-transform:uppercase}
.s-running{background:var(--blue-dim);color:var(--blue);border:1px solid var(--blue)}
.s-pattern{background:var(--green-dim);color:var(--green);border:1px solid var(--green)}
.s-complete{background:var(--amber-dim);color:var(--amber);border:1px solid var(--amber2)}
.s-low{background:var(--red-dim);color:var(--red);border:1px solid var(--red)}
.s-open{background:var(--bg3);color:var(--text1);border:1px solid var(--border)}
.inc-conf{margin-left:auto;font-size:10px;font-weight:700}
.inc-time{font-size:9px;color:var(--text2)}

/* spinning indicator */
@keyframes spin{to{transform:rotate(360deg)}}
.spinner{
  display:inline-block;width:8px;height:8px;
  border:1px solid var(--blue);border-top-color:transparent;
  border-radius:50%;animation:spin .8s linear infinite;
}

/* ── RIGHT PANEL — detail ── */
.right-panel{
  display:flex;flex-direction:column;
  overflow:hidden;background:var(--bg);
}
.detail-hdr{
  padding:14px 20px;
  border-bottom:1px solid var(--border);
  background:var(--bg1);
  display:flex;align-items:flex-start;gap:12px;
}
.detail-title{
  font-family:var(--head);font-size:16px;font-weight:600;
  letter-spacing:1px;color:var(--text0);
}
.detail-sub{font-size:10px;color:var(--text2);margin-top:3px;letter-spacing:.5px}
.detail-actions{margin-left:auto;display:flex;gap:8px;align-items:center}

/* approve / reject switches */
.switch-wrap{display:flex;flex-direction:column;align-items:center;gap:3px}
.switch-lbl{font-size:7px;letter-spacing:2px;color:var(--text2);text-transform:uppercase}
.switch{
  width:48px;height:24px;border-radius:2px;cursor:pointer;
  display:flex;align-items:center;justify-content:center;
  font-family:var(--head);font-size:9px;font-weight:700;
  letter-spacing:1px;border:1px solid;transition:all .2s;
  text-transform:uppercase;
}
.sw-approve{
  background:var(--green-dim);border-color:var(--green);color:var(--green);
}
.sw-approve:hover,.sw-approve.on{background:var(--green);color:var(--bg)}
.sw-reject{
  background:var(--red-dim);border-color:var(--red);color:var(--red);
}
.sw-reject:hover,.sw-reject.on{background:var(--red);color:var(--bg)}

/* confidence gauge */
.conf-gauge{
  display:flex;flex-direction:column;align-items:center;gap:3px;
  min-width:60px;
}
.gauge-bar{
  width:48px;height:6px;background:var(--bg3);border-radius:1px;
  overflow:hidden;border:1px solid var(--border);
}
.gauge-fill{
  height:100%;border-radius:1px;transition:width .8s ease;
}
.gauge-val{font-size:11px;font-weight:700;font-family:var(--head)}

/* ── DETAIL BODY ── */
.detail-body{flex:1;overflow-y:auto;padding:20px}
.empty-detail{
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  height:100%;color:var(--text2);gap:12px;
}
.empty-icon{font-size:32px;opacity:.3}
.empty-txt{font-family:var(--head);font-size:13px;letter-spacing:3px;text-transform:uppercase}

/* ── VERDICT BOX ── */
.verdict{
  background:var(--bg2);border:1px solid var(--border);
  padding:16px;margin-bottom:16px;border-radius:2px;
  position:relative;overflow:hidden;
}
.verdict::before{
  content:'';position:absolute;left:0;top:0;bottom:0;
  width:3px;background:var(--amber);
}
.verdict.pattern::before{background:var(--green)}
.verdict.low_confidence::before{background:var(--red)}
.verdict-lbl{
  font-family:var(--head);font-size:9px;letter-spacing:3px;
  color:var(--text2);text-transform:uppercase;margin-bottom:8px;
}
.verdict-text{color:var(--text0);line-height:1.7;font-size:12px}
.verdict-fix{
  margin-top:12px;padding:10px;
  background:var(--bg);border:1px solid var(--amber-dim);
  color:var(--amber);font-size:11px;line-height:1.6;
  white-space:pre-wrap;
}
.pattern-badge{
  display:inline-block;
  background:var(--green-dim);border:1px solid var(--green);
  color:var(--green);font-size:9px;letter-spacing:1px;
  padding:2px 8px;margin-bottom:8px;
  font-family:var(--head);text-transform:uppercase;
}

/* ── TRANSCRIPT ── */
.transcript-hdr{
  font-family:var(--head);font-size:9px;letter-spacing:3px;
  color:var(--text2);text-transform:uppercase;
  margin-bottom:12px;padding-bottom:6px;
  border-bottom:1px solid var(--border);
  display:flex;align-items:center;gap:8px;
}
.round-block{margin-bottom:20px}
.round-num{
  font-family:var(--head);font-size:11px;font-weight:700;
  color:var(--blue);letter-spacing:2px;margin-bottom:8px;
}
.msg{
  padding:10px 14px;margin-bottom:6px;
  border-radius:2px;line-height:1.7;font-size:11px;
  position:relative;
}
.msg-agent{
  background:var(--bg2);border:1px solid var(--border);
  border-left:3px solid var(--amber);
  color:var(--text1);
}
.msg-agent::before{
  content:'AGENT';
  display:block;font-family:var(--head);font-size:8px;
  letter-spacing:2px;color:var(--amber);margin-bottom:4px;
}
.msg-claude{
  background:var(--bg3);border:1px solid var(--border);
  border-left:3px solid var(--blue);
  color:var(--text0);margin-left:20px;
}
.msg-claude::before{
  content:'CLAUDE';
  display:block;font-family:var(--head);font-size:8px;
  letter-spacing:2px;color:var(--blue);margin-bottom:4px;
}
.msg-conf{
  float:right;font-family:var(--head);font-weight:700;
  font-size:13px;margin-left:8px;
}

/* ── KB PANEL ── */
.kb-section{margin-bottom:20px}
.kb-hdr{
  font-family:var(--head);font-size:10px;letter-spacing:3px;
  color:var(--amber);text-transform:uppercase;
  padding:8px 12px;background:var(--amber-dim);
  border:1px solid var(--amber2);margin-bottom:8px;
}
.kb-pattern{
  background:var(--bg2);border:1px solid var(--border);
  padding:12px;margin-bottom:6px;border-radius:2px;
}
.kb-pat-id{
  font-family:var(--head);font-size:11px;font-weight:700;
  color:var(--amber);letter-spacing:1px;margin-bottom:4px;
}
.kb-pat-title{font-size:11px;color:var(--text0);margin-bottom:6px}
.kb-meta{display:flex;gap:12px;font-size:9px;color:var(--text2)}
.kb-cause{
  margin-top:8px;font-size:10px;color:var(--text1);
  line-height:1.6;border-top:1px solid var(--border);padding-top:8px;
}

/* ── TABS ── */
.tabs{display:flex;border-bottom:1px solid var(--border);background:var(--bg1)}
.tab{
  padding:10px 20px;font-family:var(--head);font-size:10px;
  letter-spacing:2px;color:var(--text2);cursor:pointer;
  text-transform:uppercase;border-bottom:2px solid transparent;
  transition:all .15s;
}
.tab:hover{color:var(--text0)}
.tab.active{color:var(--amber);border-bottom-color:var(--amber)}

/* ── MANUAL TRIGGER MODAL ── */
.modal-bg{
  display:none;position:fixed;inset:0;
  background:rgba(0,0,0,.7);z-index:500;
  align-items:center;justify-content:center;
}
.modal-bg.open{display:flex}
.modal{
  background:var(--bg1);border:1px solid var(--amber2);
  padding:24px;width:400px;
}
.modal-title{
  font-family:var(--head);font-size:16px;color:var(--amber);
  letter-spacing:2px;text-transform:uppercase;margin-bottom:16px;
}
.field{margin-bottom:12px}
.field label{display:block;font-size:9px;letter-spacing:2px;color:var(--text2);
  text-transform:uppercase;margin-bottom:4px;}
.field input,.field select{
  width:100%;background:var(--bg2);border:1px solid var(--border);
  color:var(--text0);font-family:var(--mono);font-size:12px;
  padding:7px 10px;outline:none;
}
.field input:focus,.field select:focus{border-color:var(--amber)}
.modal-btns{display:flex;gap:8px;margin-top:16px}
.mbtn{
  flex:1;padding:9px;font-family:var(--head);font-size:10px;
  font-weight:700;letter-spacing:2px;text-transform:uppercase;
  cursor:pointer;border:1px solid;transition:all .2s;
}
.mbtn-go{background:var(--amber-dim);border-color:var(--amber2);color:var(--amber)}
.mbtn-go:hover{background:var(--amber);color:var(--bg)}
.mbtn-cancel{background:var(--bg3);border-color:var(--border);color:var(--text2)}
.mbtn-cancel:hover{color:var(--text0)}

@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.live-dot{
  width:7px;height:7px;border-radius:50%;background:var(--green);
  animation:pulse 2s ease infinite;display:inline-block;
}
</style>
</head>
<body>
<div class="root">

  <!-- HEADER -->
  <div class="header">
    <div class="logo">RCA<span>///</span>CONSOLE</div>
    <div class="hdiv"></div>
    <div class="hstat">
      <div class="lbl">INCIDENTS TODAY</div>
      <div class="val" id="hTotal">0</div>
    </div>
    <div class="hstat">
      <div class="lbl">PATTERN MATCHES</div>
      <div class="val" id="hPattern">0</div>
    </div>
    <div class="hstat">
      <div class="lbl">KB PATTERNS</div>
      <div class="val" id="hKb">0</div>
    </div>
    <div class="hstat">
      <div class="lbl">ACTIVE</div>
      <div class="val" id="hActive">0</div>
    </div>
    <button class="trigger-btn" onclick="openModal()">+ TRIGGER INVESTIGATION</button>
    <div class="clock" id="clock">--:--:--</div>
  </div>

  <!-- BODY -->
  <div class="body">

    <!-- LEFT: incident list -->
    <div class="left-panel">
      <div class="panel-hdr">
        INCIDENTS
        <span class="live-dot"></span>
        <span class="count" id="incCount">0</span>
      </div>
      <div class="inc-list" id="incList">
        <div style="padding:20px;color:var(--text2);font-size:10px;text-align:center">
          No incidents yet.<br>Trigger one or wait for divergence.
        </div>
      </div>
    </div>

    <!-- RIGHT: detail -->
    <div class="right-panel">
      <div class="tabs">
        <div class="tab active" onclick="showTab('investigation',this)">INVESTIGATION</div>
        <div class="tab" onclick="showTab('kb',this)">KNOWLEDGE BASE</div>
      </div>

      <!-- Investigation tab -->
      <div id="tab-investigation" style="display:flex;flex-direction:column;flex:1;overflow:hidden">
        <div class="detail-hdr" id="detailHdr" style="display:none">
          <div>
            <div class="detail-title" id="detailTitle">—</div>
            <div class="detail-sub" id="detailSub">—</div>
          </div>
          <div class="detail-actions">
            <div class="conf-gauge">
              <div class="gauge-bar"><div class="gauge-fill" id="gaugeFill" style="width:0%"></div></div>
              <div class="gauge-val" id="gaugeVal" style="color:var(--text2)">—%</div>
              <div class="switch-lbl">CONFIDENCE</div>
            </div>
            <div class="switch-wrap">
              <div class="switch sw-approve" id="swApprove" onclick="doApprove()">APPROVE</div>
              <div class="switch-lbl">FIX</div>
            </div>
            <div class="switch-wrap">
              <div class="switch sw-reject" onclick="doReject()">REJECT</div>
              <div class="switch-lbl">ESCALATE</div>
            </div>
          </div>
        </div>
        <div class="detail-body" id="detailBody">
          <div class="empty-detail">
            <div class="empty-icon">⟁</div>
            <div class="empty-txt">Select an incident</div>
          </div>
        </div>
      </div>

      <!-- KB tab -->
      <div id="tab-kb" style="display:none;flex:1;overflow-y:auto;padding:20px">
        <div id="kbContent">Loading knowledge base...</div>
      </div>
    </div>
  </div>
</div>

<!-- MODAL -->
<div class="modal-bg" id="modal">
  <div class="modal">
    <div class="modal-title">Trigger Investigation</div>
    <div class="field">
      <label>Incident Type</label>
      <select id="mType">
        <option value="DIVERGENCE">DIVERGENCE — broker vs DB mismatch</option>
        <option value="INSTANT_SL">INSTANT_SL — SL fires at entry price</option>
        <option value="REJECTION_STORM">REJECTION_STORM — mass order rejections</option>
        <option value="MANUAL">MANUAL — custom investigation</option>
      </select>
    </div>
    <div class="field">
      <label>Tickers (comma-separated)</label>
      <input type="text" id="mTickers" placeholder="e.g. BANDHANBNK,IRB">
    </div>
    <div class="modal-btns">
      <button class="mbtn mbtn-go" onclick="submitTrigger()">INVESTIGATE</button>
      <button class="mbtn mbtn-cancel" onclick="closeModal()">CANCEL</button>
    </div>
  </div>
</div>

<script>
let _selected   = null;
let _incidents  = [];
let _approved   = new Set();
let _pollTimer  = null;

// ── TABS ──────────────────────────────────────────────────
function showTab(name, el){
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('tab-investigation').style.display = name==='investigation'?'flex':'none';
  document.getElementById('tab-kb').style.display            = name==='kb'?'block':'none';
  if(name==='kb') loadKb();
}

// ── MODAL ─────────────────────────────────────────────────
function openModal(){  document.getElementById('modal').classList.add('open'); }
function closeModal(){ document.getElementById('modal').classList.remove('open'); }

function submitTrigger(){
  const type    = document.getElementById('mType').value;
  const tickers = document.getElementById('mTickers').value
                    .split(',').map(t=>t.trim()).filter(Boolean);
  closeModal();
  fetch('/api/trigger',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({type, tickers, evidence:{}})
  }).then(r=>r.json()).then(d=>{
    loadIncidents();
    if(d.incident_id) selectIncident(d.incident_id);
  });
}

// ── INCIDENT LIST ─────────────────────────────────────────
function loadIncidents(){
  fetch('/api/incidents').then(r=>r.json()).then(list=>{
    _incidents = list;
    const today = new Date().toISOString().slice(0,10);
    const todayList = list.filter(i=>i.timestamp&&i.timestamp.startsWith(today));
    const patterns  = todayList.filter(i=>i.status==='pattern_matched').length;
    const active    = list.filter(i=>i.status==='investigating'||i.status==='running').length;

    document.getElementById('hTotal').textContent   = todayList.length;
    document.getElementById('hPattern').textContent = patterns;
    document.getElementById('hActive').textContent  = active;
    document.getElementById('incCount').textContent = list.length;

    const el = document.getElementById('incList');
    if(!list.length){
      el.innerHTML='<div style="padding:20px;color:var(--text2);font-size:10px;text-align:center">No incidents yet.</div>';
      return;
    }

    el.innerHTML = list.map(inc=>{
      const st   = inc.status||'open';
      const stCls= {
        pattern_matched:'pattern',
        complete:'complete',
        low_confidence:'low_confidence',
        investigating:'running',
        running:'running',
      }[st]||'open';
      const stLabel = {
        pattern_matched:'PATTERN HIT',
        complete:'COMPLETE',
        low_confidence:'LOW CONF',
        investigating:'RUNNING',
        running:'RUNNING',
        open:'OPEN',
      }[st]||st.toUpperCase();
      const conf = inc.confidence!=null ? Math.round(inc.confidence*100)+'%' : '—';
      const confColor = inc.confidence>=.9?'var(--green)':inc.confidence>=.6?'var(--amber)':'var(--red)';
      const tickers = (inc.tickers||[]).join(', ') || inc.type;
      const ts = (inc.timestamp||'').slice(11,16);
      const isRunning = st==='investigating'||st==='running';
      const isSelected = _selected===inc.id;
      return `<div class="inc-item ${stCls} ${isSelected?'active':''}"
                   onclick="selectIncident('${inc.id}')">
        <div class="inc-id">${inc.id.slice(-20)}</div>
        <div class="inc-tickers">${tickers}</div>
        <div class="inc-meta">
          <span class="inc-status s-${stCls}">${isRunning?'<span class="spinner"></span> ':''} ${stLabel}</span>
          <span class="inc-time">${ts}</span>
          <span class="inc-conf" style="color:${confColor}">${conf}</span>
        </div>
      </div>`;
    }).join('');

    // Re-select if still active
    if(_selected) selectIncident(_selected, true);
  }).catch(()=>{});
}

// ── INCIDENT DETAIL ───────────────────────────────────────
function selectIncident(id, silent=false){
  _selected = id;
  if(!silent) loadIncidents();

  const inc = _incidents.find(i=>i.id===id);
  if(!inc) return;

  // Header
  document.getElementById('detailHdr').style.display='flex';
  document.getElementById('detailTitle').textContent =
    (inc.tickers||[]).join(', ') + ' — ' + (inc.type||'');
  document.getElementById('detailSub').textContent =
    `${inc.id}  ·  ${(inc.timestamp||'').slice(0,19).replace('T',' ')}  ·  Status: ${inc.status||'—'}`;

  // Confidence gauge
  const conf   = inc.confidence || 0;
  const confPct= Math.round(conf*100);
  const gColor = conf>=.9?'var(--green)':conf>=.6?'var(--amber)':'var(--red)';
  document.getElementById('gaugeFill').style.width      = confPct+'%';
  document.getElementById('gaugeFill').style.background = gColor;
  document.getElementById('gaugeVal').textContent       = confPct+'%';
  document.getElementById('gaugeVal').style.color       = gColor;

  // Approve button state
  if(inc.approved || _approved.has(id)){
    document.getElementById('swApprove').classList.add('on');
    document.getElementById('swApprove').textContent='APPROVED';
  } else {
    document.getElementById('swApprove').classList.remove('on');
    document.getElementById('swApprove').textContent='APPROVE';
  }

  // Body
  const body = document.getElementById('detailBody');

  if(inc.status==='investigating'||inc.status==='running'){
    body.innerHTML=`
      <div style="display:flex;align-items:center;gap:12px;padding:20px;color:var(--blue)">
        <span class="spinner" style="width:16px;height:16px;border-width:2px"></span>
        <span style="font-family:var(--head);letter-spacing:2px;font-size:13px">INVESTIGATING — gathering evidence and interrogating Claude...</span>
      </div>`;
    // Poll for updates
    clearTimeout(_pollTimer);
    _pollTimer = setTimeout(()=>selectIncident(id), 2000);
    return;
  }

  let html = '';

  // Verdict
  const vcls = inc.status==='pattern_matched'?'pattern':inc.status==='low_confidence'?'low_confidence':'';
  html += `<div class="verdict ${vcls}">`;
  html += `<div class="verdict-lbl">ROOT CAUSE VERDICT</div>`;
  if(inc.pattern_match){
    html += `<div class="pattern-badge">⚡ PATTERN MATCH: ${inc.pattern_match}</div><br>`;
  }
  html += `<div class="verdict-text">${esc(inc.rca||'No conclusion reached.')}</div>`;
  if(inc.proposed_fix){
    html += `<div class="verdict-fix">${esc(inc.proposed_fix)}</div>`;
  }
  html += `</div>`;

  // Transcript
  const rounds = inc.rounds||[];
  if(rounds.length){
    html += `<div class="transcript-hdr">
      INTERROGATION TRANSCRIPT
      <span style="color:var(--text2);font-weight:400">${rounds.length} round${rounds.length>1?'s':''}</span>
    </div>`;
    for(const r of rounds){
      const conf = r.confidence!=null?Math.round(r.confidence*100):'—';
      const cColor = r.confidence>=.9?'var(--green)':r.confidence>=.6?'var(--amber)':'var(--red)';
      html += `<div class="round-block">
        <div class="round-num">◈ ROUND ${r.round}</div>
        <div class="msg msg-claude">
          <span class="msg-conf" style="color:${cColor}">${conf}%</span>
          ${esc(r.response||'')}
        </div>
      </div>`;
    }
  } else if(inc.status==='pattern_matched'){
    html += `<div style="color:var(--text2);font-size:11px;padding:12px;border:1px solid var(--border);background:var(--bg2)">
      Pattern matched from knowledge base — no Claude interrogation needed.
      <br><br>
      Matched pattern: <span style="color:var(--green)">${inc.pattern_match}</span>
    </div>`;
  }

  body.innerHTML = html;
}

// ── APPROVE / REJECT ──────────────────────────────────────
function doApprove(){
  if(!_selected) return;
  fetch('/api/approve',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({incident_id:_selected})
  }).then(r=>r.json()).then(d=>{
    if(d.ok){
      _approved.add(_selected);
      document.getElementById('swApprove').classList.add('on');
      document.getElementById('swApprove').textContent='APPROVED';
    }
  });
}

function doReject(){
  if(!_selected) return;
  // Mark locally as rejected (no backend action — just UI feedback)
  document.getElementById('swApprove').classList.remove('on');
  alert('Incident flagged for manual review. Check rca/incident_log.json.');
}

// ── KNOWLEDGE BASE ────────────────────────────────────────
function loadKb(){
  fetch('/api/kb/patterns').then(r=>r.json()).then(patterns=>{
    document.getElementById('hKb').textContent = patterns.length;
    const el = document.getElementById('kbContent');
    if(!patterns.length){
      el.innerHTML='<div style="color:var(--text2);padding:20px">No patterns in knowledge base.</div>';
      return;
    }
    el.innerHTML = `<div class="kb-section">
      <div class="kb-hdr">CONFIRMED PATTERNS (${patterns.length})</div>
      ${patterns.map(p=>`
        <div class="kb-pattern">
          <div class="kb-pat-id">${p.id}</div>
          <div class="kb-pat-title">${esc(p.title||'')}</div>
          <div class="kb-meta">
            <span>Category: ${p.category||'—'}</span>
            <span>Confidence: ${Math.round((p.confidence||0)*100)}%</span>
            <span>Seen: ${p.recurrence_count||1}×</span>
            <span>Confirmed: ${p.confirmed_date||'—'}</span>
            <span style="color:${p.fix_date?'var(--green)':'var(--amber)'}">
              ${p.fix_date?'✓ Fixed '+p.fix_date:'⚠ Not yet fixed'}
            </span>
          </div>
          <div class="kb-cause">${esc(p.root_cause||'')}</div>
        </div>`).join('')}
    </div>`;
  });
}

// ── UTILS ─────────────────────────────────────────────────
function esc(s){
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/\n/g,'<br>');
}

function tickClock(){
  document.getElementById('clock').textContent = new Date().toTimeString().slice(0,8);
}

// ── BOOT ──────────────────────────────────────────────────
loadIncidents();
tickClock();
setInterval(loadIncidents, 3000);
setInterval(tickClock, 1000);
</script>
</body>
</html>"""


if __name__ == "__main__":
    log.info("RCA Console starting on http://localhost:5002")
    app.run(host="0.0.0.0", port=5002, debug=False, use_reloader=False)