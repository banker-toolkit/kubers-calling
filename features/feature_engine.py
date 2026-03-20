"""
KUBER'S CALLING — features/feature_engine.py
==============================================
Layer 2: MarketSnapshot assembly.

Assembles exactly ONE complete MarketSnapshot per ticker per cycle.
Every strategy — live and shadow — receives this and ONLY this.
No strategy reaches into the candle store, API, or database directly.

This is the contract that makes the strategy layer swappable.
Replace the decision logic, not this assembly layer.
"""

import os, sys, logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    MIN_CANDLES_3M, MIN_CANDLES_15M,
    VIX_FLOOR_ABSOLUTE, VIX_CEILING_ABSOLUTE,
    NIFTY_ATR_FLOOR_ABSOLUTE,
    DYNAMIC_REGIME_WINDOW, DYNAMIC_VIX_MULT,
    DYNAMIC_ATR_MULT, NIFTY_VOLUME_MULT,
    NIFTY_DIRECTIONAL_THRESHOLD,
    NIFTY_HARD_DIRECTIONAL_THRESHOLD,
    OPEN_PROTECTION_END, GAP_DAY_PROTECTION_END,
    GAP_DAY_NIFTY_THRESHOLD, ENABLE_GAP_DAY_EXTENSION,
    ENABLE_MIDDAY_BLACKOUT, MIDDAY_BLACKOUT_START, MIDDAY_BLACKOUT_END,
    VELOCITY_CAP_WINDOW_SEC,
    SCOUT_ATR_PERIOD,
)

log = logging.getLogger("feature_engine")


# ═══════════════════════════════════════════════════════════════════
# REGIME STATE
# ═══════════════════════════════════════════════════════════════════

class RegimeState:
    HEALTHY               = "HEALTHY"
    STANDBY               = "STANDBY"
    LONG_CAUTION          = "LONG_CAUTION"    # NIFTY down > threshold
    SHORT_CAUTION         = "SHORT_CAUTION"   # NIFTY up > threshold


# ═══════════════════════════════════════════════════════════════════
# MARKET SNAPSHOT — complete feature set for one ticker, one cycle
# ═══════════════════════════════════════════════════════════════════

@dataclass
class MarketSnapshot:
    # Identity
    ticker:              str
    timestamp:           datetime
    price:               float
    sector:              str
    adv_tier:            str             # 'HIGH' | 'MID' | 'LOW'

    # Candle series (complete candles only)
    candles_3m:          List[dict]      = field(default_factory=list)
    candles_15m:         List[dict]      = field(default_factory=list)

    # Computed features
    vol_z_score:         float           = 0.0  # time-of-day normalised, capped
    atr_15m:             float           = 0.0
    velocity_ratio:      float           = 0.0  # abs(close-open) / atr_15m
    sector_composite:    List[float]     = field(default_factory=list)
    sector_lag:          float           = 0.0
    sector_slope:        float           = 0.0
    prev_close:          float           = 0.0  # from history_store

    # Market context — MUST be non-zero during market hours
    nifty_price:         float           = 0.0
    nifty_open:          float           = 0.0
    nifty_open_change:   float           = 0.0  # (price - open) / open
    nifty_atr:           float           = 0.0  # 15m ATR
    vix:                 float           = 0.0

    # Regime
    regime:              str             = RegimeState.STANDBY
    is_open_protection:  bool            = False
    is_gap_day:          bool            = False
    is_midday_blackout:  bool            = False
    signal_density:      int             = 0    # signals in last window


# ═══════════════════════════════════════════════════════════════════
# FEATURE ENGINE
# ═══════════════════════════════════════════════════════════════════

class FeatureEngine:
    """
    Assembles MarketSnapshot for every ticker every cycle.
    Holds rolling state for regime computation.
    One instance lives for the full session.
    """

    def __init__(self):
        self._vix_history   = []   # rolling VIX readings
        self._atr_history   = []   # rolling NIFTY ATR readings
        self._vol_history   = []   # rolling NIFTY volume readings
        self._signal_times  = []   # recent signal timestamps (all tickers)
        self._sector_opens  = {}   # {sector: open_composite_price}
        self._ticker_opens  = {}   # {ticker: open_price}
        self._nifty_open    = 0.0

    # ─────────────────────────────────────────────────────────────
    # REGIME CHECK — called once per cycle before stock loop
    # ─────────────────────────────────────────────────────────────

    def check_regime(self, nifty_price: float, nifty_volume: float,
                     nifty_atr: float, vix: float) -> str:
        """
        Evaluate market regime using current NIFTY and VIX data.
        Updates rolling history.
        Returns RegimeState string.

        RULE: If NIFTY price, ATR, or VIX is zero → STANDBY.
        Missing market context means no trading.
        """
        # Zero check — data error → STANDBY
        if nifty_price <= 0 or nifty_atr <= 0 or vix <= 0:
            log.warning("[regime] STANDBY — zero market data (nifty=%.0f atr=%.1f vix=%.1f)",
                        nifty_price, nifty_atr, vix)
            return RegimeState.STANDBY

        # Update rolling history
        self._vix_history.append(vix)
        self._atr_history.append(nifty_atr)
        self._vol_history.append(nifty_volume)
        if len(self._vix_history) > DYNAMIC_REGIME_WINDOW * 3:
            self._vix_history = self._vix_history[-DYNAMIC_REGIME_WINDOW * 3:]
            self._atr_history = self._atr_history[-DYNAMIC_REGIME_WINDOW * 3:]
            self._vol_history = self._vol_history[-DYNAMIC_REGIME_WINDOW * 3:]

        # ── Hard absolute floors
        if not (VIX_FLOOR_ABSOLUTE < vix < VIX_CEILING_ABSOLUTE):
            return RegimeState.STANDBY
        if nifty_atr < NIFTY_ATR_FLOOR_ABSOLUTE:
            return RegimeState.STANDBY

        # ── Dynamic thresholds (active once enough history)
        if len(self._vix_history) >= DYNAMIC_REGIME_WINDOW:
            vix_mean = sum(self._vix_history[-DYNAMIC_REGIME_WINDOW:]) / DYNAMIC_REGIME_WINDOW
            atr_mean = sum(self._atr_history[-DYNAMIC_REGIME_WINDOW:]) / DYNAMIC_REGIME_WINDOW
            vol_mean = sum(self._vol_history[-DYNAMIC_REGIME_WINDOW:]) / DYNAMIC_REGIME_WINDOW

            if vix < vix_mean * DYNAMIC_VIX_MULT:
                return RegimeState.STANDBY
            if nifty_atr < atr_mean * DYNAMIC_ATR_MULT:
                return RegimeState.STANDBY
            if nifty_volume > 0 and nifty_volume < vol_mean * NIFTY_VOLUME_MULT:
                return RegimeState.STANDBY

        # ── Directional regime
        if self._nifty_open > 0:
            change = (nifty_price - self._nifty_open) / self._nifty_open
            if change < -NIFTY_DIRECTIONAL_THRESHOLD:
                return RegimeState.LONG_CAUTION
            if change > NIFTY_DIRECTIONAL_THRESHOLD:
                return RegimeState.SHORT_CAUTION

        return RegimeState.HEALTHY

    def set_nifty_open(self, nifty_open: float):
        """Called once at session start with today's NIFTY open."""
        self._nifty_open = nifty_open

    def record_signal(self):
        """Called when any signal fires — tracked for signal_density."""
        import time
        self._signal_times.append(time.time())
        # Prune old
        cutoff = time.time() - VELOCITY_CAP_WINDOW_SEC
        self._signal_times = [t for t in self._signal_times if t > cutoff]

    def get_signal_density(self) -> int:
        """Count of signals in last VELOCITY_CAP_WINDOW_SEC seconds."""
        import time
        cutoff = time.time() - VELOCITY_CAP_WINDOW_SEC
        return sum(1 for t in self._signal_times if t > cutoff)

    # ─────────────────────────────────────────────────────────────
    # SNAPSHOT ASSEMBLY — called per ticker per cycle
    # ─────────────────────────────────────────────────────────────

    def build(self, ticker: str, price: float, volume: float,
              sector: str, adv_tier: str,
              sector_peer_closes: dict,
              nifty_price: float, nifty_open: float,
              nifty_atr: float, vix: float,
              regime: str) -> MarketSnapshot:
        """
        Assemble a complete MarketSnapshot for one ticker.
        All strategy evaluation starts from this object.
        """
        from data.candle_factory import candle_store
        from data.history_store import get_prev_close
        from features.volume_profile import get_volume_z
        from features.sector_builder import (
            build_ex_self_composite,
            compute_sector_lag,
            compute_sector_slope,
        )

        now = datetime.now()

        # ── Candles
        candles_3m  = candle_store.get_candles(ticker, "3m")
        candles_15m = candle_store.get_candles(ticker, "15m")

        # ── ATR from 15m candles
        atr_15m = _compute_atr(candles_15m, SCOUT_ATR_PERIOD)

        # ── Velocity (current open 3m candle)
        velocity_ratio = 0.0
        open_candle    = candle_store.get_current_open(ticker, "3m")
        if open_candle and atr_15m > 0:
            move           = abs(open_candle["close"] - open_candle["open"])
            velocity_ratio = move / atr_15m

        # ── Volume Z-score — use latest COMPLETED 15m candle volume.
        # Backtest (50 stocks, 60 days) confirmed 15m cadence gives positive
        # expectancy (+0.006%/trade, win/loss 1.11) vs 3m cadence which is a
        # near-coin-flip (-0.014%/trade, win/loss 0.99). A single 3m spike can
        # be one large order; 15m sustained volume confirms the lion has been
        # feeding for a full window, not just touched the tape.
        if candles_15m:
            vol_for_z = candles_15m[-1]["volume"]
            from datetime import datetime as _dt
            ts_for_z  = _dt.fromtimestamp(candles_15m[-1]["time"])
        else:
            vol_for_z = volume
            ts_for_z  = now
        vol_z = get_volume_z(ticker, vol_for_z, ts_for_z)

        # ── Ex-self sector composite
        composite = build_ex_self_composite(ticker, sector_peer_closes)

        # ── Sector lag
        ticker_open = self._ticker_opens.get(ticker, price)
        sector_open = self._sector_opens.get(sector, composite[-1] if composite else 0.0)
        lag = compute_sector_lag(
            [price], composite, ticker_open, sector_open
        ) if composite else 0.0

        # ── Sector slope
        slope = compute_sector_slope(composite, SCOUT_ATR_PERIOD) if composite else 0.0

        # ── NIFTY context
        nifty_change = (
            (nifty_price - nifty_open) / nifty_open
            if nifty_open > 0 else 0.0
        )

        # ── Timing flags
        now_str = now.strftime("%H:%M")
        is_gap_day = (
            ENABLE_GAP_DAY_EXTENSION and
            abs(nifty_change) > GAP_DAY_NIFTY_THRESHOLD
        )
        open_end = GAP_DAY_PROTECTION_END if is_gap_day else OPEN_PROTECTION_END
        is_open_prot = _time_in_window(now_str, "09:15", open_end)
        is_midday    = (
            ENABLE_MIDDAY_BLACKOUT and
            _time_in_window(now_str, MIDDAY_BLACKOUT_START, MIDDAY_BLACKOUT_END)
        )

        return MarketSnapshot(
            ticker           = ticker,
            timestamp        = now,
            price            = price,
            sector           = sector,
            adv_tier         = adv_tier,
            candles_3m       = candles_3m,
            candles_15m      = candles_15m,
            vol_z_score      = vol_z,
            atr_15m          = atr_15m,
            velocity_ratio   = velocity_ratio,
            sector_composite = composite,
            sector_lag       = lag,
            sector_slope     = slope,
            prev_close       = get_prev_close(ticker),
            nifty_price      = nifty_price,
            nifty_open       = nifty_open,
            nifty_open_change= nifty_change,
            nifty_atr        = nifty_atr,
            vix              = vix,
            regime           = regime,
            is_open_protection = is_open_prot,
            is_gap_day       = is_gap_day,
            is_midday_blackout = is_midday,
            signal_density   = self.get_signal_density(),
        )

    def record_opens(self, ticker: str, price: float,
                     sector: str, sector_price: float):
        """Record today's open prices for lag computation."""
        self._ticker_opens.setdefault(ticker, price)
        self._sector_opens.setdefault(sector, sector_price)


# ═══════════════════════════════════════════════════════════════════
# ATR COMPUTATION — pure Python
# ═══════════════════════════════════════════════════════════════════

def _compute_atr(candles: list, period: int = 10) -> float:
    """
    Average True Range over last `period` candles.
    MODIFIED: Ignores overnight gaps so morning velocity isn't artificially crushed.
    """
    if len(candles) < 2:
        return 0.0

    trs = []
    for i in range(1, len(candles)):
        c    = candles[i]
        prev = candles[i - 1]
        
        # Check if this candle is the first of a new day (more than 2 hours gap)
        is_new_day = False
        if "time" in c and "time" in prev:
            if (c["time"] - prev["time"]) > 7200: 
                is_new_day = True

        if is_new_day:
            # First candle: ignore yesterday's close, just use High minus Low
            tr = c["high"] - c["low"]
        else:
            # Normal candle: standard True Range formula
            tr = max(
                c["high"] - c["low"],
                abs(c["high"] - prev["close"]),
                abs(c["low"]  - prev["close"]),
            )
        trs.append(tr)

    if not trs:
        return 0.0

    recent = trs[-period:]
    return sum(recent) / len(recent)




def _time_in_window(now_str: str, start: str, end: str) -> bool:
    """Check if now_str ('HH:MM') is within [start, end)."""
    try:
        from datetime import time as dtime
        t = dtime(*map(int, now_str.split(":")))
        s = dtime(*map(int, start.split(":")))
        e = dtime(*map(int, end.split(":")))
        return s <= t < e
    except ValueError:
        return False


# ── Module-level singleton ───────────────────────────────────────────
feature_engine = FeatureEngine()
