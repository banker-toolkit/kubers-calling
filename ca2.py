import sqlite3 
conn=sqlite3.connect('database/kubers_live.db') 
print(conn.execute('SELECT ticker,timeframe,time,open,high,low,close FROM historical_candles LIMIT 3').fetchall()) 
