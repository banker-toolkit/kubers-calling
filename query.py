import sys, sqlite3, json
sys.path.insert(0, 'C:\\Users\\sande\\Downloads\\999777\\kubers_calling')
from config import DB_LIVE_PATH

conn = sqlite3.connect(DB_LIVE_PATH)
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT ticker, direction, entry_price, exit_price, qty,
           hold_minutes, exit_reason, gross_pnl, net_pnl,
           cost_total, entry_time
    FROM trade_log
    WHERE exit_reason = 'SL_HIT'
    ORDER BY entry_time DESC
    LIMIT 200
""").fetchall()
print(json.dumps([dict(r) for r in rows], indent=2))
conn.close()