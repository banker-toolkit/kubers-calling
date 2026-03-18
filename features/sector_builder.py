"""
KUBER'S CALLING — features/sector_builder.py
==============================================
Layer 2: Ex-self sector composite.

For each ticker, builds an equal-weight composite of all OTHER
tickers in the same sector — explicitly excluding the ticker being
evaluated. This prevents large-caps from distorting their own benchmark.

Pure Python — no numpy, no scipy.
"""

import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("sector_builder")


def build_ex_self_composite(ticker: str, sector_peers: dict) -> list:
    """
    Build ex-self equal-weight sector composite price series.
    MODIFIED: Normalizes absolute prices to a base of 1.0 to prevent 
    large-cap price distortion and false lag spikes.
    """
    peers = {
        t: prices for t, prices in sector_peers.items()
        if t != ticker and prices and len(prices) > 0
    }

    if len(peers) < 2:
        return []

    # Align to shortest series
    min_len = min(len(p) for p in peers.values())
    if min_len == 0:
        return []

    aligned = [p[-min_len:] for p in peers.values()]

    # Equal-weight mean of NORMALIZED prices
    n_peers = len(aligned)
    composite = []
    for i in range(min_len):
        total_normalized = 0.0
        for series in aligned:
            # Anchor each stock to 1.0 using its first price in the series
            base_price = series[0] if series[0] > 0 else 1.0
            total_normalized += (series[i] / base_price)
        
        # The composite is now a true equal-weight percentage tracker
        composite.append(total_normalized / n_peers)

    return composite

def compute_sector_lag(ticker_closes: list, sector_composite: list,
                       today_open_ticker: float,
                       today_open_sector: float) -> float:
    """
    Compute how much the ticker lags (or leads) its sector composite
    since today's open.

    Returns: lag_pct (positive = ticker lagging = potential long setup)
             Returns 0.0 if inputs are invalid.
    """
    if not ticker_closes or not sector_composite:
        return 0.0
    if today_open_ticker <= 0 or today_open_sector <= 0:
        return 0.0

    ticker_change  = (ticker_closes[-1] - today_open_ticker) / today_open_ticker
    sector_change  = (sector_composite[-1] - today_open_sector) / today_open_sector

    return sector_change - ticker_change   # positive = ticker lagging sector


def compute_sector_slope(sector_composite: list, period: int) -> float:
    """
    Compute linear regression slope of last `period` points.
    Returns slope as float — positive = rising, negative = falling.
    Returns 0.0 if insufficient data.

    Pure Python — no numpy, no scipy.
    """
    if len(sector_composite) < period:
        return 0.0

    y = sector_composite[-period:]
    n = len(y)
    x = list(range(n))

    sum_x  = sum(x)
    sum_y  = sum(y)
    sum_xy = sum(xi * yi for xi, yi in zip(x, y))
    sum_xx = sum(xi * xi for xi in x)

    denom = n * sum_xx - sum_x ** 2
    if denom == 0:
        return 0.0

    return (n * sum_xy - sum_x * sum_y) / denom
