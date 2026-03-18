import sqlite3 
conn=sqlite3.connect('database/kubers_live.db') 
tables=conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() 
print([t[0] for t in tables]) 
