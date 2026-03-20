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
for r in rows:
    print(f"{r['entry_time'][11:16]}-{r['exit_time'][11:16] if r['exit_time'] else '?'} {r['ticker']:<14} {r['direction']:<5} qty={r['qty']} entry={r['entry_price']:.2f} exit={r['exit_price']:.2f if r['exit_price'] else 0:.2f} reason={r['exit_reason']}")
conn.close()
```

**3. Which broker.py actually ran — version check:**
```
findstr /n "SL_HIT" C:\Kubers\engine\execution\broker.py | head -20