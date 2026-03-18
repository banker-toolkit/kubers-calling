import sys, sqlite3
sys.path.insert(0, 'C:\\Users\\sande\\Downloads\\999777\\kubers_calling')
from config import DB_LIVE_PATH

conn = sqlite3.connect(DB_LIVE_PATH)
conn.row_factory = sqlite3.Row

phantoms = ("BHEL", "GRINDWELL", "NIACL", "RAMCOCEM")
conn.execute(f"DELETE FROM positions WHERE ticker IN ({','.join('?'*len(phantoms))})", phantoms)
conn.commit()
print(f"Deleted {len(phantoms)} phantoms")

print("\nRemaining open positions in DB:")
rows = conn.execute("SELECT ticker, direction, qty, entry_price FROM positions").fetchall()
for r in rows:
    print(f"  {r['ticker']:15s} {r['direction']:5s} qty={r['qty']} entry={r['entry_price']}")
conn.close()