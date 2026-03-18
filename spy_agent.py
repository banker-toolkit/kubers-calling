"""
THE SPY — Direction Decision
Uses ex-self sector composite (stock excluded from its own benchmark).
Returns LONG, SHORT, or PASS with full telemetry.

NEWS FILTER (Gemini fix #2):
If the stock is moving sharply in absolute terms but the sector is not,
that is idiosyncratic news (CEO resignation, regulatory action, earnings leak)
not institutional absorption. We refuse to step in front of informed flow.

No numpy or scipy dependencies — pure Python only.
"""
import math
from config import (CANDLE_TOP_PCT, CANDLE_BOTTOM_PCT, SECTOR_SLOPE_PERIOD,
                    MIN_SECTOR_SLOPE_LONG, MAX_SECTOR_SLOPE_SHORT,
                    LAG_THRESHOLD_PCT, SCOUT_ATR_PERIOD,
                    NEWS_FILTER_ABSOLUTE_DROP, NEWS_FILTER_SECTOR_MOVE_MIN)


def calculate_sector_slope(closes: list, period: int = SECTOR_SLOPE_PERIOD) -> float:
    """Linear regression slope over last N closes. Pure Python, no scipy."""
    if len(closes) < 3:
        return 0.0
    y = closes[-period:] if len(closes) >= period else closes
    n = len(y)
    xs = list(range(n))
    x_mean = sum(xs) / n
    y_mean = sum(y) / n
    num = sum((xs[i] - x_mean) * (y[i] - y_mean) for i in range(n))
    den = sum((xs[i] - x_mean) ** 2 for i in range(n))
    return float(num / den) if den > 1e-9 else 0.0


def get_candle_close_position(open_p: float, high: float,
                               low: float, close: float) -> float:
    """Returns close position 0.0-1.0 within candle range."""
    candle_range = high - low
    if candle_range < 1e-9:
        return 0.5
    return (close - low) / candle_range


def build_ex_self_composite(ticker: str, peer_prices: dict) -> list:
    """
    Builds equal-weight sector composite excluding the target ticker.
    peer_prices: {ticker: [price_history_list]} for all sector peers.
    Pure Python, no numpy.
    """
    if not peer_prices:
        return []
    histories = [prices for t, prices in peer_prices.items() if t != ticker and prices]
    if not histories:
        return []
    min_len = min(len(h) for h in histories)
    if min_len < 2:
        return []
    composite = []
    for i in range(min_len):
        vals = [h[-(min_len - i)] for h in histories]
        composite.append(sum(vals) / len(vals))
    return composite


def _check_news_filter(stock_curr: float, prev_close: float,
                        sector_pct: float) -> tuple:
    """
    Returns (is_news_event: bool, reason: str).
    Blocks trading when stock moves sharply on its own while sector is flat.
    """
    if prev_close <= 0:
        return False, ""
    abs_change_pct = ((stock_curr - prev_close) / prev_close) * 100
    if (abs_change_pct < -NEWS_FILTER_ABSOLUTE_DROP and
            abs(sector_pct) < NEWS_FILTER_SECTOR_MOVE_MIN):
        return True, f"NEWS FILTER: stock {abs_change_pct:.1f}% vs sector {sector_pct:.2f}%"
    if (abs_change_pct > NEWS_FILTER_ABSOLUTE_DROP and
            abs(sector_pct) < NEWS_FILTER_SECTOR_MOVE_MIN):
        return True, f"NEWS FILTER: stock +{abs_change_pct:.1f}% vs sector {sector_pct:.2f}%"
    return False, ""


def spy_absorption_protocol(candles_3m: list, sector_closes: list,
                             atr_15m: float, prev_close: float = 0.0) -> tuple:
    """
    Full Spy evaluation with news filter.
    Returns (signal: str, metrics: dict)
      signal: 'LONG' / 'SHORT' / 'PASS'
    prev_close: previous day's close for absolute move check.
    """
    metrics = {
        "sector_lag_pct": 0.0, "sector_slope": 0.0,
        "candle_close_pct": 0.0, "gap_to_target": 0.0,
        "reject_reason": "",
    }

    if len(candles_3m) < 2 or len(sector_closes) < SECTOR_SLOPE_PERIOD:
        metrics["reject_reason"] = "Insufficient data"
        return "PASS", metrics

    stock_prev = candles_3m[-2]["close"]
    stock_curr = candles_3m[-1]["close"]
    if stock_prev <= 0:
        metrics["reject_reason"] = "Invalid stock price"
        return "PASS", metrics

    sector_prev = sector_closes[-2]
    sector_curr = sector_closes[-1]
    if sector_prev <= 0:
        metrics["reject_reason"] = "Invalid sector composite"
        return "PASS", metrics

    stock_pct  = ((stock_curr - stock_prev) / stock_prev) * 100
    sector_pct = ((sector_curr - sector_prev) / sector_prev) * 100
    lag_pct    = stock_pct - sector_pct

    if prev_close > 0:
        is_news, news_reason = _check_news_filter(stock_curr, prev_close, sector_pct)
        if is_news:
            metrics["reject_reason"] = news_reason
            return "PASS", metrics

    slope     = calculate_sector_slope(sector_closes)
    curr      = candles_3m[-1]
    close_pct = get_candle_close_position(
        curr["open"], curr["high"], curr["low"], curr["close"])
    implied_fair = stock_curr * (sector_curr / sector_prev) if sector_prev > 0 else stock_curr
    gap_abs      = abs(implied_fair - stock_curr)

    metrics.update({
        "sector_lag_pct":   round(lag_pct, 4),
        "sector_slope":     round(slope, 6),
        "candle_close_pct": round(close_pct, 4),
        "gap_to_target":    round(gap_abs, 4),
        "implied_target":   round(implied_fair, 2),
    })

    if (lag_pct < -LAG_THRESHOLD_PCT and slope > MIN_SECTOR_SLOPE_LONG and
            close_pct >= CANDLE_TOP_PCT and
            (gap_abs >= atr_15m * 0.5 if atr_15m > 0 else True)):
        return "LONG", metrics

    if (lag_pct > LAG_THRESHOLD_PCT and slope < MAX_SECTOR_SLOPE_SHORT and
            close_pct <= CANDLE_BOTTOM_PCT and
            (gap_abs >= atr_15m * 0.5 if atr_15m > 0 else True)):
        return "SHORT", metrics

    reasons = []
    if abs(lag_pct) < LAG_THRESHOLD_PCT:
        reasons.append(f"lag={lag_pct:.2f}% insufficient")
    if not (slope > MIN_SECTOR_SLOPE_LONG or slope < MAX_SECTOR_SLOPE_SHORT):
        reasons.append(f"slope={slope:.4f} extreme")
    if CANDLE_BOTTOM_PCT < close_pct < CANDLE_TOP_PCT:
        reasons.append(f"candle_pos={close_pct:.2f} neutral")
    metrics["reject_reason"] = " | ".join(reasons) or "No setup"
    return "PASS", metrics
