import sqlite3,csv 
conn=sqlite3.connect('database/kubers_live.db') 
rows=conn.execute("SELECT * FROM trade_log").fetchall() 
cols=[d[0] for d in conn.execute("SELECT * FROM trade_log").description] 
w=csv.writer(open('trade_log_dump.csv','w',newline='')) 
w.writerow(cols) 
w.writerows(rows) 
print('trade_log done',len(rows),'rows') 
conn.close() 
