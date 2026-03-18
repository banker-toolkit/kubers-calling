# KUBER'S CALLING — Startup Guide

## What's in this folder

| File | What it does |
|---|---|
| `config.py` | All parameters — thresholds, limits, paths |
| `vajrapaat.py` | Main trading engine — runs the strategy loop |
| `kubers_calling.py` | Dashboard web server — open in browser |
| `scout_math.py` | ATR, Z-score, velocity calculations |
| `spy_agent.py` | Direction logic — sector lag, candle structure |
| `bouncer.py` | Risk manager — capital limits, kill switch |
| `shadow_book.py` | 26 simulated strategies — ML data generation |
| `auditor.py` | 4:30 PM dossier — daily performance report |
| `candle_builder.py` | Builds proper candles from live ticks |
| `market_data.py` | IndMoney API — quotes, orders, token map |
| `universe_mapper.py` | 500 stocks with sector mappings |
| `database/vault.py` | SQLite database — all trade logging |
| `templates/index.html` | Dashboard UI |

---

## STEP 1 — Install requirements (first time only)

Open a terminal in this folder and run:

```
pip install flask requests pandas numpy scipy
```

---

## STEP 2 — Every morning before 9:00 AM

1. Open a terminal in this folder
2. Run the dashboard:
   ```
   python kubers_calling.py
   ```
3. Open your browser at: **http://localhost:5000**
4. Log into IndMoney on your phone or browser
5. Get your daily JWT token (from the developer portal or your existing auth flow)
6. Paste the token into the **DAILY TOKEN** box on the dashboard and click SAVE

---

## STEP 3 — Start the engine

Click **▶ START** on the dashboard at any time before 9:15 AM.

The engine will:
- Verify your IndMoney connection
- Map 9,000+ NSE tokens
- Start scanning when the market opens at 9:15

---

## STEP 4 — During trading hours

Everything runs automatically. You can:
- Change the **kill switch floor** at any time (takes effect in 2.5 seconds)
- Change **global limit** and **per-stock limit** at any time
- Set a **focus ticker** to watch its price chart in real time
- Click **■ STOP** to halt the engine (open positions remain — close them manually)

---

## STEP 5 — End of day

- At 3:20 PM the engine automatically squares off all open positions
- At 4:30 PM the Auditor generates the daily dossier (visible on dashboard)
- Review the dossier — check win rate, slippage, best simulated strategy

---

## Kill Switch

If your equity drops below the floor (default ₹90,000):
- All live positions are closed
- Live trading halts for the rest of the day
- The system keeps running on Shadow Book (paper trading)
- You can see this happening via the red banner on the dashboard

---

## Where your data lives

- `database/kubers_vault.db` — SQLite database with every signal and trade
- `engine_state.json` — live state file (read by dashboard every 2.5 seconds)
- `live_config.json` — runtime config (updated by dashboard)
- `investright_creds.json` — your daily token (never share this file)

---

## For the ML work (Day 30+)

Open a Jupyter notebook and:
```python
import sqlite3, pandas as pd
conn = sqlite3.connect('database/kubers_vault.db')
trades = pd.read_sql("SELECT * FROM trade_ledger", conn)
signals = pd.read_sql("SELECT * FROM signal_log", conn)
```

All features needed for XGBoost/LightGBM are already logged.

---

## Something not working?

Most common issues:

1. **"No token"** — paste your IndMoney JWT token via the dashboard
2. **Z: nan in watchlist** — normal for first 10–15 minutes, candles are warming up
3. **API rejected** — token has expired, paste a fresh one and restart
4. **Orders not filling** — check IndMoney account has INTRADAY enabled

---

*Built for NSE intraday trading at ₹1 lakh capital.*
*Strategy: Institutional absorption detection on sector-relative volume anomalies.*
