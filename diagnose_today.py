"""
KUBER'S CALLING — diagnose_today.py
=====================================
Run this from the project root at any time:
    python diagnose_today.py

Tells you:
  1. What trades fired today and their P&L
  2. What signals were generated and their dispositions
  3. Whether positions table is clean
  4. Whether the key bugs (side column, risk_reason blank) are gone
  5. Whether candle history is intact
  6. A plain verdict: HEALTHY / ISSUES FOUND
"""

import sqlite3
import os
from datetime import datetime

DB_PATH = os.path.join("database", "kubers_live.db")
TODAY   = datetime.now().strftime("%Y-%m-%d")
issues  = []

def sep(title=""):
    print("\n" + "═" * 60)
    if title:
        print(f"  {title}")
        print("═" * 60)

def check(label, ok, detail=""):
    icon = "✅" if ok else "❌"
    print(f"  {icon}  {label}", end="")
    if detail:
        print(f"  →  {detail}", end="")
    print()
    if not ok:
        issues.append(label)

if not os.path.exists(DB_PATH):
    print(f"❌ DB not found at {DB_PATH}")
    print("   Run from C:\\Users\\sande\\Downloads\\999777\\kubers_calling")
    exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# ── 1. CANDLE HISTORY ─────────────────────────────────────────────
sep("1. CANDLE HISTORY (Z-score / ATR baseline)")
rows = conn.execute("""
    SELECT COUNT(DISTINCT ticker) tickers,
           COUNT(*) total_candles,
           MIN(DATE(time,'unixepoch')) oldest,
           MAX(DATE(time,'unixepoch')) newest
    FROM historical_candles
""").fetchone()

check("Candle rows exist",        rows["total_candles"] > 0,
      f"{rows['total_candles']:,} candles across {rows['tickers']} tickers")
check("History covers 7+ days",
      rows["oldest"] is not None and
      (datetime.now() - datetime.strptime(rows["oldest"], "%Y-%m-%d")).days >= 7,
      f"{rows['oldest']} → {rows['newest']}")

# ── 2. TODAY'S SIGNALS ────────────────────────────────────────────
sep("2. TODAY'S SIGNALS")
sigs = conn.execute("""
    SELECT disposition, COUNT(*) n
    FROM signal_log
    WHERE DATE(timestamp) = ?
    GROUP BY disposition
""", (TODAY,)).fetchall()

total_sigs = sum(r["n"] for r in sigs)
if total_sigs == 0:
    print("  ⚠️  No signals recorded today — engine may not have run,")
    print("      or signal_log was crashing (old bug). Check logs.")
    issues.append("No signals today")
else:
    for r in sigs:
        print(f"  {'✅' if r['disposition']=='LIVE' else '  '}  {r['disposition']:<20} {r['n']} signal(s)")

# Check risk_reason blank (old bug symptom)
blank_risk = conn.execute("""
    SELECT COUNT(*) n FROM signal_log
    WHERE DATE(timestamp) = ?
    AND disposition = 'RISK_REJECTED'
    AND (risk_reason IS NULL OR risk_reason = '')
""", (TODAY,)).fetchone()["n"]

if blank_risk > 0:
    check("risk_reason populated on rejects",  False,
          f"{blank_risk} RISK_REJECTED signal(s) have blank reason — old engine.py still deployed")
else:
    check("risk_reason populated on rejects", True,
          "all rejects have a reason (or none today)")

# ── 3. TODAY'S TRADES ─────────────────────────────────────────────
sep("3. TODAY'S TRADES")
trades = conn.execute("""
    SELECT ticker, direction, entry_price, exit_price, qty,
           gross_pnl, cost_total, net_pnl, exit_reason, hold_minutes
    FROM trade_log
    WHERE DATE(entry_time) = ?
    ORDER BY entry_time
""", (TODAY,)).fetchall()

if not trades:
    print("  ⚠️  No completed trades today.")
else:
    total_net = 0
    for t in trades:
        pnl_icon = "🟢" if (t["net_pnl"] or 0) >= 0 else "🔴"
        print(f"  {pnl_icon}  {t['ticker']:<14} {t['direction']:<6} "
              f"qty={t['qty']}  "
              f"entry=₹{t['entry_price']:.2f} → exit=₹{(t['exit_price'] or 0):.2f}  "
              f"net=₹{(t['net_pnl'] or 0):.2f}  "
              f"reason={t['exit_reason']}  "
              f"held={t['hold_minutes']:.0f}m")
        total_net += (t["net_pnl"] or 0)
    pnl_icon = "🟢" if total_net >= 0 else "🔴"
    print(f"\n  {pnl_icon}  TODAY'S NET P&L: ₹{total_net:.2f}  ({len(trades)} trade(s))")

    # Check for oversized trades (> 2x per_stock_limit = ₹10,000)
    fat = [t for t in trades if (t["entry_price"] or 0) * (t["qty"] or 0) > 10000]
    check("No oversized positions (>₹10K)", len(fat) == 0,
          f"{len(fat)} trade(s) exceeded 2x per_stock_limit" if fat else "")

# ── 4. POSITIONS TABLE ────────────────────────────────────────────
sep("4. POSITIONS TABLE (should be empty outside market hours)")
pos_rows = conn.execute("SELECT * FROM positions").fetchall()
now_hour = datetime.now().hour
market_open = 9 <= now_hour < 15

if not market_open:
    check("Positions table empty (after hours)", len(pos_rows) == 0,
          f"{len(pos_rows)} stale row(s) — run clear_positions.py" if pos_rows else "")
else:
    print(f"  ℹ️  Market hours — {len(pos_rows)} open position(s) (normal)")
    for p in pos_rows:
        print(f"       {dict(p).get('ticker','?')}  {dict(p).get('direction','?')}  "
              f"entry=₹{dict(p).get('entry_price',0):.2f}  qty={dict(p).get('qty',0)}")
    # Check for side column (old bug)
    if pos_rows:
        cols = [d["name"] for d in conn.execute("PRAGMA table_info(positions)").fetchall()]
        check("positions schema uses 'direction' not 'side'",
              "direction" in cols and "side" not in cols,
              f"columns: {cols}")

# ── 5. SCHEMA SANITY ─────────────────────────────────────────────
sep("5. SCHEMA SANITY")
pos_cols  = [d["name"] for d in conn.execute("PRAGMA table_info(positions)").fetchall()]
sig_cols  = [d["name"] for d in conn.execute("PRAGMA table_info(signal_log)").fetchall()]

check("positions has 'direction' column",  "direction" in pos_cols)
check("positions has no 'side' column",    "side" not in pos_cols)
check("signal_log has 'velocity_ratio'",   "velocity_ratio" in sig_cols)
check("signal_log has 'risk_reason'",      "risk_reason" in sig_cols)

# ── 6. SHADOW BOOK ────────────────────────────────────────────────
sep("6. SHADOW BOOK (today)")
shadow = conn.execute("""
    SELECT COUNT(*) total,
           SUM(fill_simulated) fills,
           ROUND(SUM(COALESCE(simulated_pnl,0)),2) pnl
    FROM shadow_log
    WHERE DATE(created_at) = ?
""", (TODAY,)).fetchone()

if shadow["total"] == 0:
    print("  ⚠️  No shadow records today — shadow book may not be running")
    issues.append("Shadow book empty today")
else:
    print(f"  ✅  {shadow['total']} shadow evaluations  |  "
          f"{shadow['fills']} fills  |  P&L ₹{shadow['pnl']:.2f}")

# ── VERDICT ───────────────────────────────────────────────────────
sep("VERDICT")
if not issues:
    print("  ✅  HEALTHY — all checks passed")
    print("      Deploy the latest fixed files and you're ready for tomorrow.")
else:
    print(f"  ❌  {len(issues)} ISSUE(S) FOUND:")
    for iss in issues:
        print(f"       • {iss}")

conn.close()
print()