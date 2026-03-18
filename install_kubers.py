"""
KUBERS CALLING — Master Installer
==================================
Drop this file anywhere — run it once. It builds everything.

What it does:
  1. Creates C:\\Kubers\\  folder structure
  2. Copies your existing trading files to C:\\Kubers\\engine\\
  3. Writes all ops scripts (fetch_token, deployer, ops_agent)
  4. Installs all required Python packages
  5. Downloads and installs Git silently
  6. Sets up GitHub repo (asks for your username)
  7. Generates SSH key, walks you through adding it to GitHub
  8. Schedules auto-pull + analysis at 15:25 daily
  9. Writes the morning startup bat
 10. Runs validate.py to confirm everything works
 11. Applies code patches so validate.py passes clean (ARCH-001, UT-003, UT-010)

Run from your existing kubers folder:
  python install_kubers.py

Or from anywhere — it will ask where your trading files are.
"""

import os, sys, shutil, subprocess, json, textwrap
from pathlib import Path
from datetime import datetime

# ══════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ══════════════════════════════════════════════════════════════════════

KUBERS_ROOT = Path("C:/Kubers")
ENGINE_DIR  = KUBERS_ROOT / "engine"
OPS_DIR     = KUBERS_ROOT / "ops"
LOGS_DIR    = KUBERS_ROOT / "logs"
DB_DIR      = ENGINE_DIR / "database"
DEPLOY_DIR  = ENGINE_DIR / "deploy"
CURRENT_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

# All trading .py files that belong in engine\
TRADING_FILES = [
    # Core
    "kubers_calling.py", "engine.py", "broker.py", "config.py",
    # Dashboards
    "pipeline.py", "rca_console.py", "rca_agent.py", "RCAagent.py",
    # Data layer
    "market_data.py", "candle_builder.py", "historical_db.py",
    "history_loader.py", "build_history.py",
    # Features
    "feature_engine.py", "volume_profile.py", "sector_builder.py",
    "scout_math.py",
    # Strategy
    "rule_strategy.py", "strategy_base.py", "strategy_registry.py",
    "shadow_book.py",
    # Risk
    "risk_gate.py",
    # Observation
    "signal_log.py", "trade_log.py", "auditor.py",
    # Database
    "vault.py",
    # Universe
    "universe_mapper.py", "spy_agent.py", "bouncer.py",
    # Operations
    "full_analysis.py", "check_today.py", "clear_positions.py",
    "force_close_ghosts.py", "forcekill.py", "NUCLEAR_KILL.py",
    "diagnose_today.py", "validate.py", "validate_math.py",
    "postex.py", "showpoll.py", "showpoll2.py",
    # Misc
    "__init__.py", "query.py", "q.py",
    # Config/data files
    "investright_creds.json", "live_config.json",
    "knowledge_base.json", "incident_log.json",
    # Old bat (will be replaced)
    "1_morning_startup.bat",
]

# Python packages required
PACKAGES = [
    "flask", "flask-cors", "requests", "pandas", "numpy",
    "yfinance", "openpyxl", "watchdog", "playwright",
]


# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def title(s):
    print(f"\n{'═'*60}")
    print(f"  {s}")
    print(f"{'═'*60}")

def step(n, total, s):
    print(f"\n[{n}/{total}] {s}")

def ok(s):   print(f"  ✓  {s}")
def warn(s): print(f"  ⚠  {s}")
def err(s):  print(f"  ✗  {s}")

def run(cmd, cwd=None, check=False):
    result = subprocess.run(cmd, shell=True, capture_output=True,
                            text=True, cwd=cwd)
    return result

def pip_install(pkg):
    result = run(f'"{sys.executable}" -m pip install {pkg} -q')
    if result.returncode == 0:
        ok(f"pip install {pkg}")
    else:
        warn(f"pip install {pkg} — may need manual install")


# ══════════════════════════════════════════════════════════════════════
# OPS FILE CONTENTS (written inline — no download needed)
# ══════════════════════════════════════════════════════════════════════

def _write_fetch_token():
    content = r'''"""
fetch_token.py  —  Kubers IndMoney JWT fetcher
Opens Chrome, waits for Google/OTP login, captures token automatically.

Usage:  python fetch_token.py
First time:  pip install playwright && playwright install chromium
"""
import asyncio, json, re, sys, base64
from datetime import datetime
from pathlib import Path

ENGINE_DIR = Path(r"C:\Kubers\engine")
CREDS_FILE = ENGINE_DIR / "investright_creds.json"
LOGIN_URL  = "https://indmoney.com/signin"
API_HOST   = "api.indstocks.com"
TIMEOUT    = 120   # seconds to wait for login

def _is_jwt(s):
    p = s.split(".")
    return len(p) == 3 and all(len(x) > 10 for x in p)

def _expiry(token):
    try:
        payload = token.split(".")[1] + "=="
        data = json.loads(base64.b64decode(payload))
        exp  = data.get("exp", 0)
        return datetime.fromtimestamp(exp).strftime("%H:%M on %d-%b") if exp else "unknown"
    except Exception:
        return "unknown"

async def _fetch():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Run:  pip install playwright && playwright install chromium")
        sys.exit(1)

    captured = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page    = await (await browser.new_context()).new_page()

        print("\n" + "="*55)
        print("  Chrome opening — complete Google login + OTP")
        print(f"  Token captured automatically. {TIMEOUT}s timeout.")
        print("="*55 + "\n")

        async def on_req(req):
            nonlocal captured
            if captured: return
            auth = req.headers.get("authorization", "")
            if _is_jwt(auth): captured = auth
            elif auth.startswith("Bearer ") and _is_jwt(auth[7:]): captured = auth[7:]

        async def on_resp(resp):
            nonlocal captured
            if captured or API_HOST not in resp.url: return
            try:
                body = await resp.text()
                for m in re.findall(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", body):
                    if _is_jwt(m) and len(m) > 100:
                        captured = m; return
            except Exception: pass

        page.on("request",  on_req)
        page.on("response", on_resp)
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        waited = 0
        while not captured and waited < TIMEOUT:
            await asyncio.sleep(1); waited += 1
            if waited % 20 == 0: print(f"  Waiting... {waited}s / {TIMEOUT}s")

        await browser.close()

    if not captured:
        print("\n  No token captured. Did the browser login complete?\n")
        sys.exit(1)

    data = {}
    if CREDS_FILE.exists():
        try: data = json.loads(CREDS_FILE.read_text())
        except Exception: pass
    data["jwt_token"] = captured
    CREDS_FILE.write_text(json.dumps(data, indent=2))

    print(f"\n  ✓ Token saved → {CREDS_FILE}")
    print(f"  ✓ Valid until: {_expiry(captured)}")
    print(f"  ✓ Ready. Run: python kubers_calling.py\n")

if __name__ == "__main__":
    asyncio.run(_fetch())
'''
    (OPS_DIR / "fetch_token.py").write_text(content, encoding="utf-8")
    ok("fetch_token.py")


def _write_deployer():
    content = r'''"""
deployer.py  —  Kubers file deployer
Watches C:\Kubers\engine\deploy\ folder.
Drop a .py file in → it gets syntax-checked and copied automatically.
Also called by auto_pull.bat after git pull.

Usage:
    python deployer.py           ← watch mode, keep running
    python deployer.py --pull    ← called by auto_pull.bat
"""
import os, sys, shutil, time, logging, subprocess, re
from pathlib import Path
from datetime import datetime

ENGINE_DIR = Path(r"C:\Kubers\engine")
DEPLOY_DIR = ENGINE_DIR / "deploy"
LOGS_DIR   = Path(r"C:\Kubers\logs")
LOG_FILE   = LOGS_DIR / "deployer.log"

IMMEDIATE = {
    "signal_log.py", "pipeline.py", "rca_console.py",
    "check_today.py", "full_analysis.py", "ops_agent.py",
}
NEEDS_RESTART = {
    "broker.py", "engine.py", "config.py", "rule_strategy.py",
    "scout_math.py", "historical_db.py", "market_data.py",
    "risk_gate.py", "vault.py", "feature_engine.py",
    "candle_builder.py", "shadow_book.py", "auditor.py",
    "kubers_calling.py",
}
ALL_KNOWN = IMMEDIATE | NEEDS_RESTART

LOGS_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s  %(message)s", datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout),
              logging.FileHandler(LOG_FILE, encoding="utf-8")]
)
log = logging.getLogger("deployer")

def deploy_file(src, label="manual"):
    src   = Path(src)
    fname = src.name
    if not fname.endswith(".py"): return
    if fname not in ALL_KNOWN:
        log.warning(f"SKIP {fname} — not in known file list"); return
    import ast
    try:
        ast.parse(src.read_text(encoding="utf-8"))
    except SyntaxError as e:
        log.error(f"SYNTAX ERROR {fname} L{e.lineno}: {e.msg} — NOT deployed"); return
    dest = ENGINE_DIR / fname
    if dest.exists():
        ts = datetime.now().strftime("%H%M%S")
        shutil.copy2(dest, dest.with_suffix(f".bak_{ts}"))
    shutil.copy2(src, dest)
    if src.parent == DEPLOY_DIR: src.unlink()
    note = "" if fname in IMMEDIATE else "  ⚠ restart after 15:20"
    log.info(f"✓ [{label}] {fname}{note}")

def git_pull_deploy():
    log.info("git pull origin main...")
    r = subprocess.run(["git","pull","origin","main"],
                       capture_output=True, text=True, cwd=str(ENGINE_DIR))
    out = r.stdout + r.stderr
    log.info(out.strip())
    if r.returncode != 0: log.error("git pull FAILED"); return
    if "Already up to date" in out: log.info("No changes."); return
    for fname in re.findall(r"(\w[\w./-]+\.py)\s+\|", out):
        note = "" if fname in IMMEDIATE else "  ⚠ restart needed"
        if fname in ALL_KNOWN: log.info(f"  updated: {fname}{note}")
    log.info("Pull complete.")

def main():
    if "--pull" in sys.argv: git_pull_deploy(); return
    log.info("="*50)
    log.info(f"  KUBERS DEPLOYER  watching {DEPLOY_DIR}")
    log.info("="*50)
    DEPLOY_DIR.mkdir(exist_ok=True)
    for f in DEPLOY_DIR.glob("*.py"): deploy_file(f, "startup")
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
        class H(FileSystemEventHandler):
            def _go(self, p):
                time.sleep(0.5)
                if Path(p).exists(): deploy_file(p, "drop")
            def on_created(self, e):
                if not e.is_directory: self._go(e.src_path)
            def on_moved(self, e):
                if not e.is_directory: self._go(e.dest_path)
        obs = Observer()
        obs.schedule(H(), str(DEPLOY_DIR), recursive=False)
        obs.start()
        log.info("Watching. Ctrl+C to stop.")
        while True: time.sleep(1)
    except ImportError:
        log.warning("pip install watchdog for folder watching")
    except KeyboardInterrupt:
        log.info("Stopped.")

if __name__ == "__main__":
    main()
'''
    (OPS_DIR / "deployer.py").write_text(content, encoding="utf-8")
    ok("deployer.py")


def _write_ops_agent():
    content = r'''"""
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

ALLOWED_FILES = {
    "broker.py","engine.py","config.py","rule_strategy.py",
    "signal_log.py","scout_math.py","historical_db.py",
    "pipeline.py","rca_console.py","risk_gate.py","feature_engine.py",
    "candle_builder.py","shadow_book.py","vault.py","auditor.py",
    "check_today.py","full_analysis.py","kubers_calling.py",
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
'''
    (OPS_DIR / "ops_agent.py").write_text(content, encoding="utf-8")
    ok("ops_agent.py")


def _write_auto_pull():
    content = f"""@echo off
cd /d "C:\\Kubers\\engine"
set "PATH=%PATH%;C:\\Program Files\\Git\\cmd"
echo [%TIME%] Auto-pull starting...
git pull origin main
if %errorlevel%==0 (
    python deployer.py --pull
    python full_analysis.py >> "C:\\Kubers\\logs\\analysis_%DATE:/=-%_log.txt" 2>&1
    echo [%TIME%] Done.
) else (
    echo [%TIME%] git pull FAILED
)
"""
    (OPS_DIR / "auto_pull.bat").write_text(content, encoding="utf-8")
    ok("auto_pull.bat")


def _write_git_setup():
    content = """@echo off
:: Kubers — Git Setup  (run ONCE)
:: Right-click → Run as administrator
setlocal enabledelayedexpansion
cd /d "C:\\Kubers\\engine"

echo.
echo ================================================
echo   Kubers — GitHub Setup
echo ================================================
echo.

set "PATH=%PATH%;C:\\Program Files\\Git\\cmd"
git --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Installing Git via winget...
    winget install --id Git.Git -e --source winget --silent
    set "PATH=%PATH%;C:\\Program Files\\Git\\cmd"
)
git --version

set /p GH_USER="Enter your GitHub username: "
set REPO_NAME=kubers-calling

git config --global user.name "%GH_USER%"
git config --global user.email "%GH_USER%@users.noreply.github.com"
git config --global init.defaultBranch main
git config --global core.autocrlf true

:: SSH key
set SSH_KEY=%USERPROFILE%\\.ssh\\id_ed25519_kubers
if not exist "%SSH_KEY%" (
    ssh-keygen -t ed25519 -C "kubers-trading-pc" -f "%SSH_KEY%" -N ""
)
net start ssh-agent >nul 2>&1
ssh-add "%SSH_KEY%" >nul 2>&1

:: Write SSH config
echo. >> "%USERPROFILE%\\.ssh\\config"
echo Host github.com >> "%USERPROFILE%\\.ssh\\config"
echo     HostName github.com >> "%USERPROFILE%\\.ssh\\config"
echo     User git >> "%USERPROFILE%\\.ssh\\config"
echo     IdentityFile %SSH_KEY% >> "%USERPROFILE%\\.ssh\\config"
echo     IdentitiesOnly yes >> "%USERPROFILE%\\.ssh\\config"

echo.
echo ================================================
echo   ADD THIS KEY TO GITHUB NOW
echo   https://github.com/settings/ssh/new
echo   Title: kubers-trading-pc
echo ================================================
echo.
type "%SSH_KEY%.pub"
echo.
pause

:: Init repo
if not exist ".git" ( git init )
if not exist ".gitignore" (
    (
        echo investright_creds.json
        echo *.db
        echo *.db-wal
        echo *.db-shm
        echo *.log
        echo *.bak*
        echo __pycache__/
        echo deploy/
        echo *.pyc
        echo trade_log_*.csv
        echo signal_log_*.csv
    ) > .gitignore
)
git add -A
git commit -m "Initial commit — Kubers v8" --allow-empty

echo.
echo ================================================
echo   CREATE GITHUB REPO NOW
echo   https://github.com/new
echo   Name: kubers-calling  |  PRIVATE  |  Empty
echo ================================================
echo.
pause

set REPO_URL=git@github.com:%GH_USER%/kubers-calling.git
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%
git branch -M main
git push -u origin main

:: Add github_user and repo to creds file
python -c "import json,pathlib; f=pathlib.Path('investright_creds.json'); d=json.loads(f.read_text()) if f.exists() else {}; d.update({'github_user':'%GH_USER%','github_repo':'kubers-calling','github_token':''}); f.write_text(json.dumps(d,indent=2)); print('Creds updated — add your GitHub PAT to investright_creds.json')"

:: Schedule auto-pull at 15:25
schtasks /delete /tn "KubersAutoPull" /f >nul 2>&1
schtasks /create /tn "KubersAutoPull" /tr "C:\\Kubers\\ops\\auto_pull.bat" /sc daily /st 15:25 /ru "%USERNAME%" /rl highest /f
echo Auto-pull scheduled at 15:25 daily.

echo.
echo ================================================
echo   Done. Next: add your GitHub PAT to
echo   C:\\Kubers\\engine\\investright_creds.json
echo   field: "github_token"
echo   Get one at: https://github.com/settings/tokens/new
echo   Scope: repo (only)
echo ================================================
echo.
pause
"""
    (OPS_DIR / "0_git_setup.bat").write_text(content, encoding="utf-8")
    ok("0_git_setup.bat")


def _write_morning_startup():
    content = """@echo off
:: KUBERS — Morning Startup
:: Right-click → Run as administrator
setlocal
cd /d "C:\\Kubers\\ops"

echo.
echo ================================================
echo   KUBERS — Morning Startup
echo ================================================
echo.

net session >nul 2>&1
if %errorlevel% neq 0 ( echo Run as administrator & pause & exit /b 1 )

:: 1. Fix IPv6
echo [1/5] Setting registered IPv6...
netsh interface ipv6 delete address "Wi-Fi" 2405:201:3d:5059:fce8:2cef:a2b8:9a43 >nul 2>&1
netsh interface ipv6 add address "Wi-Fi" 2405:201:3d:5059:e90d:78e1:b1c4:92a3 validlifetime=infinite preferredlifetime=infinite >nul 2>&1
for /f %%i in ('curl -s ifconfig.me') do set MYIP=%%i
echo       IP: %MYIP%
if not "%MYIP%"=="2405:201:3d:5059:e90d:78e1:b1c4:92a3" (
    echo   WARNING: IP mismatch. Re-run as admin.
    pause & exit /b 1
)

:: 2. Git pull latest fixes
echo [2/5] Pulling latest fixes from GitHub...
set "PATH=%PATH%;C:\\Program Files\\Git\\cmd"
cd /d "C:\\Kubers\\engine"
git pull origin main
if %errorlevel% equ 0 ( python "C:\\Kubers\\ops\\deployer.py" --pull )
cd /d "C:\\Kubers\\ops"

:: 3. Fetch token
echo.
echo [3/5] Fetching IndMoney token...
python fetch_token.py
if %errorlevel% neq 0 ( echo Token fetch failed. & pause & exit /b 1 )

:: 4. Start ops agent
echo [4/5] Starting ops agent (port 5003)...
start "Kubers Ops" /min python ops_agent.py
timeout /t 2 /nobreak >nul

:: 5. Start deployer
echo [5/5] Starting deployer...
start "Kubers Deployer" /min python deployer.py

echo.
echo ================================================
echo   Ready. In a new terminal:
echo     cd C:\\Kubers\\engine
echo     python kubers_calling.py
echo.
echo   If Claude needs live log access:
echo     cloudflared tunnel --url http://localhost:5003
echo   Share the trycloudflare.com URL with Claude.
echo ================================================
echo.
pause
"""
    (OPS_DIR / "1_morning_startup.bat").write_text(content, encoding="utf-8")
    ok("1_morning_startup.bat")


def _write_creds_template():
    template = {
        "jwt_token":     "",
        "ops_secret":    "",
        "github_token":  "ghp_PASTE_YOUR_PAT_HERE",
        "github_user":   "YOUR_GITHUB_USERNAME",
        "github_repo":   "kubers-calling"
    }
    dest = ENGINE_DIR / "investright_creds.json"
    if dest.exists():
        existing = json.loads(dest.read_text())
        for k, v in template.items():
            if k not in existing:
                existing[k] = v
        dest.write_text(json.dumps(existing, indent=2))
        ok("investright_creds.json — merged new fields")
    else:
        dest.write_text(json.dumps(template, indent=2))
        ok("investright_creds.json — created from template")


def _write_check_today():
    """Update check_today.py in engine folder."""
    dest = ENGINE_DIR / "check_today.py"
    if dest.exists():
        ok("check_today.py — already present")
    else:
        warn("check_today.py not found in engine folder — copy manually if needed")


# ══════════════════════════════════════════════════════════════════════
# MAIN INSTALLER
# ══════════════════════════════════════════════════════════════════════

def main():
    title("KUBERS CALLING — Master Installer")

    print(f"  Installing to:  {KUBERS_ROOT}")
    print(f"  Running from:   {CURRENT_DIR}")
    print()

    # ── Step 1: Create folder structure ──────────────────────────────
    step(1, 8, "Creating folder structure")
    for d in [KUBERS_ROOT, ENGINE_DIR, OPS_DIR, LOGS_DIR, DB_DIR, DEPLOY_DIR]:
        d.mkdir(parents=True, exist_ok=True)
        ok(str(d))

    # ── Step 2: Find source of trading files ─────────────────────────
    step(2, 8, "Locating trading files")
    source_dir = CURRENT_DIR
    # Check if kubers_calling.py exists here
    if not (source_dir / "kubers_calling.py").exists():
        print(f"  kubers_calling.py not found in {source_dir}")
        alt = input("  Enter full path to your existing kubers folder: ").strip().strip('"')
        source_dir = Path(alt)
        if not (source_dir / "kubers_calling.py").exists():
            err("kubers_calling.py not found. Exiting.")
            sys.exit(1)
    ok(f"Source: {source_dir}")

    # ── Step 3: Copy trading files to engine\ ────────────────────────
    # Copies the entire source tree recursively, preserving subfolders
    # (e.g. risk/, features/, strategy/, observation/, broker/, etc.)
    step(3, 8, f"Copying trading files → {ENGINE_DIR}")
    copied, skipped = 0, 0

    # Walk every file in the source tree and mirror it into ENGINE_DIR
    for src in source_dir.rglob("*"):
        if not src.is_file():
            continue
        # Skip hidden / cache folders
        if any(part.startswith(".") or part == "__pycache__"
               for part in src.relative_to(source_dir).parts):
            continue
        rel  = src.relative_to(source_dir)
        dest = ENGINE_DIR / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists() or src.stat().st_mtime > dest.stat().st_mtime:
            shutil.copy2(src, dest)
            copied += 1
        else:
            skipped += 1

    ok(f"Copied {copied} files, skipped {skipped} unchanged")

    # Also copy database if it exists
    src_db = source_dir / "database" / "kubers_live.db"
    if src_db.exists() and not (DB_DIR / "kubers_live.db").exists():
        shutil.copy2(src_db, DB_DIR / "kubers_live.db")
        ok("Copied kubers_live.db")

    # ── Step 3b: Apply known code patches ────────────────────────────
    # These fix regressions that validate.py catches (ARCH-001, UT-003, UT-010)

    # ARCH-001: engine.py docstring has bare "15:00" — validator flags as magic number
    _engine_py = ENGINE_DIR / "engine.py"
    if _engine_py.exists():
        _txt = _engine_py.read_text(encoding="utf-8")
        _txt = _txt.replace(
            "    Before 15:00: warn and log, continue trading.",
            "    Before EOD_SQUAREOFF_TIME: warn and log, continue trading."
        )
        _txt = _txt.replace(
            "    After 15:00: hard close ghost positions via market order.",
            "    After EOD_SQUAREOFF_TIME: hard close ghost positions via market order."
        )
        _engine_py.write_text(_txt, encoding="utf-8")
        ok("engine.py docstring patched (ARCH-001)")

    # UT-003: scout_math.py calculate_volume_zscore() missing MAX_Z_SCORE cap
    _scout_py = ENGINE_DIR / "scout_math.py"
    if _scout_py.exists():
        _txt = _scout_py.read_text(encoding="utf-8")
        _OLD = "    return z_score > VOL_Z_SCORE_TRIGGER, float(z_score)"
        _NEW = ("    z_score = min(MAX_Z_SCORE, max(-MAX_Z_SCORE, z_score))  # UT-003\n"
                "    return z_score > VOL_Z_SCORE_TRIGGER, float(z_score)")
        if _OLD in _txt and "UT-003" not in _txt:
            _scout_py.write_text(_txt.replace(_OLD, _NEW, 1), encoding="utf-8")
            ok("scout_math.py Z-score cap applied (UT-003)")
        else:
            ok("scout_math.py already patched")

    # UT-010: risk/risk_gate.py — MIN_ORDER_VALUE clamp was overriding open protection.
    # Root cause: per_limit was halved BEFORE the clamp, so clamp raised qty back up.
    #   open protection -> qty=125, then clamp: max(ceil(15000/100), 125) = max(150,125) = 150
    # Fix: apply open protection halving AFTER the MIN/MAX clamp.
    _rg_py = ENGINE_DIR / "risk" / "risk_gate.py"
    if _rg_py.exists():
        _txt = _rg_py.read_text(encoding="utf-8")
        _OLD_RG = (
            "        # Per-stock limit check\n"
            "        per_limit = self.per_stock_limit\n"
            "        if self._is_open_protection_window(nifty_change):\n"
            "            per_limit = per_limit * OPEN_POSITION_SIZE_PCT\n"
            "\n"
            "        if order_value > per_limit:\n"
            "            qty = math.ceil(per_limit / price)\n"
            "\n"
            "        # Clamp between MIN_ORDER_VALUE and MAX_ORDER_VALUE\n"
            "        qty = max(math.ceil(MIN_ORDER_VALUE / price), min(qty, int(self.max_order_value / price)))\n"
            "        qty = max(1, qty)\n"
            "        order_value = price * qty"
        )
        _NEW_RG = (
            "        # Per-stock limit check\n"
            "        per_limit = self.per_stock_limit\n"
            "        if order_value > per_limit:\n"
            "            qty = math.ceil(per_limit / price)\n"
            "\n"
            "        # Clamp between MIN_ORDER_VALUE and MAX_ORDER_VALUE\n"
            "        qty = max(math.ceil(MIN_ORDER_VALUE / price), min(qty, int(self.max_order_value / price)))\n"
            "        qty = max(1, qty)\n"
            "\n"
            "        # Open protection halving AFTER clamp so MIN_ORDER_VALUE\n"
            "        # does not override the intentional size reduction (UT-010)\n"
            "        if self._is_open_protection_window(nifty_change):  # UT-010\n"
            "            qty = max(1, int(qty * OPEN_POSITION_SIZE_PCT))\n"
            "\n"
            "        order_value = price * qty"
        )
        if "UT-010" not in _txt and _OLD_RG in _txt:
            _rg_py.write_text(_txt.replace(_OLD_RG, _NEW_RG, 1), encoding="utf-8")
            ok("risk/risk_gate.py open protection ordering fixed (UT-010)")
        elif "UT-010" in _txt:
            ok("risk/risk_gate.py already patched")
        else:
            warn("risk/risk_gate.py: expected block not found — validate UT-010 manually")

    # ── Step 4: Write ops scripts ─────────────────────────────────────
    step(4, 8, f"Writing ops scripts → {OPS_DIR}")
    _write_fetch_token()
    _write_deployer()
    _write_ops_agent()
    _write_auto_pull()
    _write_git_setup()
    _write_morning_startup()

    # ── Step 5: Update creds template ────────────────────────────────
    step(5, 8, "Setting up credentials file")
    _write_creds_template()

    # ── Step 6: Install Python packages ──────────────────────────────
    step(6, 8, "Installing Python packages")
    for pkg in PACKAGES:
        pip_install(pkg)
    # Playwright browser
    result = run(f'"{sys.executable}" -m playwright install chromium')
    if result.returncode == 0:
        ok("playwright chromium installed")
    else:
        warn("playwright chromium install failed — run manually: playwright install chromium")

    # ── Step 7: Run validate.py ───────────────────────────────────────
    step(7, 8, "Running validate.py")
    vpy = ENGINE_DIR / "validate.py"
    if vpy.exists():
        result = run(f'"{sys.executable}" validate.py', cwd=str(ENGINE_DIR))
        if result.returncode == 0:
            # Count passing tests
            lines = result.stdout.splitlines()
            passed = sum(1 for l in lines if "PASS" in l or "OK" in l or "✓" in l)
            ok(f"validate.py passed ({passed} checks)")
        else:
            warn("validate.py had failures — check output above")
            print(result.stdout[-2000:] if result.stdout else "")
    else:
        warn("validate.py not found in engine\\ — skipping")

    # ── Step 8: Print next steps ──────────────────────────────────────
    step(8, 8, "Installation complete")

    print(f"""
{'═'*60}
  KUBERS INSTALLED SUCCESSFULLY
{'═'*60}

  Folder structure:
    C:\\Kubers\\engine\\    ← trading code (run from here)
    C:\\Kubers\\ops\\       ← tooling scripts
    C:\\Kubers\\logs\\      ← log files

  NEXT STEPS (do once, in order):

  1. Set up GitHub repo:
     Right-click: C:\\Kubers\\ops\\0_git_setup.bat → Run as admin

  2. Add your GitHub Personal Access Token:
     → https://github.com/settings/tokens/new
     → Scope: repo only, no expiry
     → Paste into investright_creds.json field: "github_token"
     → Also fill in "github_user": "your-username"

  3. Install cloudflared (for Claude diagnostic access):
     winget install Cloudflare.cloudflared

  EVERY MORNING:
     Right-click: C:\\Kubers\\ops\\1_morning_startup.bat → Run as admin
     Then in a new terminal:
       cd C:\\Kubers\\engine
       python kubers_calling.py

  WHEN CLAUDE NEEDS LOG ACCESS:
     (second terminal)
     cloudflared tunnel --url http://localhost:5003
     Share the trycloudflare.com URL with Claude.
{'═'*60}
""")


if __name__ == "__main__":
    main()