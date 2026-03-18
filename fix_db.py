import sqlite3 
conn=sqlite3.connect('database/kubers_live.db') 
conn.execute("INSERT INTO trade_log (ticker,direction,entry_price,exit_price,qty,entry_time,exit_time,hold_minutes,exit_reason,gross_pnl,cost_brokerage,cost_stt,cost_exchange,cost_sebi,cost_stamp,cost_gst,cost_total,net_pnl) VALUES ('BRIGADE','SHORT',659.95,659.95,22,'2026-03-12T09:57:24','2026-03-12T10:10:00',12.6,'UNKNOWN_EXTERNAL',0.0,14.56,3.64,0.87,0.029,0.44,2.78,22.32,-22.32)") 
conn.execute("INSERT INTO trade_log (ticker,direction,entry_price,exit_price,qty,entry_time,exit_time,hold_minutes,exit_reason,gross_pnl,cost_brokerage,cost_stt,cost_exchange,cost_sebi,cost_stamp,cost_gst,cost_total,net_pnl) VALUES ('KOTAKBANK','LONG',378.05,378.0,39,'2026-03-12T10:03:47','2026-03-12T10:15:00',11.2,'UNKNOWN_EXTERNAL',-1.95,14.74,3.69,0.88,0.03,0.44,2.81,22.59,-24.54)") 
conn.commit() 
print('done - 2 trades inserted') 
conn.close() 
