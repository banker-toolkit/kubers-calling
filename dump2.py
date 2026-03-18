import sqlite3,csv 
conn=sqlite3.connect('database/kubers_live.db') 
rows=conn.execute("SELECT * FROM signal_log WHERE DATE(timestamp)='2026-03-12'").fetchall() 
w=csv.writer(open('signal_log_dump.csv','w',newline='')) 
w.writerow([d[0] for d in conn.execute('PRAGMA table_info(signal_log)').fetchall()]) 
w.writerows(rows) 
print('signal_log done',len(rows),'rows') 
conn.close() 
