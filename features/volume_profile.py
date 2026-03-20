"""
KUBER'S CALLING — features/volume_profile.py
==============================================
Layer 2: Time-of-day normalised volume Z-scores.

Each ticker's volume is compared against its own historical mean
for the SAME 15-minute time bucket. 09:15 volume is compared against
09:15 history only — not a flat daily mean.

CADENCE: 15m (switched from 3m after backtest — see notes below)
  Backtest result (50 stocks, 60 days, Mar 2026):
    3m: expectancy -0.014%/trade, win/loss ratio 0.99 (coin flip)
   15m: expectancy +0.006%/trade, win/loss ratio 1.11
  Root cause: a single 3m spike can be one large order; 15m sustained
  volume confirms institutional flow has persisted for a full window.

RULES:
  - Pure Python arithmetic — no numpy, no scipy
  - Winsorization applied at 90th percentile to protect baseline from outliers
  - Z-scores hard-capped at MAX_Z_SCORE at point of computation
  - Both the profile path AND the rolling fallback apply the same cap
  - Built at startup from historical_candles DB
  - Rebuilt daily after update_today() completes
"""

import os, sys, sqlite3, logging
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_LIVE_PATH, HISTORY_DAYS_FOR_PROFILE, MAX_Z_SCORE, VOL_Z_SCORE_PERIOD

log = logging.getLogger("volume_profile")

# ── Module-level profile state ───────────────────────────────────────
_profile       = {}       # {ticker: {bucket: (mean, std)}}
_profile_built = False


def _winsorize(values: list, upper_percentile: float = 0.90) -> list:
    """
    Cap extreme right-tail volume spikes to prevent baseline distortion.
    Replaces any value above the target percentile with the percentile value itself.
    """
    n = len(values)
    if n < 3:
        return values
        
    sorted_vals = sorted(values)
    cap_idx = int(n * upper_percentile)
    if cap_idx >= n:
        cap_idx = n - 1
        
    cap_val = sorted_vals[cap_idx]
    return [min(v, cap_val) for v in values]


# ═══════════════════════════════════════════════════════════════════
# BUILD
# ═══════════════════════════════════════════════════════════════════

def build_volume_profile(tickers: list = None) -> int:
    """
    Build per-ticker, per-bucket volume statistics from DB.
    If tickers is None, builds for all tickers in DB.
    Returns count of tickers with profiles built.
    """
    global _profile, _profile_built

    try:
        conn    = sqlite3.connect(DB_LIVE_PATH)
        if tickers:
            placeholders = ",".join(["?"] * len(tickers))
            rows = conn.execute(f"""
                SELECT ticker, time, volume
                FROM historical_candles
                WHERE timeframe='15m' AND ticker IN ({placeholders})
                ORDER BY ticker, time
            """, tickers).fetchall()
        else:
            rows = conn.execute("""
                SELECT ticker, time, volume
                FROM historical_candles
                WHERE timeframe='15m'
                ORDER BY ticker, time
            """).fetchall()
        conn.close()

    except sqlite3.Error as e:
        log.error("[vol_profile] DB read failed: %s", e)
        _profile_built = True  # Allow engine to run with empty profile
        return 0

    # Group volumes by (ticker, HH:MM bucket)
    buckets = defaultdict(lambda: defaultdict(list))
    for ticker, ts, volume in rows:
        try:
            dt     = datetime.fromtimestamp(ts)
            bucket = dt.strftime("%H:%M")
            buckets[ticker][bucket].append(float(volume))
        except (ValueError, OSError):
            continue

    # Compute mean and sample std per bucket
    new_profile = {}
    for ticker, bdata in buckets.items():
        new_profile[ticker] = {}
        for bucket, raw_vols in bdata.items():
            if len(raw_vols) < 2:
                continue
            
            # Protect the baseline from institutional outliers
            vols = _winsorize(raw_vols, upper_percentile=0.90)
            
            mean = sum(vols) / len(vols)
            variance = sum((v - mean) ** 2 for v in vols) / (len(vols) - 1)
            std = variance ** 0.5
            if std > 0:
                new_profile[ticker][bucket] = (mean, std)

    _profile       = new_profile
    _profile_built = True
    total_buckets  = sum(len(v) for v in _profile.values())
    log.info("[vol_profile] Built for %d tickers, %d time buckets (Winsorized)",
             len(_profile), total_buckets)
    return len(_profile)


def get_profile_coverage() -> dict:
    """Return summary stats for smoke test."""
    return {
        "tickers": len(_profile),
        "buckets": sum(len(v) for v in _profile.values()),
    }


# ═══════════════════════════════════════════════════════════════════
# Z-SCORE LOOKUP
# ═══════════════════════════════════════════════════════════════════

def get_volume_z(ticker: str, current_volume: float,
                 dt: datetime = None) -> float:
    """
    Return time-of-day normalised Z-score for current_volume.
    Uses historical profile for the same HH:MM bucket.
    Falls back to rolling Z if no profile available.
    Hard-capped at MAX_Z_SCORE in both paths.

    Returns 0.0 if insufficient data — never raises.
    """
    if dt is None:
        dt = datetime.now()

    bucket = dt.strftime("%H:%M")

    # ── Primary path: time-of-day profile
    ticker_profile = _profile.get(ticker, {})
    if bucket in ticker_profile:
        mean, std = ticker_profile[bucket]
        if std > 0:
            z = (current_volume - mean) / std
            return min(abs(z), MAX_Z_SCORE) * (1 if z >= 0 else -1)

    # ── Fallback: rolling Z from candle store
    return _rolling_z(ticker, current_volume)


def _rolling_z(ticker: str, current_volume: float) -> float:
    """
    Fallback Z-score using last VOL_Z_SCORE_PERIOD candles from candle_store.
    Used when profile is not yet built or bucket has insufficient history.
    Z-score hard-capped at MAX_Z_SCORE.
    """
    try:
        from data.candle_factory import candle_store
        candles = candle_store.get_candles(ticker, "3m")
        if len(candles) < 3:
            return 0.0

        recent_raw = [c["volume"] for c in candles[-VOL_Z_SCORE_PERIOD:]]
        if len(recent_raw) < 3:
            return 0.0

        # Protect the fallback baseline as well
        recent = _winsorize(recent_raw, upper_percentile=0.90)

        mean = sum(recent) / len(recent)
        if mean == 0:
            return 0.0

        variance = sum((v - mean) ** 2 for v in recent) / (len(recent) - 1)
        std = variance ** 0.5
        if std == 0:
            return 0.0

        z = (current_volume - mean) / std
        return min(abs(z), MAX_Z_SCORE) * (1 if z >= 0 else -1)

    except Exception:
        return 0.0