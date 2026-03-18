"""
CANDLE BUILDER
Converts live price ticks into genuine OHLCV candles at defined timeframes.
This is the critical fix — ticks are NOT candles.
"""
import time
from collections import defaultdict
from config import CANDLE_3M_SEC, CANDLE_15M_SEC

class CandleStore:
    """
    Maintains rolling OHLCV candle history per ticker per timeframe.
    Call tick(ticker, price, volume) every cycle.
    get_candles(ticker, '3m') returns list of completed candles.
    """

    def __init__(self):
        # {ticker: {timeframe_sec: [completed_candles]}}
        self._completed = defaultdict(lambda: defaultdict(list))
        # {ticker: {timeframe_sec: current_candle_dict}}
        self._current   = defaultdict(lambda: defaultdict(dict))
        self._max_candles = 50  # keep last 50 per timeframe

    def tick(self, ticker: str, price: float, volume: float):
        """Feed a new price tick. Called every scan cycle."""
        now = time.time()
        for tf in [CANDLE_3M_SEC, CANDLE_15M_SEC]:
            bucket = int(now // tf) * tf  # floor to candle start time

            cur = self._current[ticker][tf]

            if not cur or cur.get("bucket") != bucket:
                # New candle period — finalise the previous
                if cur:
                    self._completed[ticker][tf].append({
                        "open":   cur["open"],
                        "high":   cur["high"],
                        "low":    cur["low"],
                        "close":  cur["close"],
                        "volume": cur["volume"],
                        "time":   cur["bucket"],
                    })
                    # Trim history
                    if len(self._completed[ticker][tf]) > self._max_candles:
                        self._completed[ticker][tf] = \
                            self._completed[ticker][tf][-self._max_candles:]

                # Start fresh candle
                self._current[ticker][tf] = {
                    "bucket": bucket,
                    "open":   price,
                    "high":   price,
                    "low":    price,
                    "close":  price,
                    "volume": volume,
                }
            else:
                # Update current candle
                cur["high"]   = max(cur["high"],  price)
                cur["low"]    = min(cur["low"],   price)
                cur["close"]  = price
                cur["volume"] += volume

    def get_candles(self, ticker: str, timeframe: str = "3m") -> list:
        """
        Returns list of completed OHLCV candles, oldest first.
        timeframe: '3m' or '15m'
        """
        tf = CANDLE_3M_SEC if timeframe == "3m" else CANDLE_15M_SEC
        return self._completed[ticker][tf].copy()

    def get_current_candle(self, ticker: str, timeframe: str = "3m") -> dict:
        """Returns the current (incomplete) candle."""
        tf = CANDLE_3M_SEC if timeframe == "3m" else CANDLE_15M_SEC
        return self._current[ticker][tf].copy()

    def candle_count(self, ticker: str, timeframe: str = "3m") -> int:
        tf = CANDLE_3M_SEC if timeframe == "3m" else CANDLE_15M_SEC
        return len(self._completed[ticker][tf])

    def get_latest_candle(self, ticker: str, timeframe: str = "3m") -> dict:
        candles = self.get_candles(ticker, timeframe)
        return candles[-1] if candles else {}

    def inject_historical(self, ticker: str, candles: list, timeframe: str):
        """
        Pre-loads historical candles directly into completed store.
        Called at startup before live ticks begin.
        candles: list of {open, high, low, close, volume, time} oldest first.
        """
        tf = CANDLE_3M_SEC if timeframe == "3m" else CANDLE_15M_SEC
        existing = self._completed[ticker][tf]
        # Only inject candles older than what we already have
        existing_times = {c["time"] for c in existing}
        new_candles = [c for c in candles if c.get("time") not in existing_times]
        combined = sorted(existing + new_candles, key=lambda c: c.get("time", 0))
        self._completed[ticker][tf] = combined[-self._max_candles:]

    def get_candle_count_all(self) -> dict:
        """Returns {ticker: {3m: count, 15m: count}} for dashboard display."""
        result = {}
        for ticker in self._completed:
            result[ticker] = {
                "3m":  len(self._completed[ticker].get(CANDLE_3M_SEC, [])),
                "15m": len(self._completed[ticker].get(CANDLE_15M_SEC, [])),
            }
        return result

    def reset_ticker(self, ticker: str):
        """Called when a new session starts."""
        self._completed[ticker].clear()
        self._current[ticker].clear()

    def reset_all(self):
        self._completed.clear()
        self._current.clear()


# ── Singleton instance shared across the engine ──
candle_store = CandleStore()
