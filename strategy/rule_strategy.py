"""
KUBER'S CALLING — strategy/rule_strategy.py
=============================================
Layer 3: Phase 1 live strategy — Institutional Absorption Detection.

Implements the Scout + Spy thesis:
  Scout: Volume anomaly (Z-score) + velocity screen
  Spy:   Sector lag + sector slope + candle structure + news filter

All thresholds from config.py. No hardcoded values.

CHANGELOG:
  v5.1 — Fixed duplicate Z-score threshold block (BUG: regime caution
          multiplier was being discarded by a second unconditional reset).
        — Separated vol_vel (volume velocity ratio, displayed/logged)
          from price_vel (snapshot.velocity_ratio, a different metric).
          Dashboard scanner now shows the right number for Vel.
        — Velocity guard kept binary (current > prev fires the signal)
          per operator decision. vol_vel_ratio is logged for future
          threshold calibration once shadow data accumulates.
"""

import os, sys, logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    VOL_Z_SCORE_TRIGGER, SCOUT_K_MULTIPLIER,
    MIN_CANDLES_3M, MIN_CANDLES_15M,
    MAX_Z_SCORE, HIGH_CONVICTION_Z,
    OPEN_Z_MULTIPLIER, NIFTY_DIRECTIONAL_THRESHOLD,
    LAG_THRESHOLD_PCT, CANDLE_TOP_PCT, CANDLE_BOTTOM_PCT,
    SECTOR_SLOPE_PERIOD, MIN_SECTOR_SLOPE_LONG, MAX_SECTOR_SLOPE_SHORT,
    GAP_VIABILITY_ATR_MULT, TARGET_LAG_MULT,
    NEWS_FILTER_ABSOLUTE_DROP, NEWS_FILTER_SECTOR_MOVE_MIN,
    LIMIT_ORDER_OFFSET_PCT, SL_ATR_MULTIPLIER,
    MIN_ATR_PCT,
    MAX_ENTRY_PRICE,
)

from features.feature_engine import RegimeState
from strategy.strategy_base import Strategy, SignalResult

log = logging.getLogger("rule_strategy")


class RuleStrategy(Strategy):
    """
    Institutional absorption detection strategy.
    This is the live strategy from day 1 until the owner deploys an ML model.
    """

    name    = "RULE_V1"
    version = "1.0"
    is_live = True

    def evaluate(self, snapshot) -> SignalResult:
        meta = {}

        # ── Data quality gate ──────────────────────────────────────────
        n3m  = len(snapshot.candles_3m)
        n15m = len(snapshot.candles_15m)
        if n3m < MIN_CANDLES_3M or n15m < MIN_CANDLES_15M:
            meta["reject_reason"] = f"Insufficient candles: 3m={n3m} 15m={n15m}"
            return SignalResult("PASS", metadata=meta)

        # ── Regime gate ────────────────────────────────────────────────
        if snapshot.regime == RegimeState.STANDBY:
            meta["reject_reason"] = "STANDBY regime"
            return SignalResult("PASS", metadata=meta)

        if snapshot.is_midday_blackout:
            meta["reject_reason"] = "MIDDAY_BLACKOUT"
            return SignalResult("PASS", metadata=meta)

        # ── Compute volume velocity ONCE here so we can log it cleanly ──
        # vol_vel_ratio: how much bigger is the current candle's volume
        # ── Compute volume velocity using 15m candles (not 3m).
        # Velocity confirms the lion has been active for TWO consecutive 15m
        # windows — not just one spike. Comparing 3m[-1] vs 3m[-2] could fire
        # on a single large order; 15m[-1] vs 15m[-2] requires sustained flow.
        current_vol  = snapshot.candles_15m[-1].get("volume", 0) if n15m >= 2 else 0
        prev_vol     = snapshot.candles_15m[-2].get("volume", 0) if n15m >= 2 else 0
        vol_growing  = (current_vol > prev_vol)
        # When prev_vol=0 and current_vol>0: genuine new volume appearing — guard passes.
        # Use 9.999 sentinel so display and gate trace both show a high ratio (✓)
        # rather than 0.000 with ✗ which contradicts the guard passing.
        if prev_vol > 0:
            vol_vel_ratio = round(current_vol / prev_vol, 3)
        elif current_vol > 0:
            vol_vel_ratio = 9.999   # new volume where none existed — strongly bullish signal
        else:
            vol_vel_ratio = 0.0     # no volume at all — guard will fail

        # ── Populate meta early — these fields drive the scanner display ─
        meta.update({
            "vol_z":       round(snapshot.vol_z_score, 3),
            "vol_vel":     round(vol_vel_ratio, 3),   # ← volume velocity ratio (for display)
            "price_vel":   round(snapshot.velocity_ratio, 3),  # ← price velocity (different metric)
            "atr_15m":     round(snapshot.atr_15m, 3),
            "regime":      snapshot.regime,
        })

        # ── Scout: Z-score threshold (ONE block — regime caution applied once)
        # Architecture rule: threshold raised to 3.0 in directional market
        # and during open protection. Both conditions are checked here.
        # FIX v5.1: previous code had TWO z_threshold blocks; the second one
        # unconditionally reset z_threshold to base, discarding the regime
        # caution multiplier. Collapsed into a single block.
        z_threshold = VOL_Z_SCORE_TRIGGER

        if snapshot.is_open_protection:
            z_threshold *= OPEN_Z_MULTIPLIER  # raises to 3.0 in first 15m

        if snapshot.regime in (RegimeState.LONG_CAUTION, RegimeState.SHORT_CAUTION):
            z_threshold *= OPEN_Z_MULTIPLIER  # raises to 3.0 in directional market

        meta["z_threshold"] = round(z_threshold, 2)

        if snapshot.vol_z_score < z_threshold:
            meta["reject_reason"] = (
                f"Scout Z fail: {snapshot.vol_z_score:.2f} < {z_threshold:.2f}"
            )
            return SignalResult("PASS", metadata=meta)

        # ── Scout: Volume velocity (growth check) ─────────────────────
        # Thesis: volume must be growing, not just high.
        # Guard is binary — any growth fires. vol_vel_ratio is logged
        # above for future threshold review after shadow data accumulates.
        if not vol_growing:
            meta["reject_reason"] = (
                f"Scout velocity fail: {current_vol:.0f} <= {prev_vol:.0f} "
                f"(ratio {vol_vel_ratio:.3f})"
            )
            return SignalResult("PASS", metadata=meta)

        # ── Scout: Minimum ATR% guard ──────────────────────────────────
        # Block entries where ATR/price < MIN_ATR_PCT (0.3%).
        # Below this threshold the stock has insufficient range to cover
        # transaction costs — a full ATR move won't break even.
        # Day 1: 37 of 38 flash closes (< 1 min hold) were low-ATR tickers.
        # The volume signal is real but price can't move on these tickers.
        if snapshot.price > 0:
            atr_pct = snapshot.atr_15m / snapshot.price
            meta["atr_pct"] = round(atr_pct, 5)
            if atr_pct < MIN_ATR_PCT:
                meta["reject_reason"] = (
                    f"Scout ATR% fail: {atr_pct:.4f} < {MIN_ATR_PCT:.4f} "
                    f"(atr={snapshot.atr_15m:.2f} price={snapshot.price:.2f})"
                )
                return SignalResult("PASS", metadata=meta)
        # ── Scout: Maximum price guard ────────────────────────────────
        # Stocks above MAX_ENTRY_PRICE have too few shares per ₹15K position
        # to exit reliably — thin order books cause FORCE_BOOKED exits.
        # Evidence: SHREECEM ₹23,955 (1 share), ULTRACEMCO ₹11,277 (2 shares).
        if snapshot.price > MAX_ENTRY_PRICE:
            meta["reject_reason"] = (
                f"Scout price fail: ₹{snapshot.price:.0f} > MAX_ENTRY_PRICE ₹{MAX_ENTRY_PRICE:.0f} "
                f"(too few shares per position for reliable exit)"
            )
            return SignalResult("PASS", metadata=meta)

        if snapshot.prev_close > 0:
            abs_move    = abs(snapshot.price - snapshot.prev_close) / snapshot.prev_close * 100
            sector_move = abs(snapshot.sector_lag) * 100
            if (abs_move > NEWS_FILTER_ABSOLUTE_DROP and
                    sector_move < NEWS_FILTER_SECTOR_MOVE_MIN):
                meta["reject_reason"] = (
                    f"NEWS FILTER: stock {abs_move:.1f}% vs sector {sector_move:.2f}%"
                )
                return SignalResult("PASS", metadata=meta)

        # ── Spy: Sector composite available ───────────────────────────
        if not snapshot.sector_composite or len(snapshot.sector_composite) < SECTOR_SLOPE_PERIOD:
            meta["reject_reason"] = (
                f"Insufficient sector composite: {len(snapshot.sector_composite)} points"
            )
            return SignalResult("PASS", metadata=meta)

        # ── Spy: Candle structure (15m candle — matches signal cadence) ──
        latest_15m   = snapshot.candles_15m[-1]
        candle_range = latest_15m["high"] - latest_15m["low"]
        if candle_range <= 0:
            meta["reject_reason"] = "Zero candle range"
            return SignalResult("PASS", metadata=meta)

        close_position = (latest_15m["close"] - latest_15m["low"]) / candle_range
        meta["close_position"] = round(close_position, 3)
        meta["sector_lag"]     = round(snapshot.sector_lag, 4)
        meta["sector_slope"]   = round(snapshot.sector_slope, 6)

        # ── Spy: Direction ─────────────────────────────────────────────
        direction = None

        # Long: stock lagging sector, sector not falling, candle closes in top %
        if (snapshot.sector_lag > LAG_THRESHOLD_PCT / 100 and
                snapshot.sector_slope >= MIN_SECTOR_SLOPE_LONG and
                close_position >= CANDLE_TOP_PCT):
            direction = "LONG"

        # Short: stock leading sector, sector not rising, candle closes in bottom %
        elif (snapshot.sector_lag < -LAG_THRESHOLD_PCT / 100 and
                snapshot.sector_slope <= MAX_SECTOR_SLOPE_SHORT and
                close_position <= CANDLE_BOTTOM_PCT):
            direction = "SHORT"

        if direction is None:
            meta["reject_reason"] = (
                f"No Spy match: lag={snapshot.sector_lag:.4f} "
                f"slope={snapshot.sector_slope:.6f} "
                f"close_pos={close_position:.3f}"
            )
            return SignalResult("PASS", metadata=meta)

        # ── Open protection: block shorts ─────────────────────────────
        if snapshot.is_open_protection and direction == "SHORT":
            meta["reject_reason"] = "OPEN_PROTECTION: no shorts"
            return SignalResult("PASS", metadata=meta)

        # ── Gap viability ──────────────────────────────────────────────
        gap = snapshot.atr_15m * GAP_VIABILITY_ATR_MULT
        meta["gap_required"] = round(gap, 3)
        if gap > snapshot.atr_15m * 2:
            meta["reject_reason"] = "Gap viability: ATR degenerate"
            return SignalResult("PASS", metadata=meta)

        # ── Compute limit price and SL ─────────────────────────────────
        # Limit anchors to live price (snapshot.price).
        # High-conviction (Z >= HIGH_CONVICTION_Z) places limit AT live price.
        # Normal signals add a small offset to improve fill probability.
        if direction == "LONG":
            offset = 0.0 if snapshot.vol_z_score >= HIGH_CONVICTION_Z else LIMIT_ORDER_OFFSET_PCT
            limit  = snapshot.price * (1 + offset)
            sl     = snapshot.price - (SL_ATR_MULTIPLIER * snapshot.atr_15m)
            target = snapshot.price + (snapshot.sector_lag * snapshot.price * TARGET_LAG_MULT)
            side   = "BUY"
        else:
            offset = 0.0 if snapshot.vol_z_score >= HIGH_CONVICTION_Z else LIMIT_ORDER_OFFSET_PCT
            limit  = snapshot.price * (1 - offset)
            sl     = snapshot.price + (SL_ATR_MULTIPLIER * snapshot.atr_15m)
            target = snapshot.price - abs(snapshot.sector_lag * snapshot.price * TARGET_LAG_MULT)
            side   = "SELL"

        meta.update({
            "direction":  direction,
            "conviction": "HIGH" if snapshot.vol_z_score >= HIGH_CONVICTION_Z else "NORMAL",
            "limit_price": round(limit, 2),
            "sl_price":    round(sl, 2),
            "target_price":round(target, 2),
        })

        log.debug("[rule] SIGNAL %s %s z=%.2f lag=%.4f vol_vel=%.3f",
                  direction, snapshot.ticker,
                  snapshot.vol_z_score, snapshot.sector_lag, vol_vel_ratio)

        return SignalResult(
            signal       = side,
            direction    = direction,
            confidence   = 1.0,
            limit_price  = round(limit, 2),
            sl_price     = round(sl, 2),
            target_price = round(target, 2),
            metadata     = meta,
        )