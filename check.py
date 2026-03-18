import sys, sqlite3
sys.path.insert(0, 'C:\\Users\\sande\\Downloads\\999777\\kubers_calling')
from config import DB_LIVE_PATH

conn = sqlite3.connect(DB_LIVE_PATH)

for table in ['positions', 'trade_log', 'signal_log']:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    print(f"\n{table}:")
    print("  " + ", ".join(cols))

conn.close()