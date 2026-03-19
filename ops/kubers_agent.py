"""
KUBERS AUTONOMOUS AGENT
========================
Runs on your machine. Monitors the engine every 30 seconds.
When it finds an issue, calls Claude API to diagnose and propose a fix.
Shows you the diagnosis and asks: approve? (y/n)
If yes — hot-deploys immediately, pushes to GitHub.

Setup (once):
    pip install requests anthropic
    set ANTHROPIC_API_KEY=your_key_here   (or paste when prompted)

Usage:
    python kubers_agent.py --url https://xxxx.trycloudflare.com --secret YOUR_OPS_SECRET

Options:
    --url      Your cloudflared tunnel URL (from the terminal running cloudflared)
    --secret   Your ops_secret (from investright_creds.json)
    --interval Poll interval in seconds (default: 30)
    --engine   Path to engine folder (default: C:\\Kubers\\engine)
"""

import os, sys, json, time, shutil, subprocess, argparse, textwrap
from datetime import datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Run: pip install requests")
    sys.exit(1)

# ── Config ─────────────────────────────────────────────────────────────
CLAUDE_API   = "https://api.anthropic.com/v1/messages"
CLAUDE_MODEL = "claude-sonnet-4-20250514"
ENGINE_DIR   = Path(r"C:\Kubers\engine")


# ── Windows notification ────────────────────────────────────────────────

def notify(title: str, message: str, urgent: bool = False):
    """
    Pops a Windows toast notification.
    urgent=True plays a sound and stays on screen longer.
    """
    try:
        import subprocess
        sound = "ms-winsoundevent:Notification.Looping.Alarm" if urgent else "ms-winsoundevent:Notification.Default"
        duration = "long" if urgent else "short"
        ps = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$template = @"
<toast duration=\"{duration}\">
  <visual>
    <binding template=\"ToastGeneric\">
      <text>{title}</text>
      <text>{message}</text>
    </binding>
  </visual>
  <audio src=\"{sound}\" loop=\"false\" silent=\"false\"/>
</toast>
\"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier(\"Kubers Agent\").Show($toast)
"""
        subprocess.run(["powershell", "-Command", ps], capture_output=True, timeout=5)
    except Exception:
        pass  # Notification failed silently — agent continues

# Files the agent is allowed to hot-deploy
DEPLOYABLE = {
    "engine.py", "config.py", "risk/risk_gate.py",
    "strategy/rule_strategy.py", "execution/broker.py",
    "observation/signal_log.py", "data/feed.py",
}

# ── Issue detectors ─────────────────────────────────────────────────────

def detect_issues(state: dict, logs: list, positions: list) -> list:
    """
    Returns list of issue dicts. Each has:
        type, severity, description, evidence
    Empty list = all good.
    """
    issues = []

    # 1. Too many open positions
    n_pos = len(positions)
    if n_pos > 5:
        issues.append({
            "type":        "SLOT_OVERFLOW",
            "severity":    "HIGH",
            "description": f"{n_pos} positions open — exceeds MAX_OPEN_POSITIONS=5",
            "evidence":    {"positions": positions, "count": n_pos},
        })

    # 2. Ghost positions — open in DB but likely closed (held > 30 min past EOD)
    now = datetime.now()
    for p in positions:
        entry = p.get("entry_time", "")
        if entry:
            try:
                et = datetime.fromisoformat(entry)
                hold_min = (now - et).total_seconds() / 60
                if hold_min > 400:
                    issues.append({
                        "type":        "GHOST_POSITION",
                        "severity":    "HIGH",
                        "description": f"{p['ticker']} held {hold_min:.0f} min — likely a ghost from previous session",
                        "evidence":    {"position": p, "hold_min": hold_min},
                    })
            except Exception:
                pass

    # 3. SL storm — more than 3 SL hits in last 20 log lines
    sl_hits = [l for l in logs if "SL_HIT" in l or "SL breach" in l]
    if len(sl_hits) >= 3:
        issues.append({
            "type":        "SL_STORM",
            "severity":    "MEDIUM",
            "description": f"{len(sl_hits)} SL hits detected — possible regime change or bad signals",
            "evidence":    {"sl_lines": sl_hits[-5:]},
        })

    # 4. Slow cycles
    slow = [l for l in logs if "Slow cycle" in l]
    if len(slow) >= 5:
        issues.append({
            "type":        "SLOW_CYCLES",
            "severity":    "LOW",
            "description": f"{len(slow)} slow cycles detected — engine may miss signals",
            "evidence":    {"slow_lines": slow[-3:]},
        })

    # 5. Kill switch fired
    if state.get("regime") == "KILL_SWITCH":
        issues.append({
            "type":        "KILL_SWITCH",
            "severity":    "CRITICAL",
            "description": "Kill switch has fired — no new trades",
            "evidence":    {"regime": state.get("regime"), "pnl": state.get("session_pnl")},
        })

    return issues


# ── Claude diagnosis ────────────────────────────────────────────────────

def diagnose_with_claude(issue: dict, context: dict, api_key: str) -> dict:
    """
    Sends issue + context to Claude. Returns dict:
        diagnosis, fix_needed, filename, file_content, description, user_message
    """
    # Load relevant source files
    source = {}
    for rel in ["engine.py", "risk/risk_gate.py", "config.py", "execution/broker.py"]:
        p = ENGINE_DIR / rel
        if p.exists():
            source[rel] = p.read_text(encoding="utf-8", errors="replace")

    prompt = f"""You are the autonomous ops agent for Kubers Calling, an NSE intraday trading system.

ISSUE DETECTED:
Type: {issue['type']}
Severity: {issue['severity']}
Description: {issue['description']}
Evidence: {json.dumps(issue['evidence'], indent=2)}

CURRENT ENGINE STATE:
{json.dumps(context.get('state', {}), indent=2)}

RECENT LOG LINES:
{chr(10).join(context.get('logs', [])[-30:])}

OPEN POSITIONS:
{json.dumps(context.get('positions', []), indent=2)}

SOURCE FILES:
{chr(10).join(f"=== {k} ==={chr(10)}{v[:3000]}" for k,v in source.items())}

Your job:
1. Diagnose the root cause precisely
2. Determine if a code fix is needed or if this is an operational issue (bad data, needs manual close etc)
3. If code fix needed: provide the COMPLETE corrected file content
4. Write a SHORT user message (2-3 lines max) explaining what happened and what you're doing

Respond in EXACTLY this format:
DIAGNOSIS: [one paragraph root cause]
FIX_NEEDED: [YES or NO]
FILENAME: [relative path like engine.py or risk/risk_gate.py, or NONE]
DESCRIPTION: [one line description of the fix for git commit]
USER_MESSAGE: [2-3 lines for the user — what happened, what the fix does]
FILE_CONTENT_START
[complete corrected file content here, or NONE if no fix needed]
FILE_CONTENT_END"""

    try:
        resp = requests.post(
            CLAUDE_API,
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 8000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=120,
        )
        text = resp.json()["content"][0]["text"]
    except Exception as e:
        return {"error": str(e), "fix_needed": False}

    # Parse response
    def extract(label, default=""):
        import re
        m = re.search(rf"{label}:\s*(.+?)(?:\n[A-Z_]+:|FILE_CONTENT_START|$)", text, re.DOTALL)
        return m.group(1).strip() if m else default

    fix_needed = extract("FIX_NEEDED", "NO").upper() == "YES"
    filename   = extract("FILENAME", "NONE").strip()
    if filename == "NONE": filename = None

    file_content = None
    if fix_needed and filename:
        import re
        m = re.search(r"FILE_CONTENT_START\n(.*?)FILE_CONTENT_END", text, re.DOTALL)
        if m:
            file_content = m.group(1).strip()

    return {
        "diagnosis":    extract("DIAGNOSIS"),
        "fix_needed":   fix_needed,
        "filename":     filename,
        "description":  extract("DESCRIPTION"),
        "user_message": extract("USER_MESSAGE"),
        "file_content": file_content,
    }


# ── Deploy fix ──────────────────────────────────────────────────────────

def deploy_fix(filename: str, content: str, description: str, ops_url: str, ops_secret: str) -> bool:
    """
    Deploys via /api/deploy on ops_agent — hot-deploys to engine folder,
    pushes to GitHub, and restarts the engine. Fully hands-off.
    """
    try:
        r = requests.post(
            f"{ops_url}/api/deploy",
            headers={"X-Ops-Token": ops_secret, "Content-Type": "application/json"},
            json={"files": [{"filename": Path(filename).name, "content": content}],
                  "description": description},
            timeout=60,
        )
        result = r.json()
        if "error" in result:
            print(f"  ✗  Deploy failed: {result['error']}")
            return False
        print(f"  ✓  Deployed: {result.get('deployed', [])}")
        print(f"  ✓  {result.get('restart', '')}")
        if result.get("pr_urls"):
            print(f"  ✓  GitHub PR: {result['pr_urls'][0]}")
        return True
    except Exception as e:
        print(f"  ✗  Deploy error: {e}")
        return False


def deploy_all(files: list, description: str, ops_url: str, ops_secret: str) -> bool:
    """Deploy multiple files in one atomic operation."""
    try:
        r = requests.post(
            f"{ops_url}/api/deploy",
            headers={"X-Ops-Token": ops_secret, "Content-Type": "application/json"},
            json={"files": files, "description": description},
            timeout=120,
        )
        result = r.json()
        if "error" in result:
            print(f"  ✗  Deploy failed: {result['error']}")
            return False
        print(f"  ✓  Deployed: {result.get('deployed', [])}")
        print(f"  ✓  {result.get('restart', '')}")
        return True
    except Exception as e:
        print(f"  ✗  Deploy error: {e}")
        return False


# ── Ops agent helpers ───────────────────────────────────────────────────

def fetch(ops_url: str, ops_secret: str, path: str, params: dict = None) -> dict:
    try:
        r = requests.get(
            f"{ops_url}{path}",
            headers={"X-Ops-Token": ops_secret},
            params=params,
            timeout=10,
        )
        return r.json()
    except Exception as e:
        return {"error": str(e)}


# ── Main loop ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url",      required=True,  help="Cloudflared tunnel URL")
    parser.add_argument("--secret",   required=True,  help="ops_secret from investright_creds.json")
    parser.add_argument("--interval", default=30,     type=int, help="Poll interval seconds")
    parser.add_argument("--api-key",  default="",     help="Anthropic API key (or set ANTHROPIC_API_KEY env)")
    args = parser.parse_args()

    ops_url    = args.url.rstrip("/")
    ops_secret = args.secret
    interval   = args.interval
    api_key    = args.api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    if not api_key:
        api_key = input("Anthropic API key: ").strip()

    print(f"""
╔══════════════════════════════════════════════════════╗
║  KUBERS AUTONOMOUS AGENT                             ║
║  Monitoring: {ops_url[:40]:<40}  ║
║  Interval:   {interval}s                                        ║
║  Ctrl+C to stop                                      ║
╚══════════════════════════════════════════════════════╝
""")

    # Verify connection
    health = fetch(ops_url, ops_secret, "/health")
    if "error" in health:
        print(f"✗ Cannot reach ops_agent: {health['error']}")
        sys.exit(1)
    print(f"✓ Connected to ops_agent — {health.get('time','')}\n")

    seen_issues = set()   # avoid re-alerting on same issue type repeatedly

    # Track what we've already notified about
    notified_issues = set()
    reminder_sent   = False

    while True:
        try:
            # ── 4PM daily reminder
            now_t = datetime.now()
            if now_t.hour == 16 and now_t.minute < 1 and not reminder_sent:
                notify(
                    "⚡ Kubers — 4PM Reminder",
                    "Tomorrow: 1_morning_startup.bat → cloudflared tunnel → kubers_agent.py → kubers_calling.py",
                    urgent=False,
                )
                print(f"[{now_t.strftime('%H:%M:%S')}] 🔔 4PM reminder sent")
                reminder_sent = True
            if now_t.hour != 16:
                reminder_sent = False   # reset for next day

            # Gather state
            state     = fetch(ops_url, ops_secret, "/api/summary")
            logs_resp = fetch(ops_url, ops_secret, "/api/logs", {"n": 50})
            pos_resp  = fetch(ops_url, ops_secret, "/api/positions")

            logs      = logs_resp.get("lines", [])
            positions = pos_resp if isinstance(pos_resp, list) else []
            eng_state = state.get("summary", {})

            # Detect issues
            issues = detect_issues(eng_state, logs, positions)

            if not issues:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] ✓ Engine healthy — "
                      f"{len(positions)} positions, P&L ₹{eng_state.get('net', 0):.0f}")
                seen_issues.clear()
            else:
                for issue in issues:
                    issue_key = f"{issue['type']}_{datetime.now().strftime('%H%M')}"
                    if issue_key in seen_issues:
                        continue
                    seen_issues.add(issue_key)

                    print(f"\n{'═'*55}")
                    print(f"⚠  ISSUE DETECTED: {issue['type']} [{issue['severity']}]")
                    print(f"   {issue['description']}")
                    print(f"{'═'*55}")

                    # Pop Windows notification immediately for HIGH/CRITICAL
                    if issue['severity'] in ('HIGH', 'CRITICAL'):
                        notify(
                            f"⚠ Kubers — {issue['type']}",
                            issue['description'],
                            urgent=True,
                        )

                    print("   Calling Claude to diagnose...\n")

                    context = {"state": eng_state, "logs": logs, "positions": positions}
                    result  = diagnose_with_claude(issue, context, api_key)

                    if "error" in result:
                        print(f"   ✗ Claude error: {result['error']}")
                        continue

                    print(f"📋 DIAGNOSIS:\n   {result['diagnosis']}\n")
                    print(f"💬 {result['user_message']}\n")

                    if result["fix_needed"] and result["filename"] and result["file_content"]:
                        print(f"🔧 FIX: {result['description']}")
                        print(f"   File: {result['filename']}\n")
                        answer = input("   Deploy this fix? (y/n): ").strip().lower()
                        if answer == "y":
                            ok = deploy_fix(
                                result["filename"],
                                result["file_content"],
                                result["description"],
                                ops_url,
                                ops_secret,
                            )
                            if ok:
                                print("   ✓ Fix deployed. Engine will use it on next cycle.\n")
                        else:
                            print("   Skipped.\n")
                    else:
                        print("   No code fix needed — operational issue. Take manual action if required.\n")

        except KeyboardInterrupt:
            print("\nAgent stopped.")
            sys.exit(0)
        except Exception as e:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] Agent error: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
