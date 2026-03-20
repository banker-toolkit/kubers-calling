import sqlite3
conn = sqlite3.connect(r'C:\Kubers\engine\database\kubers_live.db')
conn.row_factory = sqlite3.Row
ghosts = ('BAJFINANCE','EICHERMOT','HINDPETRO','ICICIPRULI','THERMAX','MOTHERSON','CROMPTON','PERSISTENT','MGL','GSFC')
ph = ','.join('?'*len(ghosts))
rows = conn.execute(f"""
SELECT ticker, direction, qty, entry_price, exit_price, entry_time, exit_time, exit_reason, gross_pnl
FROM trade_log WHERE DATE(entry_time)='2026-03-19' AND ticker IN ({ph})
ORDER BY entry_time
""", ghosts).fetchall()
print(f"{'TIME':<12} {'TICKER':<14} {'DIR':<6} {'QTY':>4} {'ENTRY':>8} {'EXIT':>8} {'REASON':<30} {'GROSS':>8}")
print('-'*95)
for r in rows:
    entry_t = (r['entry_time'] or '')[-15:-10] if r['entry_time'] else '?'
    exit_t  = (r['exit_time'] or '')[-15:-10]  if r['exit_time']  else '?'
    ep = r['exit_price'] or 0
    gp = r['gross_pnl'] or 0
    print(f"{entry_t}-{exit_t:<6} {r['ticker']:<14} {r['direction']:<6} {r['qty']:>4} {r['entry_price']:>8.2f} {ep:>8.2f} {r['exit_reason']:<30} {gp:>8.1f}")
conn.close()