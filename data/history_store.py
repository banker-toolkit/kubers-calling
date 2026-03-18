"""
KUBER'S CALLING — data/history_store.py
=========================================
Layer 1: Historical OHLCV candle database.

Source: Yahoo Finance (yfinance) ONLY.
  - INDstocks historical API is unreliable (silent empty returns)
  - yfinance interval='3m' is unsupported → fetch '2m', resample to 3m
  - yfinance interval='15m' is supported directly

Daily update triggered automatically at DOSSIER_TIME by auditor.py.
Startup load injects history into candle_factory for immediate signal
readiness from the first candle on day 1.
"""

import os, sys, sqlite3, logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    DB_LIVE_PATH,
    LIVE_DB_RETENTION_DAYS,
    HISTORY_DAYS_FOR_PROFILE,
)

log = logging.getLogger("history_store")

# ── import candle_factory lazily to avoid circular imports ───────────
def _get_candle_store():
    from data.candle_factory import candle_store
    return candle_store


# ═══════════════════════════════════════════════════════════════════
# STATUS CHECKS
# ═══════════════════════════════════════════════════════════════════

def is_history_populated() -> bool:
    """
    Returns True if historical_candles table has meaningful data.
    Called at startup by Validation Agent smoke test.
    """
    try:
        conn = sqlite3.connect(DB_LIVE_PATH)
        count = conn.execute(
            "SELECT COUNT(*) FROM historical_candles"
        ).fetchone()[0]
        conn.close()
        return count > 1000
    except sqlite3.Error:
        return False


def get_candle_counts() -> dict:
    """Return {ticker: {'3m': N, '15m': N}} from DB."""
    try:
        conn = sqlite3.connect(DB_LIVE_PATH)
        rows = conn.execute("""
            SELECT ticker, timeframe, COUNT(*) as n
            FROM historical_candles
            GROUP BY ticker, timeframe
        """).fetchall()
        conn.close()
        result = {}
        for ticker, tf, n in rows:
            result.setdefault(ticker, {})[tf] = n
        return result
    except sqlite3.Error as e:
        log.error("[history] get_candle_counts failed: %s", e)
        return {}


# ═══════════════════════════════════════════════════════════════════
# STARTUP LOAD
# ═══════════════════════════════════════════════════════════════════

def load_into_factory() -> dict:
    """
    Load historical candles from DB into candle_factory at engine startup.
    Returns {'tickers_loaded': N, 'candles_loaded': N}.

    This gives the engine full volume profile and ATR history
    from minute one — no cold-start blindspot.
    """
    cs = _get_candle_store()
    tickers_loaded = 0
    candles_loaded = 0

    try:
        conn = sqlite3.connect(DB_LIVE_PATH)
        tickers = [
            r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM historical_candles"
            ).fetchall()
        ]

        for ticker in tickers:
            loaded_any = False
            for tf in ("3m", "15m"):
                rows = conn.execute("""
                    SELECT time, open, high, low, close, volume
                    FROM historical_candles
                    WHERE ticker=? AND timeframe=?
                    ORDER BY time ASC
                """, (ticker, tf)).fetchall()

                if rows:
                    candles = [
                        {"time": r[0], "open": r[1], "high": r[2],
                         "low": r[3], "close": r[4], "volume": r[5]}
                        for r in rows
                    ]
                    cs.inject_historical(ticker, candles, tf)
                    candles_loaded += len(candles)
                    loaded_any = True

            if loaded_any:
                tickers_loaded += 1

        conn.close()
        log.info("[history] Loaded %d tickers, %d candles into factory",
                 tickers_loaded, candles_loaded)
        return {"tickers_loaded": tickers_loaded, "candles_loaded": candles_loaded}

    except sqlite3.Error as e:
        log.error("[history] load_into_factory failed: %s", e)
        return {"tickers_loaded": 0, "candles_loaded": 0}


# ═══════════════════════════════════════════════════════════════════
# DAILY UPDATE (called at DOSSIER_TIME by auditor)
# ═══════════════════════════════════════════════════════════════════

def update_today(universe: list) -> int:
    """
    Fetch today's candles via yfinance and append to DB.
    Prunes candles older than LIVE_DB_RETENTION_DAYS.
    Returns number of candle rows added.

    RULES:
      - interval='2m' fetched, resampled to '3m' in pandas
      - interval='15m' fetched directly
      - interval='3m' is NEVER used (unsupported by Yahoo Finance)
    """
    try:
        import yfinance as yf
        import pandas as pd
    except ImportError as e:
        log.error("[history] update_today: missing dependency: %s", e)
        return 0

    from database.vault import write_candles_bulk, delete_candles_before

    total_added = 0
    today       = datetime.now().strftime("%Y-%m-%d")
    batch_size  = 20    # yfinance performs better in small batches

    # Always include NIFTY 50 — required for ATR, not in trading universe.
    # INDIA VIX has no intraday candles on Yahoo so skip it here.
    full_universe = list(universe)
    if "NIFTY 50" not in full_universe:
        full_universe.insert(0, "NIFTY 50")

    # Yahoo Finance symbol mapping — NSE indices use different tickers to INDstocks
    _YF_MAP = {
        "NIFTY 50":  "^NSEI",
        "INDIA VIX": "^INDIAVIX",
    }

    def _yf_sym(ticker: str) -> str:
        return _YF_MAP.get(ticker, ticker + ".NS")

    for i in range(0, len(full_universe), batch_size):
        batch   = full_universe[i:i + batch_size]
        yf_syms = [_yf_sym(t) for t in batch]

        # ── 3-minute candles (via 2m → resample)
        try:
            raw = yf.download(
                tickers    = yf_syms,
                start      = today,
                interval   = "2m",
                progress   = False,
                auto_adjust= True,
                group_by   = "ticker",
            )
            for j, ticker in enumerate(batch):
                sym = _yf_sym(ticker)
                try:
                    if len(batch) == 1:
                        df = raw
                    else:
                        df = raw[sym] if sym in raw.columns.get_level_values(0) else pd.DataFrame()

                    if df is None or df.empty:
                        continue

                    # Resample 2m → 3m
                    df_3m = df.resample("3min").agg(
                        Open=("Open", "first"),
                        High=("High", "max"),
                        Low=("Low", "min"),
                        Close=("Close", "last"),
                        Volume=("Volume", "sum"),
                    ).dropna()

                    candles = [
                        {
                            "time":   int(idx.timestamp()),
                            "open":   float(row.Open),
                            "high":   float(row.High),
                            "low":    float(row.Low),
                            "close":  float(row.Close),
                            "volume": float(row.Volume),
                        }
                        for idx, row in df_3m.iterrows()
                        if row.Close > 0
                    ]
                    added = write_candles_bulk(candles, ticker, "3m")
                    total_added += added

                except (KeyError, AttributeError):
                    continue

        except Exception as e:
            log.warning("[history] 2m fetch batch %d error: %s", i // batch_size, e)

        # ── 15-minute candles (direct)
        try:
            raw15 = yf.download(
                tickers    = yf_syms,
                start      = today,
                interval   = "15m",
                progress   = False,
                auto_adjust= True,
                group_by   = "ticker",
            )
            for ticker in batch:
                sym = _yf_sym(ticker)
                try:
                    if len(batch) == 1:
                        df = raw15
                    else:
                        df = raw15[sym] if sym in raw15.columns.get_level_values(0) else pd.DataFrame()

                    if df is None or df.empty:
                        continue

                    candles = [
                        {
                            "time":   int(idx.timestamp()),
                            "open":   float(row.Open),
                            "high":   float(row.High),
                            "low":    float(row.Low),
                            "close":  float(row.Close),
                            "volume": float(row.Volume),
                        }
                        for idx, row in df.iterrows()
                        if row.Close > 0
                    ]
                    added = write_candles_bulk(candles, ticker, "15m")
                    total_added += added

                except (KeyError, AttributeError):
                    continue

        except Exception as e:
            log.warning("[history] 15m fetch batch %d error: %s", i // batch_size, e)

    # Prune candles beyond retention window
    cutoff = int((datetime.now() - timedelta(days=LIVE_DB_RETENTION_DAYS)).timestamp())
    pruned = delete_candles_before(cutoff)
    if pruned:
        log.info("[history] Pruned %d candles older than %d days",
                 pruned, LIVE_DB_RETENTION_DAYS)

    log.info("[history] update_today complete: %d candles added", total_added)
    return total_added


# ═══════════════════════════════════════════════════════════════════
# PREV CLOSE LOOKUP
# ═══════════════════════════════════════════════════════════════════

def get_prev_close(ticker: str) -> float:
    """
    Return previous trading day's closing price from DB.
    Returns 0.0 if unknown — NEVER raises an exception.
    Used by news filter in spy direction check.
    """
    try:
        conn  = sqlite3.connect(DB_LIVE_PATH)
        today = int(datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp())

        row = conn.execute("""
            SELECT close FROM historical_candles
            WHERE ticker=? AND timeframe='15m' AND time < ?
            ORDER BY time DESC LIMIT 1
        """, (ticker, today)).fetchone()
        conn.close()

        return float(row[0]) if row else 0.0

    except sqlite3.Error:
        return 0.0
    except (TypeError, IndexError, ValueError):
        return 0.0


# ═══════════════════════════════════════════════════════════════════
# ARCHIVE ROLLOVER (called by auditor when retention exceeded)
# ═══════════════════════════════════════════════════════════════════

def archive_oldest_block() -> int:
    """
    Move oldest signal_log block to archive DB.
    Returns number of records archived.
    """
    from database.vault import archive_signal_block

    cutoff = (
        datetime.now() - timedelta(days=LIVE_DB_RETENTION_DAYS)
    ).strftime("%Y-%m-%dT%H:%M:%S")

    archived = archive_signal_block(cutoff)
    if archived:
        log.info("[history] Archived %d records older than %s", archived, cutoff)
    return archived