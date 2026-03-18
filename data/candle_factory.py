"""
KUBER'S CALLING — data/candle_factory.py
==========================================
Layer 1: Convert 2.5-second price ticks into genuine OHLCV candles.

CRITICAL RULE: A candle is only complete when its time window closes.
Price snapshots polled every 2.5 seconds are inputs to candle
construction — they are NEVER used directly as candle data.
ATR, velocity, and all indicators operate on complete candles only.

All parameters from config.py.
"""

import os, sys, time, logging
from collections import defaultdict
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import CANDLE_3M_SEC, CANDLE_15M_SEC

log = logging.getLogger("candle_factory")

TIMEFRAMES = {"3m": CANDLE_3M_SEC, "15m": CANDLE_15M_SEC}


class Candle:
    """Single OHLCV candle."""
    __slots__ = ("time", "open", "high", "low", "close", "volume", "complete")

    def __init__(self, ts, price, volume=0.0):
        self.time     = ts
        self.open     = price
        self.high     = price
        self.low      = price
        self.close    = price
        self.volume   = float(volume)
        self.complete = False

    def update(self, price, volume=0.0):
        self.high   = max(self.high, price)
        self.low    = min(self.low,  price)
        self.close  = price
        self.volume += float(volume)

    def to_dict(self):
        return {
            "time":   self.time,
            "open":   self.open,
            "high":   self.high,
            "low":    self.low,
            "close":  self.close,
            "volume": self.volume,
        }


def _bucket_ts(ts: float, period_sec: int) -> int:
    """Floor timestamp to candle period boundary."""
    return int(ts) - (int(ts) % period_sec)


class CandleStore:
    """
    Per-ticker candle store for both 3m and 15m timeframes.
    Thread-safe via simple in-memory state — one instance per engine.
    """

    def __init__(self):
        # {ticker: {tf: [complete_candle_dicts]}}
        self._complete: dict = defaultdict(lambda: {"3m": [], "15m": []})
        # {ticker: {tf: Candle (current open candle)}}
        self._open: dict     = defaultdict(lambda: {"3m": None, "15m": None})

    # ─────────────────────────────────────────────────────────────
    # PRIMARY METHOD — tick()
    # ─────────────────────────────────────────────────────────────

    def tick(self, ticker: str, price: float,
             volume: float, ts: float = None):
        """
        Process one price tick for a ticker.
        Closes candles whose window has elapsed and opens new ones.
        Called every SCAN_INTERVAL_SEC for every ticker.
        """
        if ts is None:
            ts = time.time()

        for tf, period in TIMEFRAMES.items():
            bucket = _bucket_ts(ts, period)
            current = self._open[ticker][tf]

            if current is None:
                # First tick — open a new candle
                self._open[ticker][tf] = Candle(bucket, price, volume)

            elif bucket > current.time:
                # New bucket — close current, open new
                current.complete = True
                self._complete[ticker][tf].append(current.to_dict())
                self._open[ticker][tf] = Candle(bucket, price, volume)

            else:
                # Same bucket — update current
                current.update(price, volume)

    # ─────────────────────────────────────────────────────────────
    # READ METHODS
    # ─────────────────────────────────────────────────────────────

    def get_candles(self, ticker: str, tf: str) -> list:
        """
        Return complete candles for ticker/timeframe as list of dicts.
        Current open (incomplete) candle is NOT included.
        """
        if tf not in TIMEFRAMES:
            raise ValueError(f"Unknown timeframe '{tf}'. Use '3m' or '15m'.")
        return list(self._complete[ticker][tf])

    def get_count(self, ticker: str, tf: str) -> int:
        """Return number of complete candles available."""
        return len(self._complete.get(ticker, {}).get(tf, []))

    def get_latest_complete(self, ticker: str, tf: str) -> dict:
        """Return the most recent complete candle, or None."""
        candles = self._complete.get(ticker, {}).get(tf, [])
        return candles[-1] if candles else None

    def get_current_open(self, ticker: str, tf: str) -> dict:
        """Return the current open (incomplete) candle as dict, or None."""
        c = self._open.get(ticker, {}).get(tf)
        return c.to_dict() if c else None

    def get_tickers(self) -> list:
        """Return all tickers with any candle data."""
        return list(self._complete.keys())

    # ─────────────────────────────────────────────────────────────
    # HISTORICAL INJECTION — called at startup
    # ─────────────────────────────────────────────────────────────

    def inject_historical(self, ticker: str, candles: list, tf: str):
        """
        Pre-populate store with historical candles from DB.
        All injected candles are marked complete.
        Called by history_store.load_into_factory() at startup.

        candles: list of dicts with keys: time, open, high, low, close, volume
        """
        if tf not in TIMEFRAMES:
            raise ValueError(f"Unknown timeframe '{tf}'. Use '3m' or '15m'.")

        valid = []
        for c in candles:
            if all(k in c for k in ("time", "open", "high", "low", "close", "volume")):
                valid.append({
                    "time":   int(c["time"]),
                    "open":   float(c["open"]),
                    "high":   float(c["high"]),
                    "low":    float(c["low"]),
                    "close":  float(c["close"]),
                    "volume": float(c["volume"]),
                })

        # Sort by time and append — don't overwrite live data already collected
        existing_times = {c["time"] for c in self._complete[ticker][tf]}
        new_candles    = sorted(
            [c for c in valid if c["time"] not in existing_times],
            key=lambda x: x["time"]
        )
        self._complete[ticker][tf] = sorted(
            self._complete[ticker][tf] + new_candles,
            key=lambda x: x["time"]
        )

    # ─────────────────────────────────────────────────────────────
    # MAINTENANCE
    # ─────────────────────────────────────────────────────────────

    def prune_old(self, ticker: str, tf: str, keep_last: int = 500):
        """Keep only the last N complete candles per ticker/timeframe."""
        store = self._complete.get(ticker, {}).get(tf, [])
        if len(store) > keep_last:
            self._complete[ticker][tf] = store[-keep_last:]

    def clear(self):
        """Full reset — used in tests."""
        self._complete.clear()
        self._open.clear()

    def stats(self) -> dict:
        """Summary statistics — used by dashboard and smoke tests."""
        total_3m  = sum(len(v["3m"])  for v in self._complete.values())
        total_15m = sum(len(v["15m"]) for v in self._complete.values())
        return {
            "tickers": len(self._complete),
            "candles_3m":  total_3m,
            "candles_15m": total_15m,
        }


# ── Module-level singleton — imported by feature_engine and others ──
candle_store = CandleStore()
