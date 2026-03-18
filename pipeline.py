"""
KUBER'S CALLING — pipeline_monitor.py
=======================================
Standalone Flask app. Port 5001.
Shows Kubers vs INDmoney side-by-side pipeline per scrip.
Run independently of kubers_calling.py.

Usage:
    python pipeline_monitor.py

Reads kubers_live.db for history + today's trades.
Calls INDmoney API for live open positions.
Token is read from the same investright_creds.json as Kubers.
"""

import os, sys, json, sqlite3, logging
from datetime import datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask, jsonify, request
import requests as _requests

app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("pipeline")

# ── Config ────────────────────────────────────────────────────────────
try:
    from config import DB_LIVE_PATH, CREDS_FILE, TOKEN_KEY, INDMONEY_BASE_URL
except ImportError:
    DB_LIVE_PATH       = os.path.join(os.path.dirname(__file__), "database", "kubers_live.db")
    CREDS_FILE         = os.path.join(os.path.dirname(__file__), "investright_creds.json")
    TOKEN_KEY          = "jwt_token"
    INDMONEY_BASE_URL  = "https://api.indstocks.com"


def _get_token():
    try:
        with open(CREDS_FILE) as f:
            return json.load(f).get(TOKEN_KEY, "").strip()
    except Exception:
        return ""


def _db():
    conn = sqlite3.connect(DB_LIVE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Data endpoints ────────────────────────────────────────────────────

@app.route("/api/pipeline")
def api_pipeline():
    """
    Returns unified pipeline state for all scrips active today.
    Each scrip has:
      kubers:  {stage, entry_price, exit_price, qty, sl, target, hold_minutes,
                exit_reason, signal_time, fill_time, exit_time, net_pnl}
      indmoney: {stage, qty, avg_price, pnl}
      diverged: bool
    """
    today = datetime.now().strftime("%Y-%m-%d")
    token = _get_token()
    headers = {"Authorization": token, "Content-Type": "application/json"} if token else {}

    # ── 1. Kubers: open positions
    kubers_open = {}
    kubers_closed = {}

    try:
        conn = _db()
        # Open positions
        rows = conn.execute("""
            SELECT ticker, direction, entry_price, qty, sl_price, target_price,
                   entry_time, strategy_name, signal_id
            FROM positions
        """).fetchall()
        for r in rows:
            kubers_open[r["ticker"]] = {
                "stage":         "HOLDING",
                "direction":     r["direction"],
                "entry_price":   r["entry_price"],
                "qty":           r["qty"],
                "sl":            r["sl_price"],
                "target":        r["target_price"],
                "entry_time":    r["entry_time"],
                "strategy":      r["strategy_name"],
                "signal_id":     r["signal_id"],
                "exit_price":    None,
                "exit_reason":   None,
                "exit_time":     None,
                "net_pnl":       None,
                "hold_minutes":  None,
            }

        # Closed today
        rows = conn.execute("""
            SELECT ticker, direction, entry_price, exit_price, qty,
                   entry_time, exit_time,
                   exit_reason, net_pnl, hold_minutes
            FROM trade_log
            WHERE DATE(entry_time) = ?
            ORDER BY entry_time DESC
        """, (today,)).fetchall()
        for r in rows:
            t = r["ticker"]
            if t not in kubers_closed:
                kubers_closed[t] = []
            kubers_closed[t].append({
                "stage":        _exit_stage(r["exit_reason"]),
                "direction":    r["direction"],
                "entry_price":  r["entry_price"],
                "exit_price":   r["exit_price"],
                "qty":          r["qty"],
                "sl":           None,
                "target":       None,
                "entry_time":   r["entry_time"],
                "exit_time":    r["exit_time"],
                "exit_reason":  r["exit_reason"],
                "net_pnl":      r["net_pnl"],
                "hold_minutes": r["hold_minutes"],
                "strategy":     "RULE_V1",
            })

        # Signal log — fired today (includes rejected)
        signals = conn.execute("""
            SELECT ticker, direction, disposition, timestamp,
                   limit_price, sl_price, vol_z_score, entry_reason
            FROM signal_log
            WHERE DATE(timestamp) = ?
            ORDER BY timestamp DESC
        """, (today,)).fetchall()
        signals_by_ticker = {}
        for s in signals:
            t = s["ticker"]
            if t not in signals_by_ticker:
                signals_by_ticker[t] = []
            signals_by_ticker[t].append(dict(s))

        conn.close()
    except Exception as e:
        log.warning("DB error: %s", e)
        signals_by_ticker = {}

    # ── 2. INDmoney: open positions
    indmoney_open = {}
    if token:
        try:
            r = _requests.get(
                f"{INDMONEY_BASE_URL}/portfolio/positions?segment=equity&product=intraday",
                headers=headers, timeout=8
            )
            if r.status_code == 200:
                raw  = r.json().get("data", [])
                rows = raw if isinstance(raw, list) else raw.get("net_positions", [])
                for p in rows:
                    qty = int(p.get("net_qty", 0))
                    if qty == 0:
                        continue
                    sym = p.get("symbol", "").strip()
                    if sym:
                        indmoney_open[sym] = {
                            "stage":     "HOLDING",
                            "direction": "LONG" if qty > 0 else "SHORT",
                            "qty":       abs(qty),
                            "avg_price": float(p.get("avg_price", 0)),
                            "ltp":       float(p.get("avg_price", 0)),
                            "pnl":       float(p.get("realized_profit", 0)),
                        }
        except Exception as e:
            log.warning("INDmoney positions error: %s", e)

    # ── 3. Build unified pipeline
    all_tickers = (
        set(kubers_open.keys()) |
        set(kubers_closed.keys()) |
        set(indmoney_open.keys()) |
        set(signals_by_ticker.keys())
    )

    pipeline = []
    for ticker in sorted(all_tickers):
        k_open   = kubers_open.get(ticker)
        k_closed = kubers_closed.get(ticker, [])
        ind      = indmoney_open.get(ticker)
        sigs     = signals_by_ticker.get(ticker, [])

        # Determine Kubers stage
        if k_open:
            k_stage = "HOLDING"
            k_data  = k_open
        elif k_closed:
            k_stage = k_closed[0]["stage"]
            k_data  = k_closed[0]
        elif sigs:
            live_sigs = [s for s in sigs if s["disposition"] == "LIVE"]
            if live_sigs:
                k_stage = "ORDER_PLACED"
            else:
                k_stage = "SIGNAL_FIRED" if sigs[0]["vol_z_score"] and float(sigs[0]["vol_z_score"] or 0) >= 2 else "REJECTED"
            k_data = None
        else:
            k_stage = "NONE"
            k_data  = None

        # Determine INDmoney stage
        if ind:
            i_stage = "HOLDING"
        elif any(t == ticker for t in (k_closed or {}) if k_closed):
            i_stage = "CLOSED"
        else:
            i_stage = "NONE"

        # Divergence check
        diverged = _is_diverged(k_stage, i_stage, k_open, ind)

        pipeline.append({
            "ticker":       ticker,
            "kubers": {
                "stage":        k_stage,
                "data":         k_data,
                "signals":      sigs[:3],
                "all_closes":   k_closed,
            },
            "indmoney": {
                "stage":   i_stage,
                "data":    ind,
            },
            "diverged": diverged,
        })

    # Sort: diverged first, then holding, then closed, then rejected
    order = {"HOLDING": 0, "ORDER_PLACED": 1, "SIGNAL_FIRED": 2,
             "FILLED": 3, "TARGET": 4, "SL_HIT": 5, "TIME_STOP": 6,
             "REJECTED": 7, "NONE": 8}
    pipeline.sort(key=lambda x: (
        not x["diverged"],
        order.get(x["kubers"]["stage"], 9)
    ))

    # Auto-trigger RCA for any new divergences
    try:
        _maybe_fire_rca(pipeline)
    except Exception:
        pass

    return jsonify(pipeline)


@app.route("/api/stats")
def api_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _db()
        row = conn.execute("""
            SELECT
                COUNT(*) total,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins,
                SUM(net_pnl) net_pnl,
                SUM(cost_total) total_costs,
                AVG(hold_minutes) avg_hold
            FROM trade_log WHERE DATE(entry_time) = ?
        """, (today,)).fetchone()
        open_count = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
        conn.close()
        return jsonify({
            "total":       row["total"] or 0,
            "wins":        row["wins"] or 0,
            "net_pnl":     round(row["net_pnl"] or 0, 2),
            "total_costs": round(row["total_costs"] or 0, 2),
            "avg_hold":    round(row["avg_hold"] or 0, 1),
            "open":        open_count,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _exit_stage(reason):
    if not reason:
        return "CLOSED"
    r = reason.upper()
    if "TARGET" in r:   return "TARGET"
    if "SL" in r:       return "SL_HIT"
    if "TIME" in r:     return "TIME_STOP"
    if "EOD" in r:      return "EOD"
    if "KILL" in r:     return "KILL_SWITCH"
    return "CLOSED"


def _is_diverged(k_stage, i_stage, k_open, ind_open):
    # Both holding — check direction matches
    if k_stage == "HOLDING" and i_stage == "HOLDING":
        if k_open and ind_open:
            return k_open["direction"] != ind_open["direction"]
        return False
    # One holding, other not
    if k_stage == "HOLDING" and i_stage == "NONE":
        return True
    if i_stage == "HOLDING" and k_stage not in ("HOLDING", "ORDER_PLACED"):
        return True
    return False


# ── Dashboard HTML ─────────────────────────────────────────────────────

DASHBOARD = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kubers · Pipeline</title>
<style>
:root{
  --bg:#f4f3f0;--surf:#fff;--surf2:#f0efe9;
  --bd:#e2dfd8;--bd2:#ccc9bf;
  --t0:#181816;--t1:#484843;--t2:#8a8a82;
  --green:#166534;--gbg:#dcfce7;--gbd:#86efac;
  --red:#991b1b;--rbg:#fee2e2;--rbd:#fca5a5;
  --amber:#92400e;--abg:#fef3c7;--abd:#fcd34d;
  --blue:#1e3a5f;--bbg:#dbeafe;--bbd:#93c5fd;
  --purple:#581c87;--pbg:#f3e8ff;--pbd:#c084fc;
  --sans:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
  --mono:'SF Mono','Fira Code','Cascadia Code',monospace;
}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--t0);font-family:var(--sans);font-size:13px}

.hdr{
  background:var(--surf);border-bottom:1px solid var(--bd);
  height:52px;padding:0 20px;display:flex;align-items:center;gap:14px;
  position:sticky;top:0;z-index:100;
}
.logo{font-size:15px;font-weight:700;letter-spacing:.3px}
.logo em{color:var(--t2);font-style:normal}
.sep{width:1px;height:22px;background:var(--bd)}
.stat{display:flex;flex-direction:column;gap:1px}
.slbl{font-size:9px;color:var(--t2);text-transform:uppercase;letter-spacing:.07em}
.sval{font-size:14px;font-weight:600}
.pos{color:var(--green)}.neg{color:var(--red)}.neu{color:var(--t1)}
.ml{margin-left:auto;display:flex;align-items:center;gap:10px}
.clk{font-family:var(--mono);font-size:11px;color:var(--t2)}
.btn{background:none;border:1px solid var(--bd2);color:var(--t1);
  padding:5px 11px;font-size:11px;border-radius:5px;cursor:pointer}
.btn:hover{border-color:var(--t0);color:var(--t0)}

.leg{
  background:var(--surf);border-bottom:1px solid var(--bd);
  padding:6px 20px;display:flex;align-items:center;gap:12px;
  font-size:10px;color:var(--t2);
}
.ld{display:flex;align-items:center;gap:4px}
.dot{width:7px;height:7px;border-radius:50%}
.div-warn{margin-left:auto;color:var(--red);font-weight:600;font-size:10px}

.main{padding:14px 20px;display:flex;flex-direction:column;gap:8px;max-width:1080px}

/* card grid: ticker | kubers | indmoney */
.card{
  display:grid;grid-template-columns:100px 1fr 1fr;
  background:var(--surf);border:1px solid var(--bd);border-radius:8px;overflow:hidden;
}
.card.div{border-color:var(--rbd);box-shadow:0 0 0 1px var(--rbd)}

.tk{
  padding:12px 8px;display:flex;flex-direction:column;
  justify-content:center;align-items:center;gap:4px;
  border-right:1px solid var(--bd);background:var(--surf2);
}
.tk-name{font-weight:700;font-size:14px;letter-spacing:.4px}
.tk-dir{font-size:9px;font-weight:600;padding:2px 6px;border-radius:3px;letter-spacing:.05em}
.dir-L{background:var(--bbg);color:var(--blue);border:1px solid var(--bbd)}
.dir-S{background:var(--rbg);color:var(--red);border:1px solid var(--rbd)}
.tk-hold{font-family:var(--mono);font-size:10px;color:var(--t2)}
.div-lbl{font-size:8px;font-weight:700;color:var(--red);background:var(--rbg);
  border:1px solid var(--rbd);padding:1px 5px;border-radius:3px}

.side{padding:11px 13px;display:flex;flex-direction:column;gap:5px}
.side-k{border-right:1px solid var(--bd)}

.shdr{display:flex;align-items:center;justify-content:space-between}
.slabel{font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.07em}
.lk{color:#1e5a8a}.li{color:#8a4a1e}

/* stage pill */
.pill{display:inline-block;font-size:9px;font-weight:600;padding:1px 7px;
  border-radius:10px;letter-spacing:.04em;white-space:nowrap}
.p-HOLD{background:var(--bbg);color:var(--blue);border:1px solid var(--bbd)}
.p-TGT{background:var(--gbg);color:var(--green);border:1px solid var(--gbd)}
.p-SL{background:var(--rbg);color:var(--red);border:1px solid var(--rbd)}
.p-TS{background:var(--abg);color:var(--amber);border:1px solid var(--abd)}
.p-SIG{background:var(--pbg);color:var(--purple);border:1px solid var(--pbd)}
.p-NONE{background:var(--surf2);color:var(--t2);border:1px solid var(--bd)}

.narr{font-size:11px;color:var(--t1);line-height:1.55;
  border-top:1px solid var(--bd);padding-top:5px;margin-top:2px}
.narr b{color:var(--t0);font-weight:600}

/* pnl */
.pnl{display:inline-block;font-family:var(--mono);font-size:11px;font-weight:700;
  padding:1px 6px;border-radius:3px}
.pnl-p{background:var(--gbg);color:var(--green)}
.pnl-n{background:var(--rbg);color:var(--red)}

/* divergence alert row — spans kubers+indmoney columns */
.div-row{
  grid-column:2/-1;
  background:var(--rbg);border-top:1px solid var(--rbd);
  padding:7px 13px;font-size:11px;color:var(--red);line-height:1.55;
}
.div-row b{font-weight:600}

.multi{font-size:10px;color:var(--t2);margin-top:3px}

.empty{text-align:center;padding:56px 20px;color:var(--t2)}
.empty strong{display:block;font-size:15px;color:var(--t1);margin-bottom:5px}
</style>
</head>
<body>

<div class="hdr">
  <div class="logo">Kubers <em>Pipeline</em></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Open</div><div class="sval neu" id="sOpen">—</div></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Closed</div><div class="sval neu" id="sClosed">—</div></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Win rate</div><div class="sval neu" id="sWin">—</div></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Net P&L</div><div class="sval" id="sPnl">—</div></div>
  <div class="sep"></div>
  <div class="stat"><div class="slbl">Costs</div><div class="sval neu" id="sCosts">—</div></div>
  <div class="ml">
    <span class="clk" id="clk">--:--:--</span>
    <button class="btn" onclick="load()">↻ Refresh</button>
  </div>
</div>

<div class="leg">
  <span class="ld"><span class="dot" style="background:var(--blue)"></span>Holding</span>
  <span class="ld"><span class="dot" style="background:var(--green)"></span>Target hit</span>
  <span class="ld"><span class="dot" style="background:var(--red)"></span>SL hit</span>
  <span class="ld"><span class="dot" style="background:var(--amber)"></span>Time stop</span>
  <span class="ld"><span class="dot" style="background:var(--purple)"></span>Signal/Order</span>
  <span class="ld"><span class="dot" style="background:var(--bd2)"></span>None</span>
  <span class="div-warn" id="divCount"></span>
</div>

<div class="main" id="list">
  <div class="empty"><strong>Loading…</strong>Fetching pipeline</div>
</div>

<script>
const f2  = v => v==null?'—':(+v).toFixed(2);
const f0  = v => v==null?'—':(+v).toFixed(0);
const inr = v => { if(v==null) return '—'; const n=+v; return (n>=0?'+':'')+'₹'+Math.abs(n).toFixed(0); }
const t16 = s => s?(s+'').slice(11,16):'—';

function pillClass(stage){
  if(!stage) return 'p-NONE';
  const s=stage.toUpperCase();
  if(s==='HOLDING')           return 'p-HOLD';
  if(s==='TARGET')            return 'p-TGT';
  if(s.includes('SL'))        return 'p-SL';
  if(s.includes('TIME')||s==='EOD') return 'p-TS';
  if(s.includes('SIGNAL')||s.includes('ORDER')) return 'p-SIG';
  return 'p-NONE';
}
function pillLabel(stage){
  const m={HOLDING:'Holding',TARGET:'Target ✓',SL_HIT:'SL hit',
    TIME_STOP_CHECK:'Time stop',TIME_STOP_HARD:'Time stop',EOD:'EOD close',
    SIGNAL_FIRED:'Signal',ORDER_PLACED:'Order placed',
    REJECTED:'Rejected',NONE:'—',CLOSED:'Closed'};
  return m[stage]||stage||'—';
}

function narrateK(k){
  const d=k.data;
  if(!d) return k.stage==='NONE'?'<em>No activity today</em>':'Stage: '+k.stage;
  const dir=d.direction||'';
  if(k.stage==='HOLDING'){
    const held=d.hold_minutes!=null?f0(d.hold_minutes)+'m':'—';
    return `<b>${dir}</b> · Entry <b>₹${f2(d.entry_price)}</b> · Qty ${d.qty} · SL ₹${f2(d.sl)} · Target ₹${f2(d.target)} · Held ${held}`;
  }
  const xr=d.exit_reason?` · <b>${d.exit_reason}</b>`:'';
  const pnl=d.net_pnl!=null?` · <span class="pnl ${d.net_pnl>=0?'pnl-p':'pnl-n'}">${inr(d.net_pnl)}</span>`:'';
  return `<b>${dir}</b> · In ₹${f2(d.entry_price)} → Out ₹${f2(d.exit_price)} · Qty ${d.qty} · Held ${f0(d.hold_minutes)}m${xr}${pnl}`;
}

function narrateI(ind){
  const d=ind.data;
  if(ind.stage==='NONE'||!d) return '<em>No open position on INDmoney</em>';
  if(ind.stage==='HOLDING'){
    const pnl=d.pnl!=null?` · <span class="pnl ${d.pnl>=0?'pnl-p':'pnl-n'}">${inr(d.pnl)}</span>`:'';
    return `<b>${d.direction}</b> · Qty ${d.qty} · Avg ₹${f2(d.avg_price)}${pnl}`;
  }
  if(ind.stage==='CLOSED') return '<em>Closed on INDmoney</em>';
  return 'Stage: '+ind.stage;
}

function divergeText(p){
  const ks=p.kubers.stage, is=p.indmoney.stage;
  const kd=p.kubers.data, id=p.indmoney.data;
  if(ks==='HOLDING'&&is==='NONE')
    return `<b>Kubers thinks ${p.ticker} is still open</b> (${kd?.direction||'?'}, ${kd?.qty||'?'} shares since ${t16(kd?.entry_time)}) but INDmoney has no position. The trade may have been closed by INDmoney — SL, manual close, or broker squareoff — without Kubers detecting it. Run startup reconciliation.`;
  if(is==='HOLDING'&&ks!=='HOLDING')
    return `<b>INDmoney has a ${id?.direction||'?'} position in ${p.ticker} that Kubers never placed.</b> This is a ghost carry position from a prior session. INDmoney will auto-close it at 15:20. No action needed unless it's large.`;
  if(ks==='HOLDING'&&is==='HOLDING'&&kd&&id&&kd.direction!==id.direction)
    return `<b>⚠ Direction mismatch</b> — Kubers says <b>${kd.direction}</b> but INDmoney says <b>${id.direction}</b>. Stop the engine and reconcile manually.`;
  return `Kubers: <b>${ks}</b> · INDmoney: <b>${is}</b>. These should match. Check for a missed fill or broker-side closure.`;
}

function multiNote(all){
  if(!all||all.length<=1) return '';
  const tot=all.reduce((s,c)=>s+(c.net_pnl||0),0);
  return `<div class="multi">${all.length} trades today · Total <span class="pnl ${tot>=0?'pnl-p':'pnl-n'}">${inr(tot)}</span></div>`;
}

function renderCard(p){
  const dir=(p.kubers.data?.direction)||(p.indmoney.data?.direction)||'';
  const hold=p.kubers.data?.hold_minutes;
  const ks=p.kubers.stage, is=p.indmoney.stage;
  return `<div class="card${p.diverged?' div':''}">
    <div class="tk">
      <div class="tk-name">${p.ticker}</div>
      ${dir?`<div class="tk-dir ${dir==='LONG'?'dir-L':'dir-S'}">${dir}</div>`:''}
      ${hold!=null?`<div class="tk-hold">${f0(hold)}m</div>`:''}
      ${p.diverged?`<div class="div-lbl">⚠ DIVERGED</div>`:''}
    </div>
    <div class="side side-k">
      <div class="shdr">
        <span class="slabel lk">Kubers</span>
        <span class="pill ${pillClass(ks)}">${pillLabel(ks)}</span>
      </div>
      <div class="narr">${narrateK(p.kubers)}</div>
      ${multiNote(p.kubers.all_closes)}
    </div>
    <div class="side">
      <div class="shdr">
        <span class="slabel li">INDmoney</span>
        <span class="pill ${pillClass(is)}">${pillLabel(is)}</span>
      </div>
      <div class="narr">${narrateI(p.indmoney)}</div>
    </div>
    ${p.diverged?`<div class="div-row">⚠ ${divergeText(p)}</div>`:''}
  </div>`;
}

function load(){
  Promise.all([
    fetch('/api/pipeline').then(r=>r.json()),
    fetch('/api/stats').then(r=>r.json())
  ]).then(([pipe,stats])=>{
    document.getElementById('sOpen').textContent=stats.open||0;
    document.getElementById('sClosed').textContent=stats.total||0;
    const wr=stats.total>0?Math.round(stats.wins/stats.total*100)+'%':'—';
    document.getElementById('sWin').textContent=wr;
    const pnlEl=document.getElementById('sPnl');
    const pnl=stats.net_pnl||0;
    pnlEl.textContent=(pnl>=0?'+':'')+'₹'+Math.abs(pnl).toFixed(0);
    pnlEl.className='sval '+(pnl>=0?'pos':'neg');
    document.getElementById('sCosts').textContent='₹'+(stats.total_costs||0).toFixed(0);

    const divs=pipe.filter(p=>p.diverged);
    document.getElementById('divCount').textContent=
      divs.length?`⚠ ${divs.length} divergence${divs.length>1?'s':''}`:'' ;

    const list=document.getElementById('list');
    if(!pipe.length){
      list.innerHTML='<div class="empty"><strong>No activity today</strong>No trades placed yet.</div>';
      return;
    }
    const sorted=[...pipe].sort((a,b)=>{
      if(a.diverged!==b.diverged) return a.diverged?-1:1;
      const ord={HOLDING:0,ORDER_PLACED:1,SIGNAL_FIRED:2,TARGET:3,
                 SL_HIT:4,TIME_STOP:5,EOD:6,CLOSED:7,REJECTED:8,NONE:9};
      return (ord[a.kubers.stage]??9)-(ord[b.kubers.stage]??9);
    });
    list.innerHTML=sorted.map(renderCard).join('');
  }).catch(e=>{
    document.getElementById('list').innerHTML=
      `<div class="empty"><strong>Error</strong>${e.message}</div>`;
  });
}

function tickClk(){document.getElementById('clk').textContent=new Date().toTimeString().slice(0,8);}
load(); tickClk();
setInterval(load,5000); setInterval(tickClk,1000);
</script>
</body>
</html>"""


@app.route("/api/trigger_rca", methods=["POST"])
def api_trigger_rca():
    """
    Fire an RCA investigation. Called internally when divergence detected,
    or externally from the pipeline dashboard.
    Forwards to rca_console on port 5002.
    """
    data = request.get_json(silent=True) or {}
    try:
        r = _requests.post(
            "http://localhost:5002/api/trigger",
            json=data, timeout=5
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e), "hint": "Is rca_console.py running on port 5002?"}), 503


# Track which divergences we already fired RCA for (avoid spam)
_rca_fired = set()

def _maybe_fire_rca(pipeline_data):
    """Auto-fire RCA when new divergences are detected."""
    for p in pipeline_data:
        if not p.get("diverged"):
            continue
        key = f"{p['ticker']}_{p['kubers']['stage']}_{p['indmoney']['stage']}"
        if key in _rca_fired:
            continue
        _rca_fired.add(key)
        try:
            _requests.post("http://localhost:5002/api/trigger", json={
                "type":    "DIVERGENCE",
                "tickers": [p["ticker"]],
                "evidence": {
                    "kubers_stage":   p["kubers"]["stage"],
                    "indmoney_stage": p["indmoney"]["stage"],
                    "in_kubers_db_positions": p["kubers"]["stage"] == "HOLDING",
                    "in_indmoney_positions":  p["indmoney"]["stage"] == "HOLDING",
                }
            }, timeout=2)
            log.info("RCA triggered for divergence: %s", p["ticker"])
        except Exception:
            pass  # RCA console not running — silent fail


@app.route("/")
def index():
    return DASHBOARD


if __name__ == "__main__":
    log.info("Pipeline Monitor starting on http://localhost:5001")
    app.run(host="0.0.0.0", port=5001, debug=False, use_reloader=False)