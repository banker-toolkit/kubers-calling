"""
ops_agent.py  —  Kubers diagnostic API (port 5003)
Gives Claude read access to your logs and DB via a cloudflared tunnel.
Pushes fixes to GitHub branch only — never touches main directly.

Usage:
    python ops_agent.py
    (second terminal) cloudflared tunnel --url http://localhost:5003
    Paste the trycloudflare.com URL to Claude.
"""
import os, sys, json, sqlite3, logging, hmac, re, base64, subprocess
from datetime import date, datetime
from pathlib import Path
from flask import Flask, jsonify, request, abort

ENGINE_DIR = Path(r"C:\Kubers\engine")
CREDS_FILE = ENGINE_DIR / "investright_creds.json"
LOGS_DIR   = Path(r"C:\Kubers\logs")
LOG_FILE   = LOGS_DIR / "engine.log"
PORT       = 5003
MAX_ROWS   = 200

# Maps bare filename → relative path within ENGINE_DIR
ALLOWED_FILES = {
    "broker.py":          "execution/broker.py",
    "engine.py":          "engine.py",
    "config.py":          "config.py",
    "rule_strategy.py":   "strategy/rule_strategy.py",
    "signal_log.py":      "observation/signal_log.py",
    "scout_math.py":      "scout_math.py",
    "risk_gate.py":       "risk/risk_gate.py",
    "feature_engine.py":  "features/feature_engine.py",
    "candle_builder.py":  "data/candle_factory.py",
    "shadow_book.py":     "strategy/shadow_book.py",
    "vault.py":           "database/vault.py",
    "auditor.py":         "observation/auditor.py",
    "kubers_calling.py":  "kubers_calling.py",
    "ops_agent.py":       "ops_agent.py",
}

LOGS_DIR.mkdir(parents=True, exist_ok=True)
app = Flask(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("ops_agent")

def _secret():
    try:
        data = json.loads(CREDS_FILE.read_text())
        s = data.get("ops_secret","")
        if not s:
            import secrets as _s; s = _s.token_hex(32)
            data["ops_secret"] = s
            CREDS_FILE.write_text(json.dumps(data, indent=2))
            log.info("OPS_SECRET generated and saved.")
        return s
    except Exception as e:
        log.error("creds error: %s", e); sys.exit(1)

OPS_SECRET = _secret()

def auth(f):
    from functools import wraps
    @wraps(f)
    def w(*a, **k):
        if not hmac.compare_digest(request.headers.get("X-Ops-Token",""), OPS_SECRET):
            abort(401)
        return f(*a, **k)
    return w

def _db_path():
    try:
        sys.path.insert(0, str(ENGINE_DIR))
        from config import DB_LIVE_PATH; return Path(DB_LIVE_PATH)
    except Exception:
        hits = list(ENGINE_DIR.rglob("kubers_live.db"))
        return hits[0] if hits else ENGINE_DIR/"database/kubers_live.db"

def q(sql, params=(), n=MAX_ROWS):
    db = _db_path()
    if not db.exists(): return []
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    try: return [dict(r) for r in conn.execute(sql, params).fetchmany(n)]
    finally: conn.close()

@app.route("/health")
def health():
    return jsonify({"status":"ok","time":datetime.now().isoformat(),"date":date.today().isoformat()})

@app.route("/api/summary")
@auth
def summary():
    today = date.today().isoformat()
    row   = q("SELECT COUNT(*) total, SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) winners, ROUND(SUM(gross_pnl),2) gross, ROUND(SUM(cost_total),2) costs, ROUND(SUM(net_pnl),2) net FROM trade_log WHERE DATE(entry_time)=?", (today,))
    exits = q("SELECT exit_reason, COUNT(*) n, ROUND(SUM(net_pnl),2) pnl FROM trade_log WHERE DATE(entry_time)=? GROUP BY exit_reason ORDER BY pnl", (today,))
    pos   = q("SELECT * FROM positions ORDER BY entry_time")
    return jsonify({"date":today,"summary":row[0] if row else {},"exits":exits,"positions":pos})

@app.route("/api/trades")
@auth
def trades():
    dt  = request.args.get("date", date.today().isoformat())
    tk  = request.args.get("ticker")
    n   = min(int(request.args.get("n",50)), MAX_ROWS)
    sql = "SELECT * FROM trade_log WHERE DATE(entry_time)=?"
    p   = [dt]
    if tk: sql += " AND ticker=?"; p.append(tk.upper())
    sql += " ORDER BY entry_time DESC LIMIT ?"; p.append(n)
    return jsonify(q(sql, p))

@app.route("/api/signals")
@auth
def signals():
    dt   = request.args.get("date", date.today().isoformat())
    tk   = request.args.get("ticker")
    disp = request.args.get("disposition")
    n    = min(int(request.args.get("n",50)), MAX_ROWS)
    sql  = "SELECT * FROM signal_log WHERE DATE(timestamp)=?"
    p    = [dt]
    if tk:   sql += " AND ticker=?";      p.append(tk.upper())
    if disp: sql += " AND disposition=?"; p.append(disp.upper())
    sql += " ORDER BY timestamp DESC LIMIT ?"; p.append(n)
    return jsonify(q(sql, p))

@app.route("/api/positions")
@auth
def positions():
    return jsonify(q("SELECT * FROM positions ORDER BY entry_time"))

@app.route("/api/logs")
@auth
def logs():
    n    = min(int(request.args.get("n",50)), 200)
    grep = request.args.get("grep")
    candidates = [LOG_FILE] + list(LOGS_DIR.glob("*.log")) + list(ENGINE_DIR.glob("*.log"))
    lf = next((f for f in candidates if f.exists()), None)
    if not lf: return jsonify({"lines":[],"note":"No log file found"})
    try:
        lines = [l.rstrip() for l in open(lf, encoding="utf-8", errors="replace").readlines()[-n:]]
        if grep: lines = [l for l in lines if grep.lower() in l.lower()]
        return jsonify({"lines":lines,"log_file":str(lf)})
    except Exception as e:
        return jsonify({"error":str(e)})

@app.route("/api/shadow")
@auth
def shadow():
    return jsonify(q("""SELECT strategy_name, COUNT(*) total,
        SUM(CASE WHEN fill_simulated=1 THEN 1 ELSE 0 END) fills,
        SUM(CASE WHEN fill_simulated=1 AND simulated_pnl>0 THEN 1 ELSE 0 END) wins,
        ROUND(SUM(CASE WHEN fill_simulated=1 THEN simulated_pnl ELSE 0 END),2) gross_pnl,
        ROUND(AVG(CASE WHEN fill_simulated=1 THEN simulated_pnl END),2) avg_pnl
        FROM shadow_log WHERE created_at>=DATE('now','-7 days')
        GROUP BY strategy_name ORDER BY gross_pnl DESC"""))

@app.route("/api/push_fix", methods=["POST"])
@auth
def push_fix():
    data     = request.get_json(silent=True) or {}
    filename = data.get("filename","")
    content  = data.get("content","")
    desc     = data.get("description","Fix")
    if not filename or not content:
        return jsonify({"error":"filename and content required"}),400
    if filename not in ALLOWED_FILES:
        return jsonify({"error":f"{filename} not in allowed list"}),400
    import ast
    try: ast.parse(content)
    except SyntaxError as e:
        return jsonify({"error":f"Syntax error L{e.lineno}: {e.msg}","pushed":False}),400
    try:
        creds     = json.loads(CREDS_FILE.read_text())
        gh_token  = creds.get("github_token","")
        gh_user   = creds.get("github_user","")
        repo_name = creds.get("github_repo","kubers-calling")
    except Exception as e:
        return jsonify({"error":f"creds error: {e}"}),500
    if not gh_token or not gh_user:
        return jsonify({"error":"github_token and github_user missing from investright_creds.json"}),500
    import base64, urllib.request, urllib.error
    branch   = f"fixes/{date.today().isoformat()}"
    api_base = f"https://api.github.com/repos/{gh_user}/{repo_name}"
    hdrs     = {"Authorization":f"token {gh_token}","Accept":"application/vnd.github.v3+json","Content-Type":"application/json"}
    def gh(method, path, body=None):
        req = urllib.request.Request(api_base+path, method=method,
              data=json.dumps(body).encode() if body else None, headers=hdrs)
        try:
            with urllib.request.urlopen(req) as r: return json.loads(r.read()), r.status
        except urllib.error.HTTPError as e: return json.loads(e.read()), e.code
    main_info, s = gh("GET","/git/ref/heads/main")
    if s!=200: return jsonify({"error":"Could not get main SHA"}),500
    main_sha = main_info["object"]["sha"]
    existing, s = gh("GET",f"/git/ref/heads/{branch}")
    if s==404: gh("POST","/git/refs",{"ref":f"refs/heads/{branch}","sha":main_sha})
    file_info, s = gh("GET",f"/contents/{filename}?ref={branch}")
    payload = {"message":f"fix: {desc}","content":base64.b64encode(content.encode()).decode(),"branch":branch}
    if s==200: payload["sha"] = file_info.get("sha")
    result, s = gh("PUT",f"/contents/{filename}",payload)
    if s not in (200,201): return jsonify({"error":"File push failed","detail":result}),500
    pr_body = {"title":f"[FIX] {desc}","body":f"File: `{filename}`\nFix: {desc}\n\n⚠ Review before merging. Merge = deploy at next 15:25 pull.","head":branch,"base":"main"}
    pr, _ = gh("POST","/pulls",pr_body)
    pr_url = pr.get("html_url","")
    log.info("Fix pushed: %s → %s | PR: %s", filename, branch, pr_url)
    return jsonify({"pushed":True,"filename":filename,"branch":branch,"pr_url":pr_url})

@app.route("/api/deploy", methods=["POST"])
@auth
def deploy():
    """
    Hot-deploy one or more files directly to the engine folder + push to GitHub.
    Accepts: {"files": [{"filename": "engine.py", "content": "..."}], "description": "..."}
    Syntax-checks all files first. If any fail, nothing is deployed.
    After deploy, restarts the engine process.
    """
    import ast, shutil
    data  = request.get_json(silent=True) or {}
    files = data.get("files", [])
    desc  = data.get("description", "Auto-deploy")

    if not files:
        return jsonify({"error": "files list required"}), 400

    # Validate all files first before touching anything
    validated = []
    for f in files:
        fname   = f.get("filename", "")
        content = f.get("content", "")
        if fname not in ALLOWED_FILES:
            return jsonify({"error": f"{fname} not in allowed list"}), 400
        if not content:
            return jsonify({"error": f"{fname} has empty content"}), 400
        try:
            ast.parse(content)
        except SyntaxError as e:
            return jsonify({"error": f"Syntax error in {fname} L{e.lineno}: {e.msg}"}), 400
        validated.append((fname, ALLOWED_FILES[fname], content))

    # Deploy all files
    deployed = []
    for fname, relpath, content in validated:
        dest = ENGINE_DIR / relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Backup
        if dest.exists():
            ts = datetime.now().strftime("%H%M%S")
            shutil.copy2(dest, dest.with_suffix(f".bak_{ts}"))
        dest.write_text(content, encoding="utf-8")
        deployed.append(relpath)
        log.info("Deployed: %s", relpath)

    # Push to GitHub
    pr_urls = []
    try:
        creds     = json.loads(CREDS_FILE.read_text())
        gh_token  = creds.get("github_token", "")
        gh_user   = creds.get("github_user", "")
        repo_name = creds.get("github_repo", "kubers-calling")
        if gh_token and gh_user:
            import base64, urllib.request, urllib.error
            branch   = f"deploy/{date.today().isoformat()}"
            api_base = f"https://api.github.com/repos/{gh_user}/{repo_name}"
            hdrs     = {"Authorization": f"token {gh_token}",
                        "Accept": "application/vnd.github.v3+json",
                        "Content-Type": "application/json"}
            def gh(method, path, body=None):
                req = urllib.request.Request(api_base + path, method=method,
                      data=json.dumps(body).encode() if body else None, headers=hdrs)
                try:
                    with urllib.request.urlopen(req) as r: return json.loads(r.read()), r.status
                except urllib.error.HTTPError as e: return json.loads(e.read()), e.code

            main_info, s = gh("GET", "/git/ref/heads/main")
            if s == 200:
                main_sha = main_info["object"]["sha"]
                existing, s = gh("GET", f"/git/ref/heads/{branch}")
                if s == 404:
                    gh("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": main_sha})
                for fname, relpath, content in validated:
                    file_info, s = gh("GET", f"/contents/{relpath}?ref={branch}")
                    payload = {"message": f"deploy: {desc}",
                               "content": base64.b64encode(content.encode()).decode(),
                               "branch": branch}
                    if s == 200: payload["sha"] = file_info.get("sha")
                    gh("PUT", f"/contents/{relpath}", payload)
                pr_body = {"title": f"[DEPLOY] {desc}",
                           "body": f"Files: {[r for _,r,_ in validated]}\nAuto-deployed at {datetime.now().isoformat()}",
                           "head": branch, "base": "main"}
                pr, _ = gh("POST", "/pulls", pr_body)
                pr_urls.append(pr.get("html_url", ""))
    except Exception as e:
        log.warning("GitHub push failed (hot-deploy still applied): %s", e)

    # Restart engine — kill python processes except this ops_agent
    restart_note = ""
    try:
        import subprocess, sys, os
        # Write a restart flag file — engine startup script checks for it
        restart_flag = ENGINE_DIR / ".restart_requested"
        restart_flag.write_text(datetime.now().isoformat())
        # Start new engine in background
        subprocess.Popen(
            [sys.executable, str(ENGINE_DIR / "kubers_calling.py")],
            cwd=str(ENGINE_DIR),
            creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        )
        restart_note = "Engine restart initiated"
        log.info("Engine restart initiated after deploy")
    except Exception as e:
        restart_note = f"Manual restart needed: {e}"

    return jsonify({
        "deployed": deployed,
        "pr_urls": pr_urls,
        "restart": restart_note,
        "description": desc,
    })


def _banner():
    print(f"\n{'═'*58}")
    print("  KUBERS OPS AGENT  —  port 5003")
    print(f"{'═'*58}")
    print(f"  Secret: {OPS_SECRET[:8]}...  (full value in investright_creds.json)")
    print()
    print("  Expose to Claude (run in a second terminal):")
    print("    cloudflared tunnel --url http://localhost:5003")
    print()
    print("  Endpoints:")
    print("    GET  /health, /api/summary, /api/trades, /api/signals")
    print("    GET  /api/positions, /api/logs, /api/shadow")
    print("    POST /api/push_fix  ← Claude pushes fixes to GitHub")
    print(f"{'═'*58}\n")

if __name__ == "__main__":
    _banner()
    app.run(host="127.0.0.1", port=PORT, debug=False)
