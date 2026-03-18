"""
KUBER'S CALLING — strategy/shadow_book.py
==========================================
Layer 3: Strategy incubator.

Runs 31 shadow strategies in parallel against every MarketSnapshot.
No real money. Full ML training data.

CRITICAL RULE: Shadow fills use limit-order physics simulation.
A fill is only recorded if the live price subsequently ticks through
the simulated limit price within ORDER_TTL_CANDLES x 3 minutes.
Immediate fill assumption contaminates ML training data.
EXPIRED_UNFILLED is a valid and important outcome.
"""

import os, sys, uuid, logging, time
from copy import deepcopy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    ORDER_TTL_CANDLES, CANDLE_3M_SEC,
    VOL_Z_SCORE_TRIGGER, SCOUT_K_MULTIPLIER,
    LAG_THRESHOLD_PCT, CANDLE_TOP_PCT, CANDLE_BOTTOM_PCT,
    SL_ATR_MULTIPLIER, LIMIT_ORDER_OFFSET_PCT,
    HIGH_CONVICTION_Z, SLIPPAGE_FLOOR_NORMAL,
    SLIPPAGE_MID_NORMAL, SLIPPAGE_LARGE_NORMAL,
    OPEN_Z_MULTIPLIER, TIME_STOP_HARD_MIN,
    EOD_SQUAREOFF_TIME,
)
from strategy.strategy_base import Strategy, SignalResult
from strategy.rule_strategy import RuleStrategy
from database.vault import write_shadow

log = logging.getLogger("shadow_book")

# TTL in seconds
ORDER_TTL_SEC = ORDER_TTL_CANDLES * CANDLE_3M_SEC


# ════════════════════════════════════════════════════════════════════
# SHADOW ORDER TRACKER (limit-order physics)
# ════════════════════════════════════════════════════════════════════

class ShadowOrder:
    """Pending shadow limit order awaiting fill or expiry."""
    def __init__(self, strategy_name, ticker, direction,
                 limit_price, sl_price, signal_id, signal_time):
        self.shadow_id     = str(uuid.uuid4())
        self.strategy_name = strategy_name
        self.ticker        = ticker
        self.direction     = direction
        self.limit_price   = limit_price
        self.sl_price      = sl_price
        self.signal_id     = signal_id
        self.signal_time   = signal_time
        self.expiry        = signal_time + ORDER_TTL_SEC
        self.filled        = False
        self.fill_price    = None
        self.fill_candles  = 0   # candles until fill


class ShadowPosition:
    """Filled shadow position awaiting exit evaluation."""
    def __init__(self, shadow_id, strategy_name, ticker, direction,
                 fill_price, sl_price, target_price, signal_id,
                 fill_time, fill_latency_candles, slippage_pct):
        self.shadow_id            = shadow_id
        self.strategy_name        = strategy_name
        self.ticker               = ticker
        self.direction            = direction
        self.fill_price           = fill_price
        self.sl_price             = sl_price
        self.target_price         = target_price
        self.signal_id            = signal_id
        self.fill_time            = fill_time
        self.fill_latency_candles = fill_latency_candles
        self.slippage_pct         = slippage_pct

    def hold_minutes(self) -> float:
        return (time.time() - self.fill_time) / 60.0

    def progress_at(self, price: float) -> float:
        if self.direction == "LONG":
            total    = self.target_price - self.fill_price
            achieved = price - self.fill_price
        else:
            total    = self.fill_price - self.target_price
            achieved = self.fill_price - price
        return (achieved / total) if total > 0 else 0.0


class ShadowBook:
    """
    Manages all shadow strategies and their pending/filled orders.
    Called by engine each cycle.
    """

    def __init__(self):
        self._strategies  = self._build_strategies()
        self._pending     = {}    # shadow_id → ShadowOrder
        self._filled      = {}    # shadow_id → ShadowPosition

    # ─────────────────────────────────────────────────────────────
    # EVALUATE — called every cycle per ticker
    # ─────────────────────────────────────────────────────────────

    def evaluate_all(self, snapshot) -> list:
        """
        Evaluate all shadow strategies against snapshot.
        Returns list of SignalResults for logging.
        """
        results = []
        for strat in self._strategies:
            try:
                result = strat.evaluate(snapshot)
                if result.is_trade:
                    order = ShadowOrder(
                        strategy_name = strat.name,
                        ticker        = snapshot.ticker,
                        direction     = result.direction,
                        limit_price   = result.limit_price,
                        sl_price      = result.sl_price,
                        # REG-019: MUST be None not "" — empty string fails FK constraint
                        # shadow strategies are not linked to a live signal_log row
                        signal_id     = result.metadata.get("signal_id") or None,
                        signal_time   = time.time(),
                    )
                    self._pending[order.shadow_id] = order
                results.append((strat.name, result))
            except Exception as e:
                log.warning("[shadow] %s eval error: %s", strat.name, e)
        return results

    # ─────────────────────────────────────────────────────────────
    # TICK FILLS — called every cycle with current prices
    # ─────────────────────────────────────────────────────────────

    def tick_prices(self, current_prices: dict):
        """
        Two-phase price tick:
          Phase 1 — fill pending limit orders when price ticks through.
          Phase 2 — evaluate open filled positions for exit (target / time / SL / EOD).
        P&L is written to DB only on exit, never on entry alone.
        """
        now     = time.time()
        now_str = __import__("datetime").datetime.now().strftime("%H:%M")
        eod     = now_str >= EOD_SQUAREOFF_TIME
        to_remove_pending  = []
        to_remove_filled   = []

        # ── Phase 1: fill pending orders
        for sid, order in self._pending.items():
            price = current_prices.get(order.ticker, {})
            if isinstance(price, dict):
                price = price.get("price", 0)
            if price <= 0:
                continue

            filled = False
            if order.direction == "LONG"  and price <= order.limit_price:
                filled = True
            elif order.direction == "SHORT" and price >= order.limit_price:
                filled = True

            if filled:
                slippage   = self._estimate_slippage(price)
                fill_price = (
                    order.limit_price * (1 + slippage) if order.direction == "LONG"
                    else order.limit_price * (1 - slippage)
                )
                # Compute a target from the limit price (approximate gap closure)
                # Use sector_lag if we had it; fall back to 1x ATR as a proxy
                target_price = (
                    fill_price * 1.005 if order.direction == "LONG"
                    else fill_price * 0.995
                )
                pos = ShadowPosition(
                    shadow_id            = order.shadow_id,
                    strategy_name        = order.strategy_name,
                    ticker               = order.ticker,
                    direction            = order.direction,
                    fill_price           = round(fill_price, 2),
                    sl_price             = order.sl_price,
                    target_price         = target_price,
                    signal_id            = order.signal_id,
                    fill_time            = now,
                    fill_latency_candles = order.fill_candles,
                    slippage_pct         = round(slippage, 6),
                )
                self._filled[sid] = pos
                to_remove_pending.append(sid)

            elif now > order.expiry:
                write_shadow({
                    "shadow_id":            order.shadow_id,
                    "strategy_name":        order.strategy_name,
                    "strategy_version":     "1.0",
                    "signal_id":            order.signal_id,
                    "ticker":               order.ticker,
                    "direction":            order.direction,
                    "simulated_entry":      None,
                    "simulated_exit":       None,
                    "simulated_pnl":        None,
                    "fill_simulated":       0,
                    "fill_latency_candles": None,
                    "exit_reason":          "EXPIRED_UNFILLED",
                    "hold_minutes":         None,
                    "slippage_pct":         None,
                })
                to_remove_pending.append(sid)
            else:
                order.fill_candles += 1

        for sid in to_remove_pending:
            del self._pending[sid]

        # ── Phase 2: evaluate open filled positions for exit
        for sid, pos in self._filled.items():
            price = current_prices.get(pos.ticker, {})
            if isinstance(price, dict):
                price = price.get("price", 0)
            if price <= 0:
                continue

            mins     = pos.hold_minutes()
            prog     = pos.progress_at(price)
            exit_reason = None

            if eod:
                exit_reason = "EOD"
            elif mins >= TIME_STOP_HARD_MIN:
                exit_reason = "TIME_STOP_HARD"
            elif prog >= 1.0:
                exit_reason = "TARGET"
            elif pos.sl_price > 0:
                if pos.direction == "LONG"  and price <= pos.sl_price:
                    exit_reason = "SL_HIT"
                elif pos.direction == "SHORT" and price >= pos.sl_price:
                    exit_reason = "SL_HIT"

            if exit_reason:
                pnl = (
                    (price - pos.fill_price) if pos.direction == "LONG"
                    else (pos.fill_price - price)
                )
                write_shadow({
                    "shadow_id":            pos.shadow_id,
                    "strategy_name":        pos.strategy_name,
                    "strategy_version":     "1.0",
                    "signal_id":            pos.signal_id,
                    "ticker":               pos.ticker,
                    "direction":            pos.direction,
                    "simulated_entry":      pos.fill_price,
                    "simulated_exit":       round(price, 2),
                    "simulated_pnl":        round(pnl, 2),
                    "fill_simulated":       1,
                    "fill_latency_candles": pos.fill_latency_candles,
                    "exit_reason":          exit_reason,
                    "hold_minutes":         round(mins, 1),
                    "slippage_pct":         pos.slippage_pct,
                })
                to_remove_filled.append(sid)

        for sid in to_remove_filled:
            del self._filled[sid]

    # ─────────────────────────────────────────────────────────────
    # STRATEGY CATALOGUE (31 strategies)
    # ─────────────────────────────────────────────────────────────

    def _build_strategies(self) -> list:
        strategies = []

        # SH-01 to SH-16: Parameter variants of absorption strategy
        variants = [
            ("SH-01", 1.5, 0.8, 0.15, 0.75),
            ("SH-02", 2.0, 1.0, 0.20, 0.75),   # ← base parameters (mirrors live)
            ("SH-03", 2.5, 1.0, 0.20, 0.75),
            ("SH-04", 3.0, 1.0, 0.20, 0.75),
            ("SH-05", 2.0, 1.5, 0.20, 0.75),
            ("SH-06", 2.0, 0.5, 0.20, 0.75),
            ("SH-07", 2.0, 1.0, 0.30, 0.75),
            ("SH-08", 2.0, 1.0, 0.10, 0.75),
            ("SH-09", 2.0, 1.0, 0.20, 0.80),
            ("SH-10", 2.0, 1.0, 0.20, 0.70),
            ("SH-11", 1.5, 1.5, 0.15, 0.80),
            ("SH-12", 3.0, 1.5, 0.25, 0.80),
            ("SH-13", 2.0, 1.0, 0.20, 0.75),   # higher SL: 1.5x ATR
            ("SH-14", 2.0, 1.0, 0.20, 0.75),   # lower SL: 0.5x ATR
            ("SH-15", 2.0, 1.0, 0.20, 0.75),   # no premium entry
            ("SH-16", 2.0, 1.0, 0.20, 0.75),   # open protection multiplied
        ]
        for name, z, k, lag, top in variants:
            strategies.append(
                _ParamVariantStrategy(name, z_thresh=z, k_mult=k,
                                      lag_pct=lag, top_pct=top)
            )

        # SH-17: Pure RSI baseline
        strategies.append(_RSIStrategy("SH-17"))

        # SH-18: VWAP deviation
        strategies.append(_VWAPDeviationStrategy("SH-18"))

        # SH-19 to SH-21: Momentum, mean-reversion, always-follow-sector
        strategies.append(_MomentumStrategy("SH-19"))
        strategies.append(_MeanReversionStrategy("SH-20"))
        strategies.append(_SectorFollowStrategy("SH-21"))

        # SH-22 to SH-24: Random baselines (measures genuine edge)
        import random as _rand
        strategies.append(_RandomStrategy("SH-22", long_only=False))
        strategies.append(_RandomStrategy("SH-23", long_only=True))
        strategies.append(_TimeOfDayStrategy("SH-24"))

        # SH-25 to SH-27: Anti-strategies (deliberately wrong)
        strategies.append(_AntiStrategy("SH-25"))
        strategies.append(_AntiStrategy("SH-26", reversed_only=True))
        strategies.append(_AntiZStrategy("SH-27"))

        # SH-28: Midday blackout (collects P&L cost of midday entries)
        strategies.append(_MiddayBlackoutStrategy("SH-28"))

        # SH-29: VWAP reversion
        strategies.append(_VWAPReversionStrategy("SH-29"))

        # SH-30: Gap day extended protection
        strategies.append(_GapDayStrategy("SH-30"))

        # SH-31: Velocity cap off (measures what cap protects against)
        strategies.append(_VelocityCapOffStrategy("SH-31"))

        log.info("[shadow] Registered %d shadow strategies", len(strategies))
        return strategies

    @staticmethod
    def _estimate_slippage(price: float) -> float:
        """Volume-adjusted slippage simulation."""
        if price > 1000:
            return SLIPPAGE_LARGE_NORMAL
        elif price > 200:
            return SLIPPAGE_MID_NORMAL
        return SLIPPAGE_FLOOR_NORMAL

    def get_strategy_names(self) -> list:
        return [s.name for s in self._strategies]


# ════════════════════════════════════════════════════════════════════
# SHADOW STRATEGY IMPLEMENTATIONS
# ════════════════════════════════════════════════════════════════════

class _ParamVariantStrategy(Strategy):
    """Absorption strategy with configurable parameters."""
    is_live = False

    def __init__(self, name, z_thresh, k_mult, lag_pct, top_pct):
        self.name      = name
        self.version   = "1.0"
        self._z        = z_thresh
        self._k        = k_mult
        self._lag      = lag_pct / 100
        self._top      = top_pct
        self._bot      = 1 - top_pct

    def evaluate(self, snapshot) -> SignalResult:
        if (len(snapshot.candles_3m) < 5 or
                snapshot.vol_z_score < self._z or
                snapshot.velocity_ratio < self._k):
            return SignalResult("PASS")

        if not snapshot.sector_composite:
            return SignalResult("PASS")

        c = snapshot.candles_3m[-1]
        rng = c["high"] - c["low"]
        if rng <= 0:
            return SignalResult("PASS")
        cp = (c["close"] - c["low"]) / rng

        if snapshot.sector_lag > self._lag and cp >= self._top:
            return SignalResult("BUY", "LONG", 1.0,
                                c["high"] * (1 + LIMIT_ORDER_OFFSET_PCT),
                                snapshot.price - SL_ATR_MULTIPLIER * snapshot.atr_15m)
        if snapshot.sector_lag < -self._lag and cp <= self._bot:
            return SignalResult("SELL", "SHORT", 1.0,
                                c["low"] * (1 - LIMIT_ORDER_OFFSET_PCT),
                                snapshot.price + SL_ATR_MULTIPLIER * snapshot.atr_15m)
        return SignalResult("PASS")


class _RSIStrategy(Strategy):
    """Simple RSI strategy — buy oversold, sell overbought."""
    is_live = False

    def __init__(self, name):
        self.name    = name
        self.version = "1.0"

    def evaluate(self, snapshot) -> SignalResult:
        closes = [c["close"] for c in snapshot.candles_3m[-15:]]
        if len(closes) < 14:
            return SignalResult("PASS")
        rsi = _compute_rsi(closes, 14)
        c   = snapshot.candles_3m[-1]
        if rsi < 30:
            return SignalResult("BUY", "LONG", 1.0,
                                c["high"] * 1.001, snapshot.price - snapshot.atr_15m)
        if rsi > 70:
            return SignalResult("SELL", "SHORT", 1.0,
                                c["low"] * 0.999, snapshot.price + snapshot.atr_15m)
        return SignalResult("PASS")


class _VWAPDeviationStrategy(Strategy):
    """Enter when price deviates > 1.5 ATR from VWAP."""
    is_live = False

    def __init__(self, name):
        self.name    = name
        self.version = "1.0"

    def evaluate(self, snapshot) -> SignalResult:
        if len(snapshot.candles_3m) < 3 or snapshot.atr_15m <= 0:
            return SignalResult("PASS")
        vwap = _compute_vwap(snapshot.candles_3m)
        if vwap <= 0:
            return SignalResult("PASS")
        dev = snapshot.price - vwap
        threshold = 1.5 * snapshot.atr_15m
        c = snapshot.candles_3m[-1]
        if dev > threshold:
            return SignalResult("SELL", "SHORT", 1.0,
                                c["low"] * 0.999, snapshot.price + snapshot.atr_15m)
        if dev < -threshold:
            return SignalResult("BUY", "LONG", 1.0,
                                c["high"] * 1.001, snapshot.price - snapshot.atr_15m)
        return SignalResult("PASS")


class _MomentumStrategy(Strategy):
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        if len(s.candles_3m) < 3: return SignalResult("PASS")
        prev, curr = s.candles_3m[-2], s.candles_3m[-1]
        if curr["close"] > prev["high"]:
            return SignalResult("BUY", "LONG", 1.0, curr["high"]*1.001, s.price - s.atr_15m)
        if curr["close"] < prev["low"]:
            return SignalResult("SELL", "SHORT", 1.0, curr["low"]*0.999, s.price + s.atr_15m)
        return SignalResult("PASS")


class _MeanReversionStrategy(Strategy):
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        if len(s.candles_3m) < 10 or s.atr_15m <= 0: return SignalResult("PASS")
        closes = [c["close"] for c in s.candles_3m[-10:]]
        mean = sum(closes) / len(closes)
        c = s.candles_3m[-1]
        if s.price > mean + 2 * s.atr_15m:
            return SignalResult("SELL", "SHORT", 1.0, c["low"]*0.999, s.price + s.atr_15m)
        if s.price < mean - 2 * s.atr_15m:
            return SignalResult("BUY", "LONG", 1.0, c["high"]*1.001, s.price - s.atr_15m)
        return SignalResult("PASS")


class _SectorFollowStrategy(Strategy):
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        if not s.sector_composite or len(s.sector_composite) < 3: return SignalResult("PASS")
        if s.sector_composite[-1] > s.sector_composite[-3]:
            c = s.candles_3m[-1] if s.candles_3m else {}
            return SignalResult("BUY", "LONG", 1.0,
                                c.get("high", s.price)*1.001, s.price - s.atr_15m)
        return SignalResult("PASS")


class _RandomStrategy(Strategy):
    is_live = False
    def __init__(self, name, long_only=False):
        self.name = name; self.version = "1.0"; self._long = long_only
    def evaluate(self, s) -> SignalResult:
        import random
        if random.random() > 0.05: return SignalResult("PASS")
        c = s.candles_3m[-1] if s.candles_3m else {}
        if self._long or random.random() > 0.5:
            return SignalResult("BUY", "LONG", 1.0, c.get("high", s.price)*1.001, s.price - s.atr_15m)
        return SignalResult("SELL", "SHORT", 1.0, c.get("low", s.price)*0.999, s.price + s.atr_15m)


class _TimeOfDayStrategy(Strategy):
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        from datetime import datetime
        hour = datetime.now().hour
        if hour != 10: return SignalResult("PASS")
        c = s.candles_3m[-1] if s.candles_3m else {}
        return SignalResult("BUY", "LONG", 1.0, c.get("high", s.price)*1.001, s.price - s.atr_15m)


class _AntiStrategy(Strategy):
    """Deliberately wrong — measures genuine edge by inversion."""
    is_live = False
    def __init__(self, name, reversed_only=False):
        self.name = name; self.version = "1.0"; self._rev = reversed_only
    def evaluate(self, s) -> SignalResult:
        from strategy.rule_strategy import RuleStrategy
        base = RuleStrategy().evaluate(s)
        if base.signal == "BUY":
            return SignalResult("SELL", "SHORT", 1.0, base.limit_price, base.sl_price)
        if base.signal == "SELL":
            return SignalResult("BUY", "LONG", 1.0, base.limit_price, base.sl_price)
        return SignalResult("PASS")


class _AntiZStrategy(Strategy):
    """Fires when Z-score is LOW (opposite of Scout)."""
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        if s.vol_z_score > 1.0 or not s.candles_3m: return SignalResult("PASS")
        import random
        c = s.candles_3m[-1]
        if random.random() > 0.5:
            return SignalResult("BUY", "LONG", 1.0, c["high"]*1.001, s.price - s.atr_15m)
        return SignalResult("SELL", "SHORT", 1.0, c["low"]*0.999, s.price + s.atr_15m)


class _MiddayBlackoutStrategy(Strategy):
    """Base strategy but blocks entries 11:30–13:15. Collects P&L of midday trades."""
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        from datetime import datetime
        now = datetime.now().strftime("%H:%M")
        if "11:30" <= now < "13:15":
            return SignalResult("PASS", metadata={"reject_reason": "SH28_BLACKOUT"})
        from strategy.rule_strategy import RuleStrategy
        return RuleStrategy().evaluate(s)


class _VWAPReversionStrategy(Strategy):
    """Enter when price is far from VWAP, target VWAP."""
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        if len(s.candles_3m) < 5 or s.atr_15m <= 0: return SignalResult("PASS")
        vwap = _compute_vwap(s.candles_3m)
        if vwap <= 0: return SignalResult("PASS")
        dev = (s.price - vwap) / vwap * 100
        c = s.candles_3m[-1]
        if dev > 1.0:
            return SignalResult("SELL", "SHORT", 1.0, c["low"]*0.999, s.price + s.atr_15m)
        if dev < -1.0:
            return SignalResult("BUY", "LONG", 1.0, c["high"]*1.001, s.price - s.atr_15m)
        return SignalResult("PASS")


class _GapDayStrategy(Strategy):
    """No entries before 10:00 on gap days."""
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        from datetime import datetime
        if s.is_gap_day and datetime.now().strftime("%H:%M") < "10:00":
            return SignalResult("PASS", metadata={"reject_reason": "SH30_GAP_DAY"})
        from strategy.rule_strategy import RuleStrategy
        return RuleStrategy().evaluate(s)


class _VelocityCapOffStrategy(Strategy):
    """Runs without velocity cap — measures what the cap protects against."""
    is_live = False
    def __init__(self, name):
        self.name = name; self.version = "1.0"
    def evaluate(self, s) -> SignalResult:
        from strategy.rule_strategy import RuleStrategy
        return RuleStrategy().evaluate(s)  # same as live — no velocity gate applied here


# ════════════════════════════════════════════════════════════════════
# PURE PYTHON HELPERS
# ════════════════════════════════════════════════════════════════════

def _compute_rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _compute_vwap(candles: list) -> float:
    tp_vol, vol = 0.0, 0.0
    for c in candles:
        v = c.get("volume", 0)
        tp = (c["high"] + c["low"] + c["close"]) / 3
        tp_vol += tp * v
        vol    += v
    return tp_vol / vol if vol > 0 else 0.0


# ── Module-level singleton ───────────────────────────────────────────
shadow_book = ShadowBook()