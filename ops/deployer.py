"""
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
