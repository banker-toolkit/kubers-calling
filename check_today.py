"""
check_today.py
==============
Run this every morning BEFORE trading starts.
Shows exactly what Kubers DB thinks is open vs what you should expect.

Usage:
    python check_today.py

No API calls — reads local DB only.
"""
import sqlite3, os, sys
from datetime import datetime, date

os.chdir(os.path.dirname(os.path.abspath(__file__)))

try:
    from config import DB_LIVE_PATH
except ImportError:
    DB_LIVE_PATH = os.path.join(os.path.dirname(__file__), "database", "kubers_live.db")

conn = sqlite3.connect(DB_LIVE_PATH)
conn.row_factory = sqlite3.Row
today = date.today().isoformat()

print("=" * 58)
print(f"  KUBERS MORNING CHECK — {today}")
print("=" * 58)

# 1. Open positions in DB
rows = conn.execute("""
    SELECT ticker, direction, entry_price, qty, sl_price, target_price, entry_time
    FROM positions
    ORDER BY entry_time
""").fetchall()

print(f"\n{'─'*58}")
print(f"  OPEN POSITIONS IN DB: {len(rows)}")
print(f"{'─'*58}")
if rows:
    for r in rows:
        et = r['entry_time'][:16] if r['entry_time'] else '?'
        val = r['entry_price'] * r['qty']
        print(f"  {r['ticker']:<14} {r['direction']:<6} "
              f"₹{r['entry_price']:.2f} × {r['qty']} = ₹{val:.0f}  "
              f"SL=₹{r['sl_price']:.2f}  entry={et}")
    print()
    print("  ⚠  These positions are in Kubers DB.")
    print("  ⚠  Verify each one is ACTUALLY OPEN on INDmoney before trading.")
    print("  ⚠  If INDmoney shows them as closed → they are ghosts.")
    print("  ⚠  Run python clear_positions.py after confirming they are ghosts.")
else:
    print("  ✅ No open positions — clean start.")

# 2. Yesterday's summary
yesterday = (date.today().replace(day=date.today().day - 1)).isoformat()
row = conn.execute("""
    SELECT COUNT(*) total,
           SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins,
           ROUND(SUM(gross_pnl),2) gross,
           ROUND(SUM(cost_total),2) costs,
           ROUND(SUM(net_pnl),2) net
    FROM trade_log WHERE DATE(entry_time) = ?
""", (yesterday,)).fetchone()

print(f"\n{'─'*58}")
print(f"  YESTERDAY ({yesterday}) FINAL SUMMARY")
print(f"{'─'*58}")
if row and row['total']:
    wr = round(row['wins'] / row['total'] * 100, 1) if row['total'] else 0
    print(f"  Trades : {row['total']}  (winners: {row['wins']}, win rate: {wr}%)")
    print(f"  Gross  : ₹{row['gross']}")
    print(f"  Costs  : ₹{row['costs']}")
    print(f"  Net    : ₹{row['net']}")
else:
    print("  No trades found for yesterday.")

# 3. Exit reason breakdown for yesterday
exits = conn.execute("""
    SELECT exit_reason,
           COUNT(*) n,
           ROUND(SUM(net_pnl),2) pnl
    FROM trade_log WHERE DATE(entry_time) = ?
    GROUP BY exit_reason ORDER BY pnl
""", (yesterday,)).fetchall()

if exits:
    print()
    for e in exits:
        flag = "  ⚠ " if e['exit_reason'] in ('SL_HIT','TARGET_FORCE_BOOKED') else "    "
        print(f"{flag}{e['exit_reason']:<28} {e['n']:>3} trades   ₹{e['pnl']}")

# 4. Pending exits (stuck check)
pending = conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
print(f"\n{'─'*58}")
if pending == 0:
    print("  ✅ DB is clean — ready to trade.")
else:
    print(f"  ⚠  {pending} position(s) in DB — reconcile with INDmoney before starting.")
print(f"{'─'*58}\n")

conn.close()
