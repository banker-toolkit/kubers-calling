"""
HISTORICAL DB MANAGER
Reads from the historical_candles table built by Gemini's build_history.py.
At startup: loads DB into CandleStore (fast, no API).
After close:  fetches only today's candles and appends, drops candles > 30 days.
Never re-pulls 30 days after the first build.
"""
import sqlite3, time, requests
from datetime import datetime, timedelta, date
from config import DB_PATH, INDMONEY_BASE_URL
from candle_builder import candle_store


# ── How many days of history to keep in DB
HISTORY_DAYS = 30


def is_history_populated() -> bool:
    """Returns True if historical_candles table exists and has > 100 rows."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur  = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM historical_candles")
        count = cur.fetchone()[0]
        conn.close()
        return count > 100
    except Exception:
        return False


def load_history_into_store(progress_callback=None) -> dict:
    """
    Reads all rows from historical_candles and injects into CandleStore.
    Called once at engine startup. Takes ~2-5 seconds (DB read, no API).
    Returns {loaded_tickers, total_candles}.
    """
    try:
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT ticker, timeframe, time, open, high, low, close, volume FROM historical_candles ORDER BY ticker, timeframe, time")
        rows   = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"[HISTORY] ❌ DB read failed: {e}")
        return {"loaded_tickers": 0, "total_candles": 0}

    # Group by ticker + timeframe
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))
    for ticker, tf, t, o, h, l, c, v in rows:
        grouped[ticker][tf].append({"time": t, "open": o, "high": h, "low": l, "close": c, "volume": v})

    tickers_loaded = 0
    total = 0
    for ticker, tfs in grouped.items():
        for tf, candles in tfs.items():
            candle_store.inject_historical(ticker, candles, tf)
            total += len(candles)
        tickers_loaded += 1

    print(f"[HISTORY] ✅ Loaded {tickers_loaded} tickers, {total:,} candles from DB")
    if progress_callback:
        progress_callback(f"{tickers_loaded} tickers, {total:,} candles loaded from DB", 100)
    return {"loaded_tickers": tickers_loaded, "total_candles": total}


def update_today(universe: list, security_ids: dict = None, headers: dict = None) -> int:
    """
    Called once after market close (at 4:30 PM via dossier trigger).
    Fetches today's 3m + 15m candles via yfinance. Appends to DB. Prunes > 30 days.
    Returns count of new candles added.
    """
    import yfinance as yf

    today    = datetime.now().strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    cutoff   = int((datetime.now() - timedelta(days=HISTORY_DAYS)).timestamp())

    conn   = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS historical_candles
                      (ticker TEXT, timeframe TEXT, time INTEGER,
                       open REAL, high REAL, low REAL, close REAL, volume REAL,
                       PRIMARY KEY (ticker, timeframe, time))''')

    added      = 0
    BATCH_SIZE = 20

    for batch_start in range(0, len(universe), BATCH_SIZE):
        batch      = universe[batch_start:batch_start + BATCH_SIZE]
        yf_symbols = {f"{t}.NS": t for t in batch}

        for yf_interval, tf_label, resample in [("2m","3m",True), ("15m","15m",False)]:
            try:
                raw = yf.download(
                    tickers=list(yf_symbols.keys()),
                    start=today, end=tomorrow,
                    interval=yf_interval,
                    progress=False, auto_adjust=True, group_by="ticker")
                if raw is None or raw.empty:
                    continue

                def extract(df):
                    if df is None or df.empty:
                        return []
                    if resample:
                        df = df.resample("3min").agg(
                            {"Open":"first","High":"max","Low":"min",
                             "Close":"last","Volume":"sum"}).dropna(subset=["Close"])
                    out = []
                    for ts, row in df.iterrows():
                        c = float(row.get("Close", 0) or 0)
                        if c > 0:
                            out.append((int(ts.timestamp()),
                                float(row.get("Open",  c) or c),
                                float(row.get("High",  c) or c),
                                float(row.get("Low",   c) or c),
                                c,
                                float(row.get("Volume",0) or 0)))
                    return out

                if len(batch) == 1:
                    candles = extract(raw)
                    if candles:
                        cursor.executemany(
                            "INSERT OR REPLACE INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                            [(batch[0], tf_label, *c) for c in candles])
                        added += len(candles)
                else:
                    for yf_sym, ticker in yf_symbols.items():
                        try:
                            if yf_sym not in raw.columns.get_level_values(0):
                                continue
                            candles = extract(raw[yf_sym])
                            if candles:
                                cursor.executemany(
                                    "INSERT OR REPLACE INTO historical_candles VALUES (?,?,?,?,?,?,?,?)",
                                    [(ticker, tf_label, *c) for c in candles])
                                added += len(candles)
                        except Exception:
                            pass
            except Exception as e:
                print(f"[DB] update batch error: {e}")
        time.sleep(0.3)

    cursor.execute("DELETE FROM historical_candles WHERE time < ?", (cutoff,))
    pruned = cursor.rowcount
    conn.commit()
    conn.close()
    print(f"[HISTORY] ✅ Daily update: +{added} candles, pruned {pruned} old candles")
    return added


def get_prev_close(ticker: str) -> float:
    """
    Returns previous day's closing price from DB.
    Used for absolute change filter (news detection).
    """
    try:
        today    = date.today()
        cutoff   = int((datetime.combine(today, datetime.min.time())).timestamp())
        conn     = sqlite3.connect(DB_PATH)
        cur      = conn.cursor()
        cur.execute("""SELECT close FROM historical_candles
                       WHERE ticker=? AND timeframe='15m' AND time < ?
                       ORDER BY time DESC LIMIT 1""", (ticker, cutoff))
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def load_nifty_into_factory() -> dict:
    """
    BUG-STANDBY FIX (v8): seeds NIFTY 50 historical candles into candle_store
    at engine startup.

    Without this, candle_store has 0 NIFTY 15m candles at 09:15 → nifty_atr=0
    → regime=STANDBY → all signals suppressed for the first 15 minutes of every
    session.

    NIFTY 50 is stored in historical_candles with ticker='NIFTY 50' (the same
    name used by engine.py's candle_store.tick("NIFTY 50", ...)).

    Called from engine.py startup() via:
        from data.history_store import load_nifty_into_factory
    The import path resolves to this file when the flat-file architecture is used.

    Returns {total_candles: int} — 0 means no NIFTY candles in DB yet
    (run build_history.py to populate).
    """
    try:
        conn   = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            """SELECT timeframe, time, open, high, low, close, volume
               FROM historical_candles
               WHERE ticker = 'NIFTY 50'
               ORDER BY timeframe, time"""
        )
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"[HISTORY] ❌ NIFTY seed DB read failed: {e}")
        return {"total_candles": 0}

    if not rows:
        print("[HISTORY] ⚠️  No NIFTY 50 candles in DB — run build_history.py first")
        return {"total_candles": 0}

    from collections import defaultdict
    grouped = defaultdict(list)
    for tf, t, o, h, l, c, v in rows:
        grouped[tf].append({"time": t, "open": o, "high": h,
                            "low": l, "close": c, "volume": v})

    total = 0
    for tf, candles in grouped.items():
        candle_store.inject_historical("NIFTY 50", candles, tf)
        total += len(candles)

    print(f"[HISTORY] ✅ NIFTY 50 seeded: {total} candles ({list(grouped.keys())})")
    return {"total_candles": total}