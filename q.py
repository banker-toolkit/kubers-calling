import sqlite3 
conn=sqlite3.connect('database/kubers_live.db') 
rows=conn.execute("SELECT ticker,direction,entry_price,qty FROM positions").fetchall() 
[print(r) for r in rows] 
conn.close() 
