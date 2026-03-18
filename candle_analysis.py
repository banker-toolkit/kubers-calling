import sqlite3 
conn=sqlite3.connect('database/kubers_live.db') 
print(conn.execute('PRAGMA table_info(historical_candles)').fetchall()) 
print(conn.execute('SELECT COUNT(*) FROM historical_candles').fetchone()) 
print(conn.execute('SELECT ticker,timeframe,timestamp FROM historical_candles LIMIT 3').fetchall()) 
import sqlite3, csv 
from datetime import datetime, timedelta 
conn=sqlite3.connect('database/kubers_live.db') 
print(conn.execute('SELECT ticker,timeframe,time,open,high,low,close FROM historical_candles LIMIT 3').fetchall()) 
