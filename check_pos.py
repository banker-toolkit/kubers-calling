import sqlite3
import os

DB_PATH = os.path.join("database", "kubers_live.db")
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

print("\n=== OPEN POSITIONS ===")
rows = conn.execute("SELECT ticker, direction, entry_price, qty, sector FROM positions").fetchall()
if not rows:
    print("  No open positions — positions table is empty")
else:
    total = 0
    for r in rows:
        val = r["entry_price"] * r["qty"]
        total += val
        print(f"  {r['ticker']:<14} {r['direction']:<6} ₹{r['entry_price']:.2f} x {r['qty']} = ₹{val:.0f}  [{r['sector'] or '--'}]")
    print(f"\n  TOTAL DEPLOYED: ₹{total:.0f}")

print("\n=== TODAY'S RISK REJECTS (last 10) ===")
from datetime import datetime
today = datetime.now().strftime("%Y-%m-%d")
rejects = conn.execute("""
    SELECT ticker, risk_reason, time_bucket
    FROM signal_log
    WHERE DATE(timestamp)=? AND disposition='RISK_REJECTED'
    ORDER BY timestamp DESC LIMIT 10
""", (today,)).fetchall()
if not rejects:
    print("  None today")
else:
    for r in rejects:
        print(f"  {r['time_bucket']}  {r['ticker']:<14}  {r['risk_reason'] or '--'}")

conn.close()