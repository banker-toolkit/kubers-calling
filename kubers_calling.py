"""
KUBER'S CALLING — kubers_calling.py
=====================================
Flask dashboard. Port 5000.
Engine runs in background thread.
"""

# Patch every requests.Session to bind outbound to INDmoney-registered IPv6.
# Patching Session.__init__ catches requests.get/post which create Sessions internally.
# Must happen before any other import that uses requests.
import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter

_INDMONEY_IP = "2405:201:3d:5059:e90d:78e1:b1c4:92a3"

class _BoundAdapter(_HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        kwargs["source_address"] = (_INDMONEY_IP, 0)
        super().init_poolmanager(*args, **kwargs)

_orig_session_init = _requests.Session.__init__
def _patched_session_init(self, *args, **kwargs):
    _orig_session_init(self, *args, **kwargs)
    _adapter = _BoundAdapter()
    self.mount("https://", _adapter)
    self.mount("http://",  _adapter)
_requests.Session.__init__ = _patched_session_init

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import sys, threading, logging, sqlite3, json, time
_engine_thread_started = False
from datetime import datetime
from flask import Flask, jsonify, request, Response

import engine

app = Flask(__name__)
logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s %(name)s %(levelname)s %(message)s",
    datefmt = "%H:%M:%S",
)
log = logging.getLogger("dashboard")

from config import DB_LIVE_PATH

def _db():
    conn = sqlite3.connect(DB_LIVE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ════════════════════════════════════════════════════════════════════
# API ENDPOINTS
# ════════════════════════════════════════════════════════════════════

@app.route("/api/state")
def api_state():
    state = engine.get_state()
    # Inject trades_today (churn count) — quick COUNT query, negligible cost
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        conn  = _db()
        row   = conn.execute(
            "SELECT COUNT(*) n FROM trade_log WHERE DATE(entry_time)=?", (today,)
        ).fetchone()
        conn.close()
        state["trades_today"] = row["n"] if row else 0
    except Exception:
        state["trades_today"] = 0
    return jsonify(state)

@app.route("/api/positions")
def api_positions():
    return jsonify(engine.get_state().get("positions", []))

@app.route("/api/pending_exits")
def api_pending_exits():
    """Returns positions currently awaiting exit fill confirmation."""
    return jsonify(engine.get_state().get("pending_exits_detail", []))

@app.route("/api/shadow_leaderboard")
def api_shadow_leaderboard():
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = _db()
        rows = conn.execute("""
            SELECT strategy_name,
                   COUNT(*) as trades,
                   SUM(CASE WHEN fill_simulated=1 THEN 1 ELSE 0 END) as fills,
                   SUM(COALESCE(simulated_pnl,0)) as pnl,
                   AVG(CASE WHEN fill_simulated=1 THEN slippage_pct END) as avg_slip
            FROM shadow_log WHERE created_at LIKE ?
            GROUP BY strategy_name ORDER BY pnl DESC
        """, (f"{today}%",)).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/signal_trace")
def api_signal_trace():
    """Last N signals with full gate trace."""
    n = int(request.args.get("n", 10))
    try:
        conn = _db()
        rows = conn.execute("""
            SELECT signal_id, ticker, direction, timestamp, disposition,
                   vol_z_score, velocity_ratio, sector_lag_pct, sector_slope,
                   candle_close_pct, regime, vix, nifty_atr, atr_15m,
                   gate_trace, entry_reason, risk_reason, sector, limit_price, sl_price
            FROM signal_log
            ORDER BY timestamp DESC LIMIT ?
        """, (n,)).fetchall()
        conn.close()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["gate_trace"] = json.loads(d["gate_trace"] or "[]")
            except Exception:
                d["gate_trace"] = []
            result.append(d)
        return jsonify(result)
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/history")
def api_history():
    try:
        conn = _db()
        rows = conn.execute("""
            SELECT ticker, direction, entry_price, exit_price, qty,
                   hold_minutes, exit_reason, gross_pnl, cost_total, net_pnl,
                   entry_time, exit_time, entry_narrative, exit_narrative,
                   cost_brokerage, cost_stt, cost_exchange, cost_stamp, cost_gst
            FROM trade_log ORDER BY entry_time DESC LIMIT 50
        """).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/dossier")
def api_dossier():
    try:
        conn = _db()
        rows = conn.execute(
            "SELECT * FROM daily_dossier ORDER BY date DESC LIMIT 30"
        ).fetchall()
        conn.close()
        return jsonify([dict(r) for r in rows])
    except sqlite3.Error as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/config", methods=["GET","POST"])
def api_config():
    if request.method == "GET":
        return jsonify(engine.get_state().get("live_config", {}))
    data = request.get_json(silent=True) or {}
    allowed = {"global_limit", "per_stock_limit", "equity_floor"}
    params  = {}
    for k in allowed:
        if k in data:
            try: params[k] = float(data[k])
            except (ValueError, TypeError): pass
    if params:
        engine.update_live_config(params)
    return jsonify({"ok": True, "updated": list(params.keys())})

@app.route("/api/token", methods=["POST"])
def api_token():
    """Update JWT token and reconnect API."""
    data  = request.get_json(silent=True) or {}
    token = data.get("token", "").strip()
    if not token:
        return jsonify({"ok": False, "error": "Empty token"}), 400
    try:
        import json as _json
        from config import CREDS_FILE, TOKEN_KEY
        try:
            with open(CREDS_FILE) as f:
                creds = _json.load(f)
        except Exception:
            creds = {}
        creds[TOKEN_KEY] = token
        with open(CREDS_FILE, "w") as f:
            _json.dump(creds, f, indent=2)
        from data.feed import load_token, verify_connection
        load_token()
        result = verify_connection()
        # Restart engine thread if it died at launch (no token was available)
        if result["ok"] and not _engine_thread_started:
            t = threading.Thread(target=_engine_thread, daemon=True, name="engine")
            t.start()
            log.info("[dashboard] Engine thread (re)started after token connect")
        return jsonify({
            "ok":          result["ok"],
            "connected":   result["ok"],
            "market_open": result.get("market_open", False),
            "detail":      result.get("detail", ""),
            "status_code": result.get("status", 0),
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/scanner")
def api_scanner():
    """Return last scanner decision_log for all tickers."""
    state = engine.get_state()
    return jsonify(state.get("decision_log", []))

@app.route("/api/candle_stats")
def api_candle_stats():
    from data.candle_factory import candle_store
    return jsonify(candle_store.stats())

@app.route("/api/volume_profile_stats")
def api_vp_stats():
    from features.volume_profile import get_profile_coverage
    return jsonify(get_profile_coverage())

@app.route("/")
def index():
    return DASHBOARD_HTML


# ════════════════════════════════════════════════════════════════════
# ENGINE THREAD
# ════════════════════════════════════════════════════════════════════

def _engine_thread():
    global _engine_thread_started
    _engine_thread_started = True
    if not engine.startup():
        log.error("[dashboard] Engine startup failed — token may not be set yet")
        _engine_thread_started = False
        return
    log.info("[dashboard] Engine running")
    from config import EOD_SQUAREOFF_TIME, SCAN_INTERVAL_SEC
    while True:
        if datetime.now().strftime("%H:%M") >= EOD_SQUAREOFF_TIME:
            log.info("[dashboard] EOD — engine stopping")
            break
        try:
            engine.run_cycle()
        except Exception as e:
            log.error("[dashboard] Cycle error: %s", e, exc_info=True)
        time.sleep(SCAN_INTERVAL_SEC)
    log.info("[dashboard] Engine thread exiting")
    _engine_thread_started = False


# ════════════════════════════════════════════════════════════════════
# DASHBOARD HTML
# ════════════════════════════════════════════════════════════════════

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Kuber's Calling</title>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@300;400;500;600&family=Chakra+Petch:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg0:#020810;--bg1:#060F1A;--bg2:#0A1628;--bg3:#0F1E35;
  --border:#132340;--border2:#1C3050;
  --text0:#E2EAF4;--text1:#8DA8C4;--text2:#4A6580;
  --cyan:#00C8FF;--cyan2:#0091BB;
  --green:#00E676;--green2:#00A854;
  --red:#FF3D5A;--red2:#BB2A40;
  --amber:#FFB300;--amber2:#CC8F00;
  --purple:#9B6DFF;
  --mono:'IBM Plex Mono',monospace;
  --head:'Chakra Petch',sans-serif;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg0);color:var(--text0);font-family:var(--mono);font-size:11px;overflow:hidden}
body::before{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.04) 2px,rgba(0,0,0,0.04) 4px);pointer-events:none;z-index:9999}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
@keyframes pulse-g{0%,100%{box-shadow:0 0 0 0 rgba(0,230,118,.4)}50%{box-shadow:0 0 0 4px rgba(0,230,118,0)}}
@keyframes slide-in{from{opacity:0;transform:translateX(-6px)}to{opacity:1;transform:none}}
.blink{animation:blink 1.1s step-end infinite}
.new-row{animation:slide-in .3s ease-out}

/* LAYOUT */
.root{display:grid;grid-template-rows:42px 56px 1fr 26px;height:100vh}

/* HEADER */
.header{background:var(--bg1);border-bottom:1px solid var(--border2);display:flex;align-items:center;padding:0 12px;gap:12px}
.logo{font-family:var(--head);font-size:15px;font-weight:700;color:var(--cyan);letter-spacing:2px;display:flex;align-items:center;gap:6px;white-space:nowrap}
.logo .bolt{color:var(--amber)}
.hdiv{width:1px;height:22px;background:var(--border2)}
.token-wrap{display:flex;align-items:center;gap:6px}
.lbl{font-size:8px;letter-spacing:1.5px;color:var(--text2);text-transform:uppercase}
.token-input{background:var(--bg3);border:1px solid var(--border2);color:var(--amber);font-family:var(--mono);font-size:10px;padding:4px 8px;width:230px;border-radius:2px;outline:none;transition:border-color .15s}
.token-input:focus{border-color:var(--cyan2)}
.token-input::placeholder{color:var(--text2)}
.btn{background:var(--cyan2);color:var(--bg0);border:none;font-family:var(--head);font-size:8px;font-weight:700;letter-spacing:1px;padding:4px 10px;border-radius:2px;cursor:pointer;text-transform:uppercase;white-space:nowrap}
.btn:hover{background:var(--cyan)}
.api-dot{width:6px;height:6px;border-radius:50%;background:var(--green);animation:pulse-g 2s infinite;flex-shrink:0}
.api-dot.dead{background:var(--red);animation:none}
.api-txt{font-size:9px;color:var(--text2)}
.hright{margin-left:auto;display:flex;align-items:center;gap:14px}
.pnl{font-family:var(--head);font-size:15px;font-weight:600}
.pnl-pos{color:var(--green)}.pnl-neg{color:var(--red)}
.clock{color:var(--text1);font-size:12px;letter-spacing:1px;font-variant-numeric:tabular-nums}
.cycle{font-size:9px;color:var(--text2);padding:2px 6px;border:1px solid var(--border);border-radius:2px}

/* MARKET SPINE */
.spine{background:var(--bg1);border-bottom:2px solid var(--border2);display:flex;align-items:stretch}
.sb{display:flex;flex-direction:column;justify-content:center;padding:0 14px;border-right:1px solid var(--border);flex-shrink:0}
.sb-lbl{font-size:8px;letter-spacing:1.5px;color:var(--text2);text-transform:uppercase;margin-bottom:1px}
.sb-val{font-family:var(--head);font-size:17px;font-weight:600;letter-spacing:.5px}
.sb-sub{font-size:9px;margin-top:1px}
.cpos{color:var(--green)}.cneg{color:var(--red)}.cflat{color:var(--text2)}
.sb-val.warn{color:var(--red)}.sb-val.amber{color:var(--amber)}
.sparkwrap{flex:1;display:flex;align-items:center;padding:4px 10px;border-right:1px solid var(--border);overflow:hidden;min-width:80px}
svg.spark{width:100%;height:38px}
.regime-wrap{display:flex;align-items:center;padding:0 16px;border-right:1px solid var(--border)}
.rpill{font-family:var(--head);font-size:11px;font-weight:700;letter-spacing:1.5px;padding:5px 12px;border-radius:2px}
.rh{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.rs{background:rgba(255,179,0,.1);color:var(--amber);border:1px solid rgba(255,179,0,.3)}
.rc{background:rgba(155,109,255,.1);color:var(--purple);border:1px solid rgba(155,109,255,.3)}
.rk{background:rgba(255,61,90,.1);color:var(--red);border:1px solid rgba(255,61,90,.4)}
.kill-btn{background:rgba(255,61,90,.08);border:1px solid var(--red2);color:var(--red);font-family:var(--head);font-size:9px;font-weight:700;letter-spacing:1.5px;padding:0 14px;cursor:pointer;text-transform:uppercase;transition:all .15s;white-space:nowrap;align-self:stretch}
.kill-btn:hover{background:rgba(255,61,90,.2)}

/* MAIN GRID */
.grid{display:grid;grid-template-columns:290px 1fr 260px 270px;grid-template-rows:1fr 1fr;gap:1px;background:var(--border);overflow:hidden;min-height:0}
.panel{background:var(--bg1);display:flex;flex-direction:column;overflow:hidden;min-height:0}
.ph{display:flex;align-items:center;padding:5px 10px;background:var(--bg2);border-bottom:1px solid var(--border);gap:7px;flex-shrink:0}
.pt{font-family:var(--head);font-size:9px;font-weight:700;letter-spacing:1.5px;color:var(--cyan);text-transform:uppercase}
.pbadge{font-size:8px;padding:1px 5px;border-radius:8px;background:var(--bg3);color:var(--text2);border:1px solid var(--border2)}
.pbadge.live{background:rgba(0,230,118,.08);color:var(--green);border-color:rgba(0,230,118,.2)}
.pbadge.amber{background:rgba(255,179,0,.08);color:var(--amber);border-color:rgba(255,179,0,.2)}
.pb{flex:1;overflow-y:auto;overflow-x:hidden;min-height:0}
.pb::-webkit-scrollbar{width:2px}.pb::-webkit-scrollbar-track{background:var(--bg1)}.pb::-webkit-scrollbar-thumb{background:var(--border2)}

/* panel spans */
.p-scanner{grid-row:1/3}
.p-pos{grid-column:2;grid-row:1}
.p-trace{grid-column:2;grid-row:2}
.p-shadow{grid-column:3;grid-row:1}
.p-history{grid-column:3;grid-row:2}
.p-log{grid-column:4;grid-row:1/3}

/* SCANNER */
.tbl{width:100%;border-collapse:collapse}
.tbl th{position:sticky;top:0;background:var(--bg2);color:var(--text2);font-size:8px;letter-spacing:1px;text-transform:uppercase;padding:4px 6px;text-align:right;border-bottom:1px solid var(--border);font-weight:400}
.tbl th:first-child{text-align:left}
.tbl td{padding:3px 6px;text-align:right;border-bottom:1px solid rgba(19,35,64,.5)}
.tbl td:first-child{text-align:left}
.tbl tr:hover td{background:var(--bg3)}
.tick-cell{font-weight:600;font-size:11px;color:var(--text0)}
.sec-tag{font-size:8px;color:var(--text2)}
.zbar{display:inline-block;height:7px;border-radius:1px;vertical-align:middle;margin-right:3px;opacity:.7}
.spill{font-size:8px;padding:1px 5px;border-radius:2px;font-weight:600;display:inline-block}
.sw{background:rgba(74,101,128,.15);color:var(--text2)}
.ssc{background:rgba(0,200,255,.1);color:var(--cyan);border:1px solid rgba(0,200,255,.2)}
.ssp{background:rgba(155,109,255,.1);color:var(--purple);border:1px solid rgba(155,109,255,.2)}
.ss{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.sb2{background:rgba(255,61,90,.07);color:var(--red)}
.sl{background:rgba(0,230,118,.18);color:var(--green);border:1px solid var(--green2);animation:pulse-g 2s infinite}

/* POSITIONS */
.pos-card{padding:8px 10px;border-bottom:1px solid var(--border)}
.pos-hd{display:flex;align-items:center;gap:7px;margin-bottom:5px}
.pos-tick{font-family:var(--head);font-size:13px;font-weight:700}
.dir-l{color:var(--green)}.dir-s{color:var(--red)}
.dbadge{font-size:8px;font-family:var(--head);font-weight:700;padding:1px 5px;border-radius:2px}
.dlong{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.dshort{background:rgba(255,61,90,.1);color:var(--red);border:1px solid rgba(255,61,90,.3)}
.ptimer{margin-left:auto;font-size:9px;color:var(--text2);font-variant-numeric:tabular-nums}
.pp{display:grid;grid-template-columns:repeat(4,1fr);gap:4px;margin-bottom:5px}
.pi .pi-l{font-size:8px;color:var(--text2);display:block}
.pi .pi-v{font-size:11px;color:var(--text1)}
.pi .pi-v.live{color:var(--text0);font-weight:600}
.pbar-wrap{background:var(--bg3);height:4px;border-radius:2px;overflow:hidden;margin-bottom:3px}
.pbar{height:100%;border-radius:2px;transition:width .5s}
.pbar.pos{background:linear-gradient(90deg,var(--green2),var(--green))}
.pbar.neg{background:linear-gradient(90deg,var(--red2),var(--red))}
.pft{display:flex;gap:10px;align-items:center}
.pmeta{font-size:9px;color:var(--text2)}.pmeta span{color:var(--text1)}
.ppnl{margin-left:auto;font-size:11px;font-weight:600}
.cost-tip{font-size:8px;color:var(--text2);margin-left:4px}

/* SIGNAL TRACE */
.tc{padding:7px 10px;border-bottom:1px solid var(--border)}
.tc-hd{display:flex;align-items:center;gap:7px;margin-bottom:5px}
.tc-tick{font-family:var(--head);font-size:12px;font-weight:700}
.tc-time{margin-left:auto;font-size:9px;color:var(--text2)}
.gates{display:flex;gap:3px;flex-wrap:wrap;margin-bottom:4px}
.gate{display:flex;align-items:center;gap:3px;font-size:8px;padding:2px 5px;border-radius:2px;border:1px solid transparent}
.gp{background:rgba(0,230,118,.05);color:var(--green);border-color:rgba(0,230,118,.2)}
.gf{background:rgba(255,61,90,.05);color:var(--red);border-color:rgba(255,61,90,.2)}
.gc{color:var(--amber);border-color:rgba(255,179,0,.2);background:rgba(255,179,0,.05)}
.gval{opacity:.65;font-size:7px}
.tc-metrics{display:flex;gap:10px}
.tc-m{font-size:9px;color:var(--text2)}.tc-m span{color:var(--text1)}
.reason-row{font-size:9px;color:var(--text2);margin-top:3px;line-height:1.4}
.reason-row span{color:var(--text1)}

/* SHADOW */
.sh-row{display:flex;align-items:center;padding:3px 10px;border-bottom:1px solid rgba(19,35,64,.4);gap:5px}
.sh-row:hover{background:var(--bg2)}
.sh-rank{color:var(--text2);font-size:9px;width:18px;text-align:right}
.sh-name{font-size:10px;color:var(--text1);flex:1}
.sh-live{font-size:7px;font-family:var(--head);font-weight:700;padding:1px 4px;border-radius:2px;background:rgba(0,230,118,.15);color:var(--green);border:1px solid rgba(0,230,118,.3)}
.sh-bar-wrap{width:52px;height:4px;background:var(--bg3);border-radius:1px;overflow:hidden}
.sh-bar{height:100%;border-radius:1px}
.sh-pnl{font-size:10px;width:55px;text-align:right}
.sh-fills{font-size:9px;color:var(--text2);width:32px;text-align:right}

/* TRADE HISTORY */
.tr-card{padding:6px 10px;border-bottom:1px solid var(--border);cursor:pointer}
.tr-card:hover{background:var(--bg2)}
.tr-top{display:flex;align-items:center;gap:7px;margin-bottom:3px}
.tr-tick{font-size:11px;font-weight:600;color:var(--text1)}
.tr-time{font-size:8px;color:var(--text2)}
.tr-prices{display:flex;gap:10px;margin-bottom:2px}
.tr-p{font-size:9px;color:var(--text2)}.tr-p span{color:var(--text1)}
.tr-footer{display:flex;gap:8px;align-items:center}
.tr-reason{font-size:8px;color:var(--text2)}
.tr-cost{font-size:8px;color:var(--text2)}
.tr-pnl{margin-left:auto;font-size:11px;font-weight:600}

/* NARRATIVE PANEL */
.narr-overlay{display:none;position:fixed;inset:0;background:rgba(2,8,16,.85);z-index:1000;align-items:center;justify-content:center}
.narr-overlay.open{display:flex}
.narr-box{background:var(--bg2);border:1px solid var(--border2);border-radius:4px;width:520px;max-height:80vh;overflow-y:auto;padding:20px}
.narr-close{float:right;cursor:pointer;color:var(--text2);font-size:14px}
.narr-close:hover{color:var(--text0)}
.narr-title{font-family:var(--head);font-size:13px;color:var(--cyan);margin-bottom:12px}
.narr-section{margin-bottom:12px}
.narr-label{font-size:8px;letter-spacing:1.5px;color:var(--text2);text-transform:uppercase;margin-bottom:4px}
.narr-text{font-size:10px;color:var(--text1);line-height:1.6;background:var(--bg3);padding:8px;border-radius:2px;border-left:2px solid var(--cyan2)}
.cost-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px}
.cost-item{font-size:9px;color:var(--text2);padding:4px 8px;background:var(--bg3);border-radius:2px}
.cost-item span{color:var(--text1);float:right}

/* LOG */
.log-wrap{padding:2px 0}
.log-line{display:flex;gap:5px;padding:2px 8px;border-bottom:1px solid rgba(19,35,64,.3);align-items:flex-start}
.log-line:hover{background:var(--bg2)}
.log-ts{color:var(--text2);font-size:9px;flex:0 0 44px;white-space:nowrap}
.log-tag{font-size:7px;font-weight:700;padding:1px 4px;border-radius:2px;flex-shrink:0;font-family:var(--head);letter-spacing:.5px}
.t-eng{background:rgba(0,200,255,.1);color:var(--cyan)}
.t-risk{background:rgba(155,109,255,.1);color:var(--purple)}
.t-sig{background:rgba(0,230,118,.1);color:var(--green)}
.t-trd{background:rgba(255,179,0,.1);color:var(--amber)}
.t-wrn{background:rgba(255,61,90,.1);color:var(--red)}
.t-dat{background:rgba(74,101,128,.1);color:var(--text2)}
.log-msg{font-size:9px;color:var(--text1);line-height:1.4}
.log-msg .hi{color:var(--text0);font-weight:500}
.log-msg .pos{color:var(--green)}.log-msg .neg{color:var(--red)}

/* STATUS BAR */
.statusbar{background:var(--bg2);border-top:1px solid var(--border2);display:flex;align-items:center;padding:0 10px;gap:16px}
.si{font-size:9px;color:var(--text2);display:flex;gap:4px;white-space:nowrap}
.si span{color:var(--text1)}.si .vg{color:var(--green)}.si .vr{color:var(--red)}

/* EMPTY */
.empty{color:var(--text2);font-size:10px;padding:16px;text-align:center}
::-webkit-scrollbar{width:2px;height:2px}::-webkit-scrollbar-track{background:var(--bg1)}::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
</style>
</head>
<body>
<div class="root">

<!-- HEADER -->
<div class="header">
  <div class="logo"><span class="bolt">⚡</span>KUBER'S CALLING</div>
  <div class="hdiv"></div>
  <div class="token-wrap">
    <div class="lbl">JWT TOKEN</div>
    <input class="token-input" id="tokenInput" type="password" placeholder="Paste today's INDmoney token here…">
    <button class="btn" onclick="saveToken()">CONNECT</button>
  </div>
  <div id="apiDot" class="api-dot dead"></div>
  <span class="api-txt" id="apiTxt">NOT CONNECTED</span>
  <div class="hright">
    <div class="cycle">CYC <span id="cycleNum">0</span></div>
    <div class="pnl pnl-pos" id="pnlDisplay">₹0</div>
    <div class="clock" id="clock">--:--:--</div>
  </div>
</div>

<!-- MARKET SPINE -->
<div class="spine">
  <div class="sb">
    <div class="sb-lbl">NIFTY 50</div>
    <div class="sb-val" id="niftyVal">--</div>
    <div class="sb-sub cflat" id="niftyChg">--</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">INDIA VIX</div>
    <div class="sb-val amber" id="vixVal">--</div>
    <div class="sb-sub cflat" id="vixStatus">--</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">NIFTY ATR 15m</div>
    <div class="sb-val" id="atrVal">--</div>
    <div class="sb-sub cflat">ROLLING 10</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">NIFTY ΔOpen</div>
    <div class="sb-val cflat" id="openChg">--</div>
    <div class="sb-sub cflat" id="openBias">--</div>
  </div>
  <div class="sparkwrap">
    <svg class="spark" id="sparkSvg" viewBox="0 0 220 38" preserveAspectRatio="none">
      <defs>
        <linearGradient id="sg" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#00C8FF" stop-opacity=".2"/>
          <stop offset="100%" stop-color="#00C8FF" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path id="sparkArea" fill="url(#sg)" d=""/>
      <path id="sparkLine" fill="none" stroke="#00C8FF" stroke-width="1.2" d=""/>
    </svg>
  </div>
  <div class="regime-wrap">
    <div class="rpill rs" id="regimePill">INITIALISING</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">SIGNALS</div>
    <div class="sb-val" style="color:var(--cyan)" id="sigCount">0</div>
    <div class="sb-sub cflat">TODAY</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">OPEN POS</div>
    <div class="sb-val" id="posCount">0</div>
    <div class="sb-sub cflat" id="equityDisplay">--</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">DEPLOYED</div>
    <div class="sb-val" id="deployedSpine">₹0</div>
    <div class="sb-sub cflat">CAPITAL IN PLAY</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">SESSION P&amp;L</div>
    <div class="sb-val" id="pnlSpine">₹0</div>
    <div class="sb-sub cflat">REALISED TODAY</div>
  </div>
  <div class="sb">
    <div class="sb-lbl">CHURN</div>
    <div class="sb-val" style="color:var(--cyan)" id="churnSpine">0</div>
    <div class="sb-sub cflat">TRADES CLOSED</div>
  </div>
  <button class="kill-btn" onclick="killSwitch()">🔴 KILL</button>
  <button class="kill-btn" style="background:rgba(0,176,80,.08);border-color:var(--green2);color:var(--green)" onclick="openConfig()">⚙ CONFIG</button>
</div>

<!-- CONFIG MODAL -->
<div id="cfgModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.7);z-index:9999;align-items:center;justify-content:center">
  <div style="background:var(--bg2);border:1px solid var(--border);border-radius:4px;padding:24px;width:340px;font-family:var(--mono)">
    <div style="font-family:var(--head);font-size:11px;letter-spacing:2px;color:var(--cyan);margin-bottom:16px">⚙ LIVE CONFIG — takes effect next cycle</div>
    <div style="display:grid;gap:12px">
      <label style="font-size:9px;color:var(--text2)">SLOT SIZE — capital per position (₹)
        <input id="cfgPerStock" type="number" step="5000" style="width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text1);padding:6px;margin-top:4px;font-family:var(--mono);font-size:11px">
        <span style="font-size:8px;color:var(--text2);margin-top:2px;display:block">Current: 3 slots × slot size = total deployed max</span>
      </label>
      <label style="font-size:9px;color:var(--text2)">MAX OPEN POSITIONS (slots)
        <input id="cfgSlots" type="number" step="1" min="1" max="10" style="width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text1);padding:6px;margin-top:4px;font-family:var(--mono);font-size:11px">
        <span style="font-size:8px;color:var(--text2);margin-top:2px;display:block">Max 1 per sector within these slots</span>
      </label>
      <label style="font-size:9px;color:var(--text2)">TARGET+ TRAILING STOP (% of peak profit)
        <input id="cfgTrailing" type="number" step="1" min="1" max="50" style="width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text1);padding:6px;margin-top:4px;font-family:var(--mono);font-size:11px">
        <span style="font-size:8px;color:var(--text2);margin-top:2px;display:block">Exit if profit drops this % below peak. 10 = exit at 90% of peak</span>
      </label>
      <label style="font-size:9px;color:var(--text2)">EQUITY FLOOR / KILL SWITCH (₹)
        <input id="cfgFloor" type="number" step="1000" style="width:100%;background:var(--bg3);border:1px solid var(--border);color:var(--text1);padding:6px;margin-top:4px;font-family:var(--mono);font-size:11px">
        <span style="font-size:8px;color:var(--text2);margin-top:2px;display:block">All positions closed if equity drops below this</span>
      </label>
    </div>
    <div style="display:flex;gap:8px;margin-top:16px">
      <button onclick="saveConfig()" style="flex:1;background:var(--green2);border:none;color:var(--bg1);font-family:var(--head);font-size:9px;letter-spacing:1.5px;padding:8px;cursor:pointer">SAVE</button>
      <button onclick="closeConfig()" style="flex:1;background:var(--bg3);border:1px solid var(--border);color:var(--text2);font-family:var(--head);font-size:9px;letter-spacing:1.5px;padding:8px;cursor:pointer">CANCEL</button>
    </div>
    <div id="cfgStatus" style="font-size:9px;color:var(--green);margin-top:8px;min-height:14px"></div>
  </div>
<!-- MAIN GRID -->
</div> <div class="grid">


  <!-- SCANNER -->
  <div class="panel p-scanner">
    <div class="ph">
      <div class="pt">Universe Scanner</div>
      <div class="pbadge live" id="scanBadge">-- TICKERS</div>
      <div class="pbadge amber" style="margin-left:auto" id="regimeBadge">STANDBY</div>
    </div>
    <div class="pb">
      <table class="tbl">
        <thead><tr>
          <th>Ticker</th><th>Vol Z</th><th>Vel</th><th>Lag%</th><th>Status</th>
        </tr></thead>
        <tbody id="scanBody"></tbody>
      </table>
    </div>
  </div>

  <!-- POSITIONS -->
  <div class="panel p-pos">
    <div class="ph">
      <div class="pt">Live Positions</div>
      <div class="pbadge live" id="posBadge">0 OPEN</div>
      <div style="margin-left:auto;font-size:9px;color:var(--text2)">
        DEPLOYED <span id="deployed" style="color:var(--text1)">₹0</span>
      </div>
    </div>
    <div class="pb" id="posBody">
      <div class="empty">No open positions</div>
    </div>
  </div>

  <!-- SHADOW LEADERBOARD -->
  <div class="panel p-shadow">
    <div class="ph">
      <div class="pt">Shadow Book</div>
      <div class="pbadge">31 STRATS</div>
    </div>
    <div class="pb" id="shadowBody"></div>
  </div>

  <!-- LOG -->
  <div class="panel p-log">
    <div class="ph">
      <div class="pt">Engine Log</div>
      <div class="pbadge live"><span class="blink">●</span> LIVE</div>
    </div>
    <div class="pb" id="logBody"></div>
  </div>

  <!-- SIGNAL TRACE -->
  <div class="panel p-trace">
    <div class="ph">
      <div class="pt">Signal Trace</div>
      <div class="pbadge" id="traceBadge">--</div>
    </div>
    <div class="pb" id="traceBody"></div>
  </div>

  <!-- TRADE HISTORY -->
  <div class="panel p-history">
    <div class="ph">
      <div class="pt">Trade History</div>
      <div class="pbadge">TODAY · click for details</div>
    </div>
    <div class="pb" id="histBody"></div>
  </div>

</div>

<!-- STATUS BAR -->
<div class="statusbar">
  <div class="si">ENGINE <span class="vg" id="sbEngine">STARTING</span></div>
  <div class="si">DB <span class="vg" id="sbDb">--</span></div>
  <div class="si">3m CANDLES <span id="sb3m">--</span></div>
  <div class="si">15m CANDLES <span id="sb15m">--</span></div>
  <div class="si">VOL PROFILE <span id="sbVp">--</span></div>
  <div class="si">INTERVAL <span>2.5s</span></div>
  <div class="si" style="margin-left:auto">EOD <span>15:20</span></div>
  <div class="si">HARD STOP <span>+30m</span></div>
  <div class="si">EQUITY <span id="sbEquity">--</span></div>
  <div class="si">FLOOR <span>₹95,000</span></div>
</div>

</div>

<!-- TRADE DETAIL OVERLAY -->
<div class="narr-overlay" id="narrativeOverlay" onclick="closeNarrative(event)">
  <div class="narr-box" id="narrativeBox">
    <span class="narr-close" onclick="closeNarrative()">✕</span>
    <div class="narr-title" id="narrTitle">Trade Detail</div>
    <div class="narr-section">
      <div class="narr-label">Why Entered</div>
      <div class="narr-text" id="narrEntry">--</div>
    </div>
    <div class="narr-section">
      <div class="narr-label">Why Exited</div>
      <div class="narr-text" id="narrExit">--</div>
    </div>
    <div class="narr-section">
      <div class="narr-label">Transaction Cost Breakdown</div>
      <div class="cost-grid" id="narrCosts"></div>
    </div>
  </div>
</div>

<script>
const R = 2500; // refresh ms
let sparkPts = [];
let logLines = [];

// ── SPARKLINE ──────────────────────────────────────────
function buildSpark(pts) {
  if(pts.length < 2) return;
  const W=220,H=38;
  const mn=Math.min(...pts), mx=Math.max(...pts), range=mx-mn||1;
  const c = pts.map((v,i)=>[i/(pts.length-1)*W, H-((v-mn)/range*(H-4))-2]);
  const ln = 'M'+c.map(p=>p[0].toFixed(1)+','+p[1].toFixed(1)).join('L');
  document.getElementById('sparkLine').setAttribute('d',ln);
  document.getElementById('sparkArea').setAttribute('d',ln+`L${W},${H}L0,${H}Z`);
}

// ── CLOCK ──────────────────────────────────────────────
function tickClock(){
  document.getElementById('clock').textContent = new Date().toTimeString().slice(0,8);
}

// ── TOKEN ──────────────────────────────────────────────
function saveToken(){
  const t = document.getElementById('tokenInput').value.trim();
  if(!t) return;
  fetch('/api/token',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:t})})
  .then(r=>r.json()).then(d=>{
    const dot=document.getElementById('apiDot'), txt=document.getElementById('apiTxt');
    if(d.ok && d.market_open){
      // Token valid, market data flowing
      dot.className='api-dot';
      txt.textContent='CONNECTED · LIVE';
    } else if(d.ok && !d.market_open){
      // Token valid but market not open yet — this is normal before 9:15
      dot.className='api-dot';
      dot.style.background='var(--amber)';
      dot.style.animation='none';
      txt.textContent='TOKEN OK · PRE-MARKET';
    } else {
      // Token rejected (401/403) or network error
      dot.className='api-dot dead';
      txt.textContent=d.detail||'CONN FAILED';
    }
    const btn=document.querySelector('.btn');
    if(d.ok){
      btn.textContent='✓ SAVED'; btn.style.background='var(--green2)';
    } else {
      btn.textContent='✗ FAILED'; btn.style.background='var(--red2)';
    }
    setTimeout(()=>{btn.textContent='CONNECT';btn.style.background='';btn.style.color='';},3000);
  }).catch(e=>{
    document.getElementById('apiTxt').textContent='NETWORK ERROR';
    document.getElementById('apiDot').className='api-dot dead';
  });
}

// ── KILL SWITCH ────────────────────────────────────────
function killSwitch(){
  if(!confirm('ACTIVATE KILL SWITCH?\n\nHalts all new entries immediately.\nOpen positions run their normal exit rules.')) return;
  fetch('/api/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kill_switch:true})});
  document.getElementById('regimePill').textContent='KILL SWITCH';
  document.getElementById('regimePill').className='rpill rk';
}

// ── LIVE CONFIG ────────────────────────────────────────
function openConfig(){
  fetch('/api/config').then(r=>r.json()).then(cfg=>{
    document.getElementById('cfgPerStock').value = cfg.per_stock_limit || 20000;
    document.getElementById('cfgSlots').value    = cfg.max_open_positions || 5;
    document.getElementById('cfgTrailing').value = (cfg.trailing_profit_pct || 0.10) * 100;
    document.getElementById('cfgFloor').value    = cfg.equity_floor    || 95000;
    document.getElementById('cfgStatus').textContent = '';
    document.getElementById('cfgModal').style.display='flex';
  });
}
function closeConfig(){
  document.getElementById('cfgModal').style.display='none';
}
function saveConfig(){
  const ps   = parseFloat(document.getElementById('cfgPerStock').value);
  const slots= parseInt(document.getElementById('cfgSlots').value);
  const tr   = parseFloat(document.getElementById('cfgTrailing').value);
  const fl   = parseFloat(document.getElementById('cfgFloor').value);
  if(isNaN(ps)||isNaN(slots)||isNaN(tr)||isNaN(fl)){
    document.getElementById('cfgStatus').textContent='Invalid values';
    document.getElementById('cfgStatus').style.color='var(--red)';
    return;
  }
  if(slots<1||slots>10){ document.getElementById('cfgStatus').textContent='Slots must be 1-10'; document.getElementById('cfgStatus').style.color='var(--red)'; return; }
  if(tr<1||tr>50){ document.getElementById('cfgStatus').textContent='Trailing % must be 1-50'; document.getElementById('cfgStatus').style.color='var(--red)'; return; }
  fetch('/api/config',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify({per_stock_limit:ps, max_open_positions:slots, trailing_profit_pct:tr/100, equity_floor:fl})
  }).then(r=>r.json()).then(d=>{
    if(d.ok){
      document.getElementById('cfgStatus').textContent='✓ Saved — takes effect next cycle';
      document.getElementById('cfgStatus').style.color='var(--green)';
      setTimeout(closeConfig, 1500);
    }
  });
}
document.getElementById('cfgModal').addEventListener('click', e=>{
  if(e.target===document.getElementById('cfgModal')) closeConfig();
});

// ── NARRATIVE OVERLAY ──────────────────────────────────
let _lastTrade = null;
function openNarrative(trade){
  _lastTrade = trade;
  document.getElementById('narrTitle').textContent = `${trade.ticker} ${trade.direction} — ${trade.entry_time||''}`;
  document.getElementById('narrEntry').textContent = trade.entry_narrative || 'No entry narrative recorded.';
  document.getElementById('narrExit').textContent = trade.exit_narrative || `Exit reason: ${trade.exit_reason||'--'}`;
  const pnl = (trade.net_pnl||0);
  const costs = [
    ['Brokerage (2 legs)', trade.cost_brokerage],
    ['STT (sell side)', trade.cost_stt],
    ['Exchange Charge', trade.cost_exchange],
    ['SEBI Levy', trade.cost_sebi],
    ['Stamp Duty', trade.cost_stamp],
    ['GST 18%', trade.cost_gst],
    ['Total Cost', trade.cost_total],
    ['Net P&L', pnl],
  ];
  document.getElementById('narrCosts').innerHTML = costs.map(([k,v])=>
    `<div class="cost-item">${k}<span style="color:${k==='Net P&L'?(pnl>=0?'var(--green)':'var(--red)'):'var(--text1)'}">${v!=null?'₹'+(+v).toFixed(2):'--'}</span></div>`
  ).join('');
  document.getElementById('narrativeOverlay').classList.add('open');
}
function closeNarrative(e){
  if(!e || e.target===document.getElementById('narrativeOverlay') || e.target.classList.contains('narr-close')){
    document.getElementById('narrativeOverlay').classList.remove('open');
  }
}
document.addEventListener('keydown',e=>{ if(e.key==='Escape') closeNarrative(); });

// ── FORMAT HELPERS ─────────────────────────────────────
function fmt(v,d=0){ return v!=null?Number(v).toFixed(d):'--'; }
function inr(v){ return v!=null?'₹'+Math.abs(+v).toFixed(0):'--'; }
function sign(v){ return v>=0?'+':'-'; }
function pnlCls(v){ return (+v)>=0?'pnl-pos':'pnl-neg'; }

// ── STATE ──────────────────────────────────────────────
function fetchState(){
  fetch('/api/state').then(r=>r.json()).then(s=>{
    const n=s.nifty||0, v=s.vix||0, a=s.nifty_atr||0;
    const chg=s.nifty_open_change||0;

    // Nifty
    document.getElementById('niftyVal').textContent = n>0?fmt(n,2):'⚠ 0';
    document.getElementById('niftyVal').className = 'sb-val'+(n<=0?' warn':'');
    const chgStr = n>0?(chg>=0?'▲ +':'▼ ')+fmt(chg*100,2)+'%':'pre-market';
    document.getElementById('niftyChg').textContent = chgStr;
    document.getElementById('niftyChg').className = 'sb-sub '+(chg>=0?'cpos':'cneg');

    // VIX
    document.getElementById('vixVal').textContent = v>0?fmt(v,2):'⚠ 0';
    document.getElementById('vixVal').className = 'sb-val'+(v<=0?' warn':' amber');
    document.getElementById('vixStatus').textContent = v>0?(v<11?'TOO LOW':v>28?'TOO HIGH':'OK'):'--';

    // ATR
    document.getElementById('atrVal').textContent = a>0?fmt(a,1):'⚠ 0';
    document.getElementById('atrVal').className = 'sb-val'+(a<=0?' warn':'');

    // Open change
    const oc=chg*100;
    document.getElementById('openChg').textContent = oc!=0?(oc>=0?'+':'')+oc.toFixed(2)+'%':'--';
    document.getElementById('openChg').className = 'sb-val '+(oc>=0?'cpos':'cneg');
    document.getElementById('openBias').textContent = Math.abs(oc)>0.8?(oc>0?'SHORT CAUTION':'LONG CAUTION'):'NEUTRAL';

    // Sparkline
    if(n>0){ sparkPts.push(n); if(sparkPts.length>80) sparkPts.shift(); buildSpark(sparkPts); }

    // Regime
    const rg=s.regime||'STANDBY';
    const rp=document.getElementById('regimePill');
    rp.textContent=rg;
    rp.className='rpill '+(rg==='HEALTHY'?'rh':rg==='KILL_SWITCH'?'rk':rg.includes('CAUTION')?'rc':'rs');
    document.getElementById('regimeBadge').textContent=rg;
    document.getElementById('regimeBadge').className='pbadge '+(rg==='HEALTHY'?'live':rg.includes('CAUTION')?'amber':'');
    document.getElementById('scanBadge').textContent=(s.tickers_count||'--')+' TICKERS';

    // Counters
    document.getElementById('cycleNum').textContent=s.cycle||0;
    document.getElementById('sigCount').textContent=s.signals_today||0;
    const pos=s.positions||[];
    document.getElementById('posCount').textContent=pos.length;
    document.getElementById('posBadge').textContent=pos.length+' OPEN';

    // P&L
    const pnl=s.session_pnl||0;
    const pe=document.getElementById('pnlDisplay');
    pe.textContent=(pnl>=0?'+':'')+inr(pnl);
    pe.className='pnl '+(pnl>=0?'pnl-pos':'pnl-neg');

    // Equity — correct key is equity_floor not equity
    const eq=s.live_config?.equity_floor||s.current_equity||0;
    document.getElementById('sbEquity').textContent=eq?inr(eq):'--';
    document.getElementById('sbEquity').className=eq&&eq<95000?'vr':'vg';
    document.getElementById('equityDisplay').textContent=eq?inr(eq):'--';

    // Deployed capital in spine
    const dep = s.deployed_capital || 0;
    document.getElementById('deployedSpine').textContent = dep > 0 ? inr(dep) : '₹0';

    // Session P&L in spine
    const spnl = s.session_pnl || 0;
    const spnlEl = document.getElementById('pnlSpine');
    spnlEl.textContent = (spnl >= 0 ? '+' : '') + inr(spnl);
    spnlEl.className = 'sb-val ' + (spnl >= 0 ? 'pnl-pos' : 'pnl-neg');

    // Churn — completed trades today
    document.getElementById('churnSpine').textContent = s.trades_today || 0;

    // Status bar
    document.getElementById('sbEngine').textContent=s.running?'RUNNING':'STOPPED';
    document.getElementById('sbEngine').className=s.running?'vg':'vr';

    // Positions
    renderPositions(pos);

    // Scanner
    renderScanner(s.decision_log||[]);

  }).catch(()=>{});
}

// ── POSITIONS ──────────────────────────────────────────
function renderPositions(positions){
  const body=document.getElementById('posBody');
  if(!positions.length){ body.innerHTML='<div class="empty">No open positions</div>'; return; }
  let deployed=0;
  let tot=0; positions.forEach(p=>{tot+=(p.current_price-p.entry_price)*p.qty*(p.direction==='LONG'?1:-1);}); const tc=tot>=0?'#00c878':'#ff4d4d'; body.innerHTML='<div style="padding:6px 10px;border-bottom:1px solid #333;margin-bottom:6px;display:flex;align-items:center;gap:8px"><span style="color:#888;font-size:10px">OPEN P&L</span><span style="font-size:18px;font-weight:700;color:'+tc+'">'+(tot>=0?'+':'')+'₹'+Math.abs(tot).toFixed(0)+'</span><span style="color:#888;font-size:10px;margin-left:auto">'+positions.length+' open</span></div>'+positions.map(p=>{
    const pnl=(p.current_price-p.entry_price)*p.qty*(p.direction==='LONG'?1:-1);
    const prog=Math.max(0,Math.min(100,(p.current_price-p.entry_price)/(p.target_price-p.entry_price)*100));
    const dirc=p.direction==='LONG'?'dlong':'dshort';
    deployed+=p.entry_price*p.qty;
    const isTrailing=p.trailing_active;
    const isClosing=p.closing;
    const statusBadge=isClosing
      ? '<span style="font-size:8px;font-family:var(--head);font-weight:700;padding:1px 6px;border-radius:2px;background:rgba(255,179,0,.15);color:var(--amber);border:1px solid rgba(255,179,0,.4);" class="blink">CLOSING...</span>'
      : isTrailing
      ? '<span style="font-size:8px;font-family:var(--head);font-weight:700;padding:1px 6px;border-radius:2px;background:rgba(0,230,118,.2);color:var(--green);border:1px solid rgba(0,230,118,.5);" class="blink">TARGET+</span>'
      : '';
    return `<div class="pos-card" style="${isClosing?'opacity:0.7;':''}${isTrailing?'border-left:2px solid var(--green);':''}">
      <div class="pos-hd">
        <div class="pos-tick ${p.direction==='LONG'?'dir-l':'dir-s'}">${p.ticker}</div>
        <span class="dbadge ${dirc}">${p.direction}</span>
        ${statusBadge}
        <span style="font-size:9px;color:var(--text2)">${p.strategy_name||'RULE_V1'}</span>
        <div class="ptimer">⏱ ${p.hold_minutes||0}m <span class="blink" style="color:var(--amber)">●</span></div>
      </div>
      <div class="pp">
        <div class="pi"><span class="pi-l">ENTRY</span><span class="pi-v">₹${fmt(p.entry_price,2)}</span></div>
        <div class="pi"><span class="pi-l">LIVE</span><span class="pi-v live">₹${fmt(p.current_price||p.entry_price,2)}</span></div>
        <div class="pi"><span class="pi-l">SL</span><span class="pi-v" style="color:var(--red)">₹${fmt(p.sl_price,2)}</span></div>
        <div class="pi"><span class="pi-l">TARGET</span><span class="pi-v" style="color:var(--green)">₹${fmt(p.target_price,2)}</span></div>
      </div>
      <div class="pbar-wrap"><div class="pbar ${pnl>=0?'pos':'neg'}" style="width:${Math.abs(prog)}%"></div></div>
      <div class="pft">
        <div class="pmeta">QTY <span>${p.qty}</span></div>
        <div class="pmeta">PROG <span>${Math.abs(prog).toFixed(0)}%</span></div>
        <div class="pmeta">SEC <span>${p.sector||'--'}</span></div>
        <div class="ppnl ${pnl>=0?'pnl-pos':'pnl-neg'}">${pnl>=0?'+':''}₹${Math.abs(pnl).toFixed(0)}</div>
      </div>
    </div>`;
  }).join('');
  document.getElementById('deployed').textContent=inr(deployed);
}

// ── SCANNER ────────────────────────────────────────────
function renderScanner(decisions){
  const body=document.getElementById('scanBody');
  if(!decisions.length){ body.innerHTML='<tr><td colspan="5" class="empty">Waiting for cycle…</td></tr>'; return; }
  const sorted=[...decisions].sort((a,b)=>{
    const o={'LIVE':0,'SIGNAL':1,'SPY':2,'SCOUT':3,'BLOCKED':4,'PASS':5};
    const td=(o[a.status_tier||'PASS']||5)-(o[b.status_tier||'PASS']||5); if(td!==0) return td; return (+(b.z||0))-(+(a.z||0));
  });
  body.innerHTML=sorted.map(d=>{
    const z=+(d.z||0), vel=+(d.vel||0), lag=+(d.lag||0)*100;
    const sig=d.signal;
    let scls='sw', slbl='WATCH';
    if(sig==='BUY'||sig==='SELL'){ scls='sl'; slbl='LIVE'; }
    else if(d.risk_reason&&d.risk_reason!=='APPROVED'){ scls='sb2'; slbl='BLOCKED'; }
    else if(d.reason&&d.reason.includes('Spy')===false&&d.reason.includes('Scout')===false&&sig==='PASS'){ scls='sw'; slbl='WATCH'; }
    const zw=Math.min(z/10*28,28);
    return `<tr>
      <td><div class="tick-cell">${d.ticker}</div><div class="sec-tag">${d.sector||''}</div></td>
      <td><span class="zbar" style="width:${zw}px;background:${z>=2?'var(--cyan)':'var(--border2)'}"></span><span style="color:${z>=2?'var(--cyan)':'var(--text2)'}">${fmt(z,2)}</span></td>
      <td style="color:${vel>=1?'var(--text1)':'var(--text2)'}">${fmt(vel,2)}</td>
      <td style="color:${Math.abs(lag)>=0.2?'var(--green)':'var(--text2)'}">${lag>=0?'+':''}${fmt(lag,3)}%</td>
      <td><span class="spill ${scls}">${slbl}</span></td>
    </tr>`;
  }).join('');
}

// ── SIGNAL TRACE ────────────────────────────────────────
function fetchTrace(){
  fetch('/api/signal_trace?n=8').then(r=>r.json()).then(signals=>{
    document.getElementById('traceBadge').textContent='LAST '+signals.length+' SIGNALS';
    const body=document.getElementById('traceBody');
    if(!signals.length){ body.innerHTML='<div class="empty">No signals yet</div>'; return; }
    body.innerHTML=signals.map(s=>{
      const gates=(s.gate_trace||[]).map(g=>{
        const cls=g.passed===false?'gf':g.passed===true?'gp':'gc';
        const icon=g.passed===false?'✗':g.passed===true?'✓':'⏳';
        return `<span class="gate ${cls}">${icon} ${g.gate} <span class="gval">${g.value||''}</span></span>`;
      }).join('');
      const dirc=s.direction==='LONG'?'dir-l':s.direction==='SHORT'?'dir-s':'';
      const filled=s.disposition==='LIVE';
      return `<div class="tc">
        <div class="tc-hd">
          <div class="tc-tick ${dirc}">${s.ticker}</div>
          ${s.direction?`<span class="dbadge ${s.direction==='LONG'?'dlong':'dshort'}">${s.direction}</span>`:''}
          ${filled?'<span style="color:var(--green);font-size:9px">✓ FILLED</span>':'<span style="color:var(--red);font-size:9px">✗ '+s.disposition+'</span>'}
          <div class="tc-time">${(s.timestamp||'').slice(11,16)}</div>
        </div>
        <div class="gates">${gates}</div>
        <div class="tc-metrics">
          <div class="tc-m">Z:<span>${fmt(s.vol_z_score,2)}</span></div>
          <div class="tc-m">Vel:<span>${fmt(s.velocity_ratio,2)}</span></div>
          <div class="tc-m">Lag:<span>${s.sector_lag_pct!=null?(+s.sector_lag_pct*100).toFixed(3)+'%':'--'}</span></div>
          <div class="tc-m">VIX:<span>${fmt(s.vix,1)}</span></div>
          <div class="tc-m">ATR:<span>${fmt(s.atr_15m,1)}</span></div>
        </div>
        ${s.entry_reason?`<div class="reason-row"><span>${s.entry_reason}</span></div>`:''}
      </div>`;
    }).join('');
  }).catch(()=>{});
}

// ── SHADOW LEADERBOARD ─────────────────────────────────
function fetchShadow(){
  fetch('/api/shadow_leaderboard').then(r=>r.json()).then(rows=>{
    const body=document.getElementById('shadowBody');
    if(!rows.length){ body.innerHTML='<div class="empty">No shadow data yet</div>'; return; }
    const maxAbs=Math.max(...rows.map(r=>Math.abs(r.pnl||0)))||1;
    const medals=['🥇','🥈','🥉'];
    body.innerHTML=rows.map((r,i)=>{
      const pnl=+(r.pnl||0), pct=Math.abs(pnl)/maxAbs*100;
      const isLive=r.strategy_name==='RULE_V1';
      return `<div class="sh-row">
        <div class="sh-rank">${medals[i]||i+1}</div>
        <div class="sh-name">${r.strategy_name} ${isLive?'<span class="sh-live">LIVE</span>':''}</div>
        <div class="sh-fills">${r.fills||0}f</div>
        <div class="sh-bar-wrap"><div class="sh-bar" style="width:${pct}%;background:${pnl>=0?'var(--green2)':'var(--red2)'}"></div></div>
        <div class="sh-pnl" style="color:${pnl>=0?'var(--green)':'var(--red)'}">${pnl>=0?'+':''}₹${Math.abs(pnl).toFixed(0)}</div>
      </div>`;
    }).join('');
  }).catch(()=>{});
}

// ── TRADE HISTORY ──────────────────────────────────────
function fetchHistory(){
  fetch('/api/history').then(r=>r.json()).then(trades=>{
    const body=document.getElementById('histBody');
    if(!trades.length){ body.innerHTML='<div class="empty">No trades yet</div>'; return; }
    body.innerHTML=trades.map(t=>{
      const net=+(t.net_pnl||0), cost=+(t.cost_total||0);
      const dirc=t.direction==='LONG'?'dlong':'dshort';
      return `<div class="tr-card" onclick='openNarrative(${JSON.stringify(t)})'>
        <div class="tr-top">
          <div class="tr-tick">${t.ticker}</div>
          <span class="dbadge ${dirc}">${t.direction}</span>
          <div class="tr-time">${(t.entry_time||'').slice(11,16)}</div>
          <div class="tr-time" style="margin-left:4px">→${(t.exit_time||'').slice(11,16)}</div>
        </div>
        <div class="tr-prices">
          <div class="tr-p">IN <span>₹${fmt(t.entry_price,2)}</span></div>
          <div class="tr-p">OUT <span>₹${fmt(t.exit_price,2)}</span></div>
          <div class="tr-p">QTY <span>${t.qty}</span></div>
          <div class="tr-p">HELD <span>${fmt(t.hold_minutes,0)}m</span></div>
        </div>
        <div class="tr-footer">
          <div class="tr-reason">${t.exit_reason||'--'}</div>
          <div class="tr-cost">cost ₹${cost.toFixed(2)}</div>
          <div class="tr-pnl ${net>=0?'pnl-pos':'pnl-neg'}">${net>=0?'+':''}₹${Math.abs(net).toFixed(0)}</div>
        </div>
      </div>`;
    }).join('');
  }).catch(()=>{});
}

// ── CANDLE + PROFILE STATS ─────────────────────────────
function fetchStats(){
  fetch('/api/candle_stats').then(r=>r.json()).then(s=>{
    document.getElementById('sb3m').textContent=(s.candles_3m||0).toLocaleString();
    document.getElementById('sb15m').textContent=(s.candles_15m||0).toLocaleString();
  }).catch(()=>{});
  fetch('/api/volume_profile_stats').then(r=>r.json()).then(s=>{
    document.getElementById('sbVp').textContent=(s.tickers||0)+' tickers / '+(s.buckets||0).toLocaleString()+' buckets';
    document.getElementById('sbDb').textContent='OK';
  }).catch(()=>{});
}

// ── LOG STREAM (poll recent signals as proxy) ──────────
function fetchLog(){
  fetch('/api/signal_trace?n=40').then(r=>r.json()).then(signals=>{
    const body=document.getElementById('logBody');
    if(!signals.length) return;
    const html=signals.map((s,i)=>{
      const isLive=s.disposition==='LIVE';
      const tag=isLive?'t-trd':s.disposition==='RISK_REJECTED'?'t-risk':s.vol_z_score>=2?'t-sig':'t-dat';
      const tagTxt=isLive?'TRADE':s.disposition==='RISK_REJECTED'?'RISK':s.vol_z_score>=2?'SIGNAL':'DATA';
      const msg=isLive
        ?`<span class="hi">${s.ticker}</span> ${s.direction} LIVE — Z=${fmt(s.vol_z_score,2)} limit=₹${fmt(s.limit_price,2)}`
        :s.disposition==='RISK_REJECTED'
        ?`<span class="hi">${s.ticker}</span> RISK REJECTED — ${s.risk_reason||'--'}`
        :`${s.ticker} PASS — ${s.entry_reason||'no signal'}`;
      return `<div class="log-line ${i===0?'new-row':''}">
        <div class="log-ts">${(s.timestamp||'').slice(11,19)}</div>
        <div class="log-tag ${tag}">${tagTxt}</div>
        <div class="log-msg">${msg}</div>
      </div>`;
    }).join('');
    body.innerHTML='<div class="log-wrap">'+html+'</div>';
  }).catch(()=>{});
}

// ── POLL INTERVALS ─────────────────────────────────────
fetchState();
fetchTrace();
fetchShadow();
fetchHistory();
fetchLog();
fetchStats();

setInterval(fetchState,  R);
setInterval(fetchTrace,  R*2);
setInterval(fetchLog,    R*2);
setInterval(fetchShadow, 10000);
setInterval(fetchHistory,8000);
setInterval(fetchStats,  15000);
setInterval(tickClock,   1000);
</script>
</body>
</html>"""


# ════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    t = threading.Thread(target=_engine_thread, daemon=True, name="engine")
    t.start()
    app.run(host="0.0.0.0", port=5000, debug=False, use_reloader=False)