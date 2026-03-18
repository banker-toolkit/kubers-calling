"""
HISTORY LOADER
Pre-loads 30 days of 3m and 15m candles into CandleStore at engine startup.
Uses INDstocks /market/historical API with correct scrip-codes format.
Without this, engine is blind until 1PM. With this, ready at 9:15.
"""
import requests, time
from datetime import datetime, timedelta
from config import INDMONEY_BASE_URL

# Historical endpoint intervals as INDstocks expects them
INTERVAL_3M  = "3minute"
INTERVAL_15M = "15minute"
DAYS_BACK    = 10   # 10 days gives plenty of candles, fetches fast
CHUNK_DAYS   = 7    # API max per request for intraday


def fetch_historical_candles(scrip_code: str, interval: str,
                              headers: dict, days_back: int = DAYS_BACK) -> list:
    """
    Fetches paginated historical candles from INDstocks.
    Returns list of {open, high, low, close, volume, time} dicts, oldest first.
    """
    all_candles = []
    end_dt   = datetime.now()
    start_dt = end_dt - timedelta(days=days_back)

    chunk_end = end_dt
    while chunk_end > start_dt:
        chunk_start = max(chunk_end - timedelta(days=CHUNK_DAYS), start_dt)

        t_start = int(chunk_start.timestamp() * 1000)
        t_end   = int(chunk_end.timestamp()   * 1000)

        try:
            res = requests.get(
                f"{INDMONEY_BASE_URL}/market/historical/{interval}",
                headers=headers,
                params={"scrip-codes": scrip_code,
                        "start_time":  t_start,
                        "end_time":    t_end},
                timeout=10
            )
            if res.status_code == 200:
                data = res.json().get("data", {})
                candles_raw = data.get("candles", [])
                for c in candles_raw:
                    # INDstocks returns [timestamp_ms, open, high, low, close, volume]
                    if len(c) >= 6:
                        all_candles.append({
                            "time":   int(c[0] / 1000),  # convert ms → seconds
                            "open":   float(c[1]),
                            "high":   float(c[2]),
                            "low":    float(c[3]),
                            "close":  float(c[4]),
                            "volume": float(c[5]),
                        })
        except Exception:
            pass

        chunk_end = chunk_start
        time.sleep(0.15)  # polite rate limiting

    # Sort oldest first, deduplicate
    seen = set()
    result = []
    for c in sorted(all_candles, key=lambda x: x["time"]):
        if c["time"] not in seen:
            seen.add(c["time"])
            result.append(c)
    return result


def preload_history(universe: list, security_ids: dict,
                    headers: dict, candle_store,
                    progress_callback=None) -> dict:
    """
    Pre-loads historical candles for all tickers into the CandleStore.
    Called once at engine startup before market opens.

    Returns summary: {loaded: int, failed: int, total_candles: int}
    """
    loaded = 0
    failed = 0
    total_candles = 0

    # Only load tickers we have security IDs for
    eligible = [(t, security_ids.get(t)) for t in universe if security_ids.get(t)]
    total = len(eligible)

    print(f"\n[HISTORY] Pre-loading candles for {total} stocks...")
    print(f"[HISTORY] This takes ~{total * 0.4:.0f} seconds. Engine will be ready at 9:15.\n")

    for idx, (ticker, sid) in enumerate(eligible):
        scrip_code = f"NSE_{sid}"

        try:
            # Fetch both timeframes
            candles_15m = fetch_historical_candles(scrip_code, INTERVAL_15M, headers)
            candles_3m  = fetch_historical_candles(scrip_code, INTERVAL_3M,  headers)

            if candles_15m:
                candle_store.inject_historical(ticker, candles_15m, "15m")
                total_candles += len(candles_15m)

            if candles_3m:
                candle_store.inject_historical(ticker, candles_3m, "3m")
                total_candles += len(candles_3m)

            if candles_15m or candles_3m:
                loaded += 1
            else:
                failed += 1

        except Exception as e:
            failed += 1

        # Progress update every 10 stocks
        if (idx + 1) % 10 == 0 or (idx + 1) == total:
            pct = ((idx + 1) / total) * 100
            msg = (f"[HISTORY] {idx+1}/{total} ({pct:.0f}%) — "
                   f"{total_candles:,} candles loaded")
            print(msg)
            if progress_callback:
                progress_callback(msg, pct)

    summary = {"loaded": loaded, "failed": failed, "total_candles": total_candles}
    print(f"\n[HISTORY] ✅ Done — {loaded} stocks loaded, "
          f"{total_candles:,} candles. Engine is signal-ready.\n")
    return summary
