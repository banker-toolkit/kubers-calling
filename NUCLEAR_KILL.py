"""
NUCLEAR_KILL.py — closes every position Kuber has, no questions asked.
Run from project root: python NUCLEAR_KILL.py
"""
import os, sys, json, time
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import requests as _requests
from requests.adapters import HTTPAdapter as _HTTPAdapter
_INDMONEY_IP = "2405:201:3d:5059:e90d:78e1:b1c4:92a3"
class _B(_HTTPAdapter):
    def init_poolmanager(self, *a, **k):
        k["source_address"] = (_INDMONEY_IP, 0)
        super().init_poolmanager(*a, **k)
_oi = _requests.Session.__init__
def _pi(self, *a, **k):
    _oi(self, *a, **k); b=_B(); self.mount("https://",b); self.mount("http://",b)
_requests.Session.__init__ = _pi

import sqlite3, logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger()

from data.feed import load_token, forge_token_map, get_scrip_code, fetch_quotes
from config import INDMONEY_BASE_URL, CREDS_FILE, TOKEN_KEY

load_token()
forge_token_map()

with open(CREDS_FILE) as f: data = json.load(f)
headers = {"Authorization": data.get(TOKEN_KEY,""), "Content-Type":"application/json"}

conn = sqlite3.connect(os.path.join("database","kubers_live.db"))
conn.row_factory = sqlite3.Row
positions = conn.execute("SELECT ticker,direction,qty,entry_price FROM positions").fetchall()
conn.close()

if not positions:
    log.info("No positions in DB.")
    sys.exit(0)

tickers = [p["ticker"] for p in positions]
prices  = fetch_quotes(tickers)

ok = failed = 0
for p in positions:
    lp    = prices.get(p["ticker"], {}).get("price", p["entry_price"])
    side  = "SELL" if p["direction"] == "LONG" else "BUY"
    sc    = get_scrip_code(p["ticker"])
    if not sc:
        log.error("NO SCRIP CODE: %s — close manually", p["ticker"])
        failed += 1
        continue
    limit = round(lp * (0.995 if side=="SELL" else 1.005), 2)
    payload = {"txn_type":side,"exchange":"NSE","segment":"EQUITY","product":"MIS",
               "security_id":sc.replace("NSE_","").replace("BSE_",""),
               "qty":p["qty"],"order_type":"LIMIT","limit_price":limit}
    try:
        r = _requests.post(f"{INDMONEY_BASE_URL}/order", headers=headers, json=payload, timeout=8)
        if r.status_code == 200:
            log.info("✅ CLOSED %s %s qty=%d @ ₹%.2f", side, p["ticker"], p["qty"], limit)
            ok += 1
        else:
            log.error("❌ FAILED %s %s: %s", side, p["ticker"], r.text[:100])
            failed += 1
    except Exception as e:
        log.error("❌ ERROR %s: %s", p["ticker"], e)
        failed += 1
    time.sleep(0.2)

# Clear DB regardless
conn = sqlite3.connect(os.path.join("database","kubers_live.db"))
conn.execute("DELETE FROM positions")
conn.commit()
conn.close()
log.info("DB cleared. %d closed, %d failed. Close any remaining manually in INDmoney.", ok, failed)
