"""
KUBER'S CALLING — Historical Data Builder
Uses yfinance (Yahoo Finance) for NSE historical data.
Free, no auth, reliable, full 30-day history in seconds per stock.
INDstocks is used only for live quotes and order execution.

Run once to populate. Engine handles daily incremental updates after that.
"""
import os, sys
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import sqlite3
import time
from datetime import datetime, timedelta

try:
    import yfinance as yf
except ImportError:
    print("Installing yfinance...")
    os.system(f"{sys.executable} -m pip install yfinance --quiet")
    import yfinance as yf

from universe_mapper import load_universe
from config import DB_PATH

DAYS_BACK = 32  
BATCH_SIZE = 20  

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS historical_candles
                      (ticker TEXT, timeframe TEXT, time INTEGER,
                       open REAL, high REAL, low REAL, close REAL, volume REAL,
                       PRIMARY KEY (ticker, timeframe, time))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS progress_log
                      (ticker TEXT PRIMARY KEY, status TEXT, last_updated TEXT)''')
    conn.commit()
    conn.close()
    print(f"[DB] Vault at: {os.path.abspath(DB_PATH)}")

def get_completed(conn) -> set:
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker FROM progress_log WHERE status='DONE'")
        return set(r[0] for r in cur.fetchall())
    except Exception:
        return set()

def apply_median_cap(candles: list) -> list:
    if not candles:
        return candles
    from collections import defaultdict
    bucket_vols = defaultdict(list)
    for c in candles:
        bucket = datetime.fromtimestamp(c["time"]).strftime("%H:%M")
        bucket_vols[bucket].append(c["volume"])

    def median(lst):
        s = sorted(lst); n = len(s)
        return s[n//2] if n % 2 else (s[n//2-1] + s[n//2]) / 2

    medians = {b: median(v) for b, v in bucket_vols.items()}
    result = []
    for c in candles:
        bucket = datetime.fromtimestamp(c["time"]).strftime("%H:%M")
        med    = medians.get(bucket, 0)
        cap    = med * 5 if med > 0 else c["volume"]
        result.append({**c, "volume": min(c["volume"], cap)})
    return result

def fetch_batch(tickers: list, interval: str, days_back: int) -> dict:
    import pandas as pd
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days_back)
    yf_interval = "2m" if interval == "3m" else "15m"

    # Map NSE indices to Yahoo Finance equivalents so they don't fail
    yf_symbols = {}
    for t in tickers:
        if t == "NIFTY 50":
            yf_symbols["^NSEI"] = t
        elif t == "INDIA VIX":
            yf_symbols["^INDIAVIX"] = t
        else:
            yf_symbols[f"{t}.NS"] = t

    try:
        raw = yf.download(
            tickers=list(yf_symbols.keys()),
            start=start_dt.strftime("%Y-%m-%d"),
            end=end_dt.strftime("%Y-%m-%d"),
            interval=yf_interval,
            progress=False,
            auto_adjust=True,
            group_by="ticker",
        )
    except Exception as e:
        print(f"\n  [!] yfinance error: {e}")
        return {}

    result = {}

    def df_to_candles(df, resample_to_3m: bool) -> list:
        if df is None or df.empty:
            return []
        if resample_to_3m:
            df = df.resample("3min").agg({
                "Open":   "first",
                "High":   "max",
                "Low":    "min",
                "Close":  "last",
                "Volume": "sum",
            }).dropna(subset=["Close"])
        candles = []
        for ts, row in df.iterrows():
            t_sec = int(ts.timestamp())
            c = float(row.get("Close", 0) or 0)
            if c > 0:
                candles.append({
                    "time":   t_sec,
                    "open":   float(row.get("Open",   c) or c),
                    "high":   float(row.get("High",   c) or c),
                    "low":    float(row.get("Low",    c) or c),
                    "close":  c,
                    "volume": float(row.get("Volume", 0) or 0),
                })
        return candles

    resample = (interval == "3m")

    if len(tickers) == 1:
        ticker = tickers[0]
        candles = df_to_candles(raw, resample)
        if candles:
            result[ticker] = candles
    else:
        for yf_sym, ticker in yf_symbols.items():
            try:
                if yf_sym not in raw.columns.get_level_values(0):
                    continue
                candles = df_to_candles(raw[yf_sym], resample)
                if candles:
                    result[ticker] = candles
            except Exception:
                pass

    return result

def run():
    init_db()
    
    universe = []
    raw_universe = load_universe()
    
    # Safely unpack your universe_mapper regardless of whether it sends dicts or tuples
    for item in raw_universe:
        if isinstance(item, dict):
            universe.append(item.get('ticker', ''))
        elif isinstance(item, tuple) or isinstance(item, list):
            universe.append(item[0])
        else:
            universe.append(str(item))
            
    # FIX FOR [REG-024]: Inject required indices at the start of the queue
    for required in ["INDIA VIX", "NIFTY 50"]:
        if required not in universe:
            universe.insert(0, required)

    conn      = sqlite3.connect(DB_PATH)
    completed = get_completed(conn)
    remaining = [t for t in universe if t not in completed]

    print(f"[BUILDER] Universe: {len(universe)} | Done: {len(completed)} | Remaining: {len(remaining)}")
    if not remaining:
        print("✅ All stocks already synced.")
        conn.close()
        return

    est_min = (len(remaining) / BATCH_SIZE) * 0.4
    print(f"[BUILDER] Fetching via Yahoo Finance (NSE) — est. {est_min:.0f} minutes\n")

    cursor      = conn.cursor()
    total_added = 0
    done_count  = 0

    for batch_start in range(0, len(remaining), BATCH_SIZE):
        batch   = remaining[batch_start:batch_start + BATCH_SIZE]
        batch_n = batch_start + len(batch)

        print(f"  Batch {batch_start//BATCH_SIZE + 1} — stocks {batch_start+1}–{batch_n} of {len(remaining)}", end=" ... ")

        batch_added = 0
        batch_ok    = []

        for interval in ["15m", "3m"]:
            data = fetch_batch(batch, interval, DAYS_BACK)
            for ticker, candles in data.items():
                candles = apply_median_cap(candles)
                records = [(ticker, interval, c["time"], c["open"], c["high"],
                            c["low"], c["close"], c["volume"]) for c in candles]
                cursor.executemany(
                    "INSERT OR REPLACE INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                    records)
                batch_added += len(records)
                if ticker not in batch_ok:
                    batch_ok.append(ticker)

        for ticker in batch_ok:
            cursor.execute("INSERT OR REPLACE INTO progress_log VALUES (?,?,?)",
                           (ticker, "DONE", datetime.now().isoformat()))

        for ticker in batch:
            if ticker not in batch_ok:
                cursor.execute("INSERT OR REPLACE INTO progress_log VALUES (?,?,?)",
                               (ticker, "MISSING", datetime.now().isoformat()))

        conn.commit()
        total_added += batch_added
        done_count  += len(batch_ok)

        ok_str   = f"✅ {len(batch_ok)}/{len(batch)}"
        miss_str = f" ❌ {len(batch)-len(batch_ok)} missing" if len(batch_ok) < len(batch) else ""
        print(f"{ok_str}{miss_str} | {batch_added} candles | total: {total_added:,}")

        time.sleep(0.5) 

    conn.close()
    print(f"\n✅ Build complete.")
    print(f"   {done_count} stocks loaded | {total_added:,} candles")
    print(f"   DB: {os.path.abspath(DB_PATH)}")
    print(f"   Start the engine: python kubers_calling.py\n")

if __name__ == "__main__":
    print("=" * 55)
    print("  KUBER'S CALLING — Historical Data Builder")
    print("  Source: Yahoo Finance (NSE) — free, no auth")
    print("=" * 55 + "\n")
    run()