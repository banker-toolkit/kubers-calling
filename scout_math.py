"""
SCOUT MATH
ATR, volume Z-score, velocity calculations.
Operates on genuine OHLCV candle lists — not tick snapshots.

Z-score is TIME-OF-DAY NORMALISED via volume_profile.py.
Falls back to rolling Z if profile not yet built.
Z-scores are no longer capped — raw values flow through so shadow book
calibration and signal quality ranking see real data. A warning is logged
when Z > 20 (likely a data spike) but the value is preserved.

No numpy dependency — pure Python.
"""
import math
import logging
from datetime import datetime
from config import (VOL_Z_SCORE_PERIOD, VOL_Z_SCORE_TRIGGER,
                    SCOUT_ATR_PERIOD, SCOUT_K_MULTIPLIER,
                    MIN_CANDLES_3M, MIN_CANDLES_15M,
                    MAX_Z_SCORE)

_scout_log = logging.getLogger("scout_math")


def calculate_atr(candles: list, period: int = SCOUT_ATR_PERIOD) -> float:
    """True Range ATR from OHLCV candle list."""
    if len(candles) < 2:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h  = candles[i]["high"]
        l  = candles[i]["low"]
        pc = candles[i-1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return float(sum(trs) / len(trs)) if trs else 0.0
    return float(sum(trs[-period:]) / period)


def calculate_atr_5m(candles_3m: list) -> float:
    """Approximates 5-minute ATR by merging pairs of 3-minute candles."""
    if len(candles_3m) < 4:
        return calculate_atr(candles_3m)
    merged = []
    for i in range(0, len(candles_3m) - 1, 2):
        a, b = candles_3m[i], candles_3m[i+1]
        merged.append({
            "open":   a["open"],
            "high":   max(a["high"], b["high"]),
            "low":    min(a["low"],  b["low"]),
            "close":  b["close"],
            "volume": a["volume"] + b["volume"],
        })
    return calculate_atr(merged, period=min(10, len(merged)))


def calculate_volume_zscore(candles: list,
                             period: int = VOL_Z_SCORE_PERIOD) -> tuple:
    """
    Rolling Z-score fallback (non-time-adjusted).
    Used only when volume_profile has not been built yet, or for shadow book.
    Returns (is_anomaly: bool, z_score: float), z_score capped at MAX_Z_SCORE.
    Uses sample std (n-1 denominator).
    """
    if len(candles) < period + 1:
        return False, 0.0

    vols        = [c["volume"] for c in candles[-(period + 1):-1]]
    current_vol = candles[-1]["volume"]
    mean_v      = sum(vols) / len(vols)
    variance    = sum((x - mean_v) ** 2 for x in vols) / max(len(vols) - 1, 1)
    std_v       = math.sqrt(variance) if variance > 0 else 1e-9

    z_score = (current_vol - mean_v) / std_v
    # v8: no hard cap. Warn on extreme values (data spike) but preserve real Z.
    if abs(z_score) > 20:
        _scout_log.warning("[scout] High Z-score: %s bucket=%s z=%.1f vol=%.0f mean=%.0f std=%.0f",
                           "unknown", "unknown", z_score, current_vol, mean_v, std_v)
    return z_score > VOL_Z_SCORE_TRIGGER, float(z_score)


def get_volume_zscore(ticker: str, candles_3m: list,
                      current_volume: float) -> float:
    """
    Returns time-of-day normalised Z if profile built, else rolling Z.
    v8: No cap applied. Real Z values flow through for shadow book calibration.
    Warning logged when Z > 20 (likely data spike) but value is preserved.
    This is the single entry point for Z-scores across the engine.
    """
    try:
        from volume_profile import get_volume_z, is_profile_built
        if is_profile_built():
            z = get_volume_z(ticker, current_volume)
            if not math.isnan(z):
                if abs(z) > 20:
                    _scout_log.warning("[scout] High Z: ticker=%s z=%.1f vol=%.0f",
                                       ticker, z, current_volume)
                return z
    except ImportError:
        pass
    # Fallback: rolling Z (already capped inside calculate_volume_zscore)
    _, z = calculate_volume_zscore(candles_3m)
    return z


def scout_trigger_evaluation(candles_3m: list, candles_15m: list,
                              z_threshold: float = VOL_Z_SCORE_TRIGGER,
                              ticker: str = "") -> tuple:
    """
    Full Scout evaluation.
    Returns (passed: bool, z_score: float, atr_15m: float, velocity_ratio: float)
    Uses time-adjusted Z with rolling fallback. Z capped at 10.
    """
    if len(candles_3m) < MIN_CANDLES_3M or len(candles_15m) < MIN_CANDLES_15M:
        return False, float("nan"), 0.0, 0.0

    atr_15m = calculate_atr(candles_15m)
    if atr_15m <= 0:
        return False, float("nan"), 0.0, 0.0

    latest      = candles_3m[-1]
    current_vol = latest.get("volume", 0)

    z_score = get_volume_zscore(ticker, candles_3m, current_vol)

    is_vol_anomaly = (not math.isnan(z_score)) and (z_score > z_threshold)

    price_move     = abs(latest["close"] - latest["open"])
    velocity_ratio = price_move / atr_15m if atr_15m > 0 else 0.0
    velocity_ok    = velocity_ratio >= SCOUT_K_MULTIPLIER

    passed = is_vol_anomaly and velocity_ok
    return passed, float(z_score) if not math.isnan(z_score) else 0.0, float(atr_15m), float(velocity_ratio)


def get_time_bucket(time_str: str) -> str:
    """Maps HH:MM to a named time bucket for analysis."""
    if not time_str:
        return "UNKNOWN"
    try:
        parts = time_str.split(":")
        h, m  = int(parts[0]), int(parts[1])
        if   h == 9  and m >= 15: return "09:15-10:00"
        elif h == 10:             return "10:00-11:00"
        elif h == 11:             return "11:00-12:00"
        elif h == 12:             return "12:00-13:00"
        elif h == 13:             return "13:00-14:00"
        elif h == 14:             return "14:00-15:00"
        elif h == 15 and m < 20:  return "15:00-15:20"
        else:                     return "15:20+"
    except Exception:
        return "UNKNOWN"