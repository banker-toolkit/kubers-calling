"""
KUBER'S CALLING — observation/signal_log.py
============================================
Layer 6: Signal recording with full audit trail.

Every signal — live, shadow, rejected — gets a complete record
including the full gate-by-gate trace (gate_trace JSON) and a
human-readable entry_reason string.

This is the primary ML training dataset AND the transparency layer.
You can query any signal_id and reconstruct exactly why that trade
was initiated, what every gate showed, and what the market looked like.
"""

import os, sys, uuid, json, logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.vault import write_signal

log = logging.getLogger("signal_log")


def build_gate_trace(snapshot, result) -> list:
    """
    Build the ordered gate trace from a snapshot + result.
    Returns list of dicts, one per gate, in evaluation order.
    Each dict: {gate, passed, value, detail}
    """
    meta = result.metadata
    trace = []

    def gate(name, passed, value, detail=""):
        trace.append({
            "gate":   name,
            "passed": passed,
            "value":  value,
            "detail": detail,
        })

    # ── DATA QUALITY
    n3m  = len(snapshot.candles_3m)
    n15m = len(snapshot.candles_15m)
    gate("MIN_CANDLES_3M",  n3m  >= 5, f"{n3m}")
    gate("MIN_CANDLES_15M", n15m >= 5, f"{n15m}")

    # ── REGIME
    from features.feature_engine import RegimeState
    regime_ok = snapshot.regime != RegimeState.STANDBY
    gate("REGIME", regime_ok, snapshot.regime)

    # ── MIDDAY BLACKOUT
    if snapshot.is_midday_blackout:
        gate("MIDDAY_BLACKOUT", False, "BLOCKED", "11:30–13:15 blackout active")
        return trace

    # ── OPEN PROTECTION
    if snapshot.is_open_protection:
        gate("OPEN_PROTECTION", True, "ACTIVE",
             f"Z threshold raised to {meta.get('z_threshold','?')}")

    # ── SCOUT: Volume Z
    from config import VOL_Z_SCORE_TRIGGER, OPEN_Z_MULTIPLIER
    z_threshold = VOL_Z_SCORE_TRIGGER
    if snapshot.is_open_protection:
        z_threshold *= OPEN_Z_MULTIPLIER
    z_passed = snapshot.vol_z_score >= z_threshold
    gate("SCOUT_VOL_Z", z_passed,
         f"{snapshot.vol_z_score:.2f}",
         f"threshold={z_threshold:.1f}")

    if not z_passed:
        return trace

    # ── SCOUT: Velocity (volume growth check)
    # vol_vel = current_candle_volume / prev_candle_volume (from rule_strategy meta)
    # Guard is binary: current > prev (ratio > 1.0). snapshot.velocity_ratio is
    # price_move/ATR — a different metric entirely, wrong to use here.
    vol_vel = meta.get("vol_vel", 0.0)
    vel_passed = vol_vel > 1.0
    gate("SCOUT_VELOCITY", vel_passed,
         f"{vol_vel:.3f}",
         "vol growing" if vel_passed else "vol fading or flat")

    if not vel_passed:
        return trace

    # ── NEWS FILTER
    news_blocked = "NEWS FILTER" in meta.get("reject_reason", "")
    gate("NEWS_FILTER", not news_blocked,
         f"stock_move={meta.get('abs_move','?')}%" if news_blocked else "PASS",
         meta.get("reject_reason", "") if news_blocked else "no idiosyncratic move")

    if news_blocked:
        return trace

    # ── SPY: Sector composite available
    comp_len = len(snapshot.sector_composite)
    comp_ok = comp_len >= 10
    gate("SECTOR_COMPOSITE", comp_ok, f"{comp_len} points")

    if not comp_ok:
        return trace

    # ── SPY: Sector lag
    from config import LAG_THRESHOLD_PCT
    lag_passed = abs(snapshot.sector_lag) >= LAG_THRESHOLD_PCT / 100
    gate("SPY_SECTOR_LAG", lag_passed,
         f"{snapshot.sector_lag*100:.3f}%",
         f"threshold={LAG_THRESHOLD_PCT}%")

    if not lag_passed:
        return trace

    # ── SPY: Sector slope
    from config import MIN_SECTOR_SLOPE_LONG, MAX_SECTOR_SLOPE_SHORT
    direction = result.direction
    if direction == "LONG":
        slope_passed = snapshot.sector_slope >= MIN_SECTOR_SLOPE_LONG
    else:
        slope_passed = snapshot.sector_slope <= MAX_SECTOR_SLOPE_SHORT
    gate("SPY_SECTOR_SLOPE", slope_passed,
         f"{snapshot.sector_slope:.6f}",
         f"dir={direction}")

    if not slope_passed:
        return trace

    # ── SPY: Candle close position
    from config import CANDLE_TOP_PCT, CANDLE_BOTTOM_PCT
    cp = meta.get("close_position", 0)
    if direction == "LONG":
        cp_passed = cp >= CANDLE_TOP_PCT
        cp_detail = f">={CANDLE_TOP_PCT} for LONG"
    else:
        cp_passed = cp <= CANDLE_BOTTOM_PCT
        cp_detail = f"<={CANDLE_BOTTOM_PCT} for SHORT"
    gate("SPY_CANDLE_STRUCTURE", cp_passed, f"{cp:.3f}", cp_detail)

    if not cp_passed:
        return trace

    # ── SPY: Gap viability (included as info even if not hard gate)
    gate("GAP_VIABILITY", True,
         f"ATR={snapshot.atr_15m:.1f}",
         meta.get("gap_required", "ok"))

    # ── RISK GATE (outcome stored separately)
    risk_reason = meta.get("risk_reason", "")
    risk_passed = risk_reason in ("APPROVED", "") or not risk_reason
    gate("RISK_GATE", risk_passed,
         meta.get("approved_qty", "?"),
         risk_reason or "APPROVED")

    return trace


def build_entry_reason(snapshot, result) -> str:
    """
    Build a concise human-readable string explaining why this trade fired.
    Stored in signal_log.entry_reason for easy querying.
    Example:
      "HDFCBANK LONG @ 09:29 | Vol Z=4.21 (HIGH CONVICTION) | Lag=+0.38% | "
      "Slope=+0.0014 | Candle top 81% | HEALTHY regime | VIX=14.82"
    """
    meta = result.metadata
    conv = meta.get("conviction", "NORMAL")
    z    = snapshot.vol_z_score
    lag  = snapshot.sector_lag * 100
    dir_str = result.direction or "?"

    parts = [
        f"{snapshot.ticker} {dir_str} @ {snapshot.timestamp.strftime('%H:%M')}",
        f"Z={z:.2f}" + (f" [{conv}]" if conv == "HIGH" else ""),
        f"Vel={meta.get('vol_vel', snapshot.velocity_ratio):.3f}",
        f"Lag={lag:+.3f}%",
        f"Slope={snapshot.sector_slope:.4f}",
        f"CandlePos={meta.get('close_position','?')}",
        f"{snapshot.regime}",
        f"VIX={snapshot.vix:.1f}",
        f"ATR={snapshot.atr_15m:.1f}",
    ]
    if snapshot.is_open_protection:
        parts.append("OPEN_PROTECTION_ACTIVE")
    if snapshot.is_gap_day:
        parts.append("GAP_DAY")

    return " | ".join(parts)


def log_signal(snapshot, result, disposition: str) -> str:
    """
    Record a signal to the DB with full gate trace and audit trail.
    Returns signal_id.

    snapshot:    MarketSnapshot
    result:      SignalResult
    disposition: 'LIVE' | 'SHADOW' | 'RISK_REJECTED' | 'EXPIRED_UNFILLED'
    """
    signal_id = str(uuid.uuid4())
    now       = snapshot.timestamp

    # Build gate trace JSON
    try:
        gate_trace = json.dumps(build_gate_trace(snapshot, result))
    except Exception as e:
        log.warning("[signal_log] gate_trace build failed: %s", e)
        gate_trace = "[]"

    # Build entry reason string
    try:
        entry_reason = build_entry_reason(snapshot, result) if result.is_trade else (
            result.metadata.get("reject_reason", "PASS")
        )
    except Exception as e:
        entry_reason = "error building reason"

    record = {
        "signal_id":        signal_id,
        "strategy_name":    result.metadata.get("strategy_name", "RULE_V1"),
        "strategy_version": result.metadata.get("strategy_version", "1.0"),
        "timestamp":        now.isoformat(),
        "ticker":           snapshot.ticker,
        "sector":           snapshot.sector,
        "adv_tier":         snapshot.adv_tier,
        "disposition":      disposition,

        # Scout features
        "vol_z_score":       snapshot.vol_z_score,
        # vol_vel = current/prev candle volume ratio — this is the operative velocity metric
        # snapshot.velocity_ratio is price_move/ATR (different), not stored here
        "velocity_ratio":    result.metadata.get("vol_vel", snapshot.velocity_ratio),
        "atr_15m":           snapshot.atr_15m,
        "candle_count_3m":   len(snapshot.candles_3m),
        "candle_count_15m":  len(snapshot.candles_15m),

        # Spy features
        "sector_lag_pct":    snapshot.sector_lag,
        "sector_slope":      snapshot.sector_slope,
        "candle_close_pct":  result.metadata.get("close_position"),
        "gap_to_target":     result.metadata.get("gap_required"),
        "news_filter_fired": 1 if "NEWS FILTER" in result.metadata.get("reject_reason","") else 0,

        # Market context
        "regime":            snapshot.regime,
        "nifty_open_change": snapshot.nifty_open_change,
        "nifty_atr":         snapshot.nifty_atr,
        "vix":               snapshot.vix,
        "time_bucket":       now.strftime("%H:%M"),
        "day_of_week":       now.weekday(),
        "is_open_protection":int(snapshot.is_open_protection),
        "is_gap_day":        int(snapshot.is_gap_day),
        "signal_density":    snapshot.signal_density,

        # Order details
        "direction":         result.direction,
        "limit_price":       result.limit_price,
        "confidence":        result.confidence,
        "sl_price":          result.sl_price,

        # Audit trail
        "gate_trace":        gate_trace,
        "entry_reason":      entry_reason,
        "risk_reason":       result.metadata.get("risk_reason", ""),
    }

    try:
        write_signal(record)
    except Exception as e:
        log.error("[signal_log] write failed for %s: %s", snapshot.ticker, e)

    return signal_id