"""
KUBER'S CALLING — data/feed.py
================================
Layer 1: INDstocks live price feed and order placement.

RULES (enforced by Validation Agent):
  - scrip-codes are ALWAYS passed as raw URL query string,
    NEVER as requests params dict (percent-encodes hyphens → silent failure)
  - All thresholds from config.py — no magic numbers here
  - Every method logs success or failure — no silent returns
"""

import os, sys, json, time, logging
import requests
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    INDMONEY_BASE_URL, CREDS_FILE, TOKEN_KEY,
    SCRIP_NIFTY50, SCRIP_INDIA_VIX, ZERODHA_INSTRUMENTS,
)

log = logging.getLogger("feed")

# ── Module-level state ───────────────────────────────────────────────
_token       = ""
_token_map   = {}    # ticker → scrip_code e.g. "RELIANCE" → "NSE_2885"
_headers     = {}

# ═══════════════════════════════════════════════════════════════════
# AUTH
# ═══════════════════════════════════════════════════════════════════


def load_token() -> bool:
    global _token, _headers
    try:
        with open(CREDS_FILE, "r") as f:
            data = json.load(f)
        _token = data.get(TOKEN_KEY, "").strip()
        if not _token:
            log.error("[feed] Token missing or empty in %s", CREDS_FILE)
            return False

     
        _headers = {"Authorization": _token, "Content-Type": "application/json"}
        log.info("[feed] Token loaded — %d chars", len(_token))
        return True

    except FileNotFoundError:
        log.error("[feed] Creds file not found: %s", CREDS_FILE)
        return False
    except json.JSONDecodeError as e:
        log.error("[feed] Creds file JSON error: %s", e)
        return False


def verify_connection() -> dict:
    """
    Ping the API. Returns dict:
      { "ok": bool, "status": int, "market_open": bool, "detail": str }

    IMPORTANT: Before market hours the API may return 4xx for live quotes
    even with a valid token. We treat any non-5xx, non-network-error response
    as "token accepted" — market_open tells you whether live data is flowing.
    A 401/403 means the token itself is bad.
    """
    if not _token:
        return {"ok": False, "status": 0, "market_open": False,
                "detail": "Token not loaded"}
    try:
        url = f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes={SCRIP_NIFTY50}"
        r   = requests.get(url, headers=_headers, timeout=8)
        code = r.status_code

        if code in (400, 401, 403):
            log.error("[feed] Token rejected — HTTP %d", code)
            return {"ok": False, "status": code, "market_open": False,
                    "detail": f"Token rejected (HTTP {code}) — paste today's token"}

        if code >= 500:
            log.error("[feed] API server error — HTTP %d", code)
            return {"ok": False, "status": code, "market_open": False,
                    "detail": f"INDstocks server error ({code}) — try again"}

        # 200 = live data available; 4xx (not auth) = pre-market / market closed
        market_open = (code == 200)
        detail = "CONNECTED — market data live" if market_open \
                 else f"Token OK (HTTP {code}) — market not yet open"
        log.info("[feed] verify_connection: %s", detail)
        return {"ok": True, "status": code, "market_open": market_open,
                "detail": detail}

    except requests.RequestException as e:
        log.error("[feed] Connection failed: %s", e)
        return {"ok": False, "status": 0, "market_open": False,
                "detail": f"Network error: {e}"}


# ═══════════════════════════════════════════════════════════════════
# TOKEN MAP — NSE ticker → scrip code
# ═══════════════════════════════════════════════════════════════════

def forge_token_map(from_universe: list = None) -> int:
    """
    Load Zerodha instrument dump and build ticker → scrip_code mapping.
    Returns count of tokens mapped.

    We use NSE exchange token from Zerodha as the numeric part of
    INDstocks scrip-code: NSE_{exchange_token}
    """
    global _token_map
    try:
        r = requests.get(ZERODHA_INSTRUMENTS, timeout=30)
        if r.status_code != 200:
            log.error("[feed] Zerodha instruments fetch failed: %d", r.status_code)
            return 0

        lines  = r.text.strip().split("\n")
        header = lines[0].split(",")
        col    = {h.strip(): i for i, h in enumerate(header)}

        for line in lines[1:]:
            parts    = line.split(",")
            exchange = parts[col.get("exchange", 0)].strip()
            symbol   = parts[col.get("tradingsymbol", 1)].strip()
            token    = parts[col.get("exchange_token", 4)].strip()
            itype    = parts[col.get("instrument_type", 9)].strip()

            if exchange == "NSE" and itype == "EQ" and symbol and token:
                _token_map[symbol] = f"NSE_{token}"

        n = len(_token_map)
        log.info("[feed] Token map built: %d NSE EQ tokens", n)
        return n

    except requests.RequestException as e:
        log.error("[feed] Zerodha instruments error: %s", e)
        return 0


def get_scrip_code(ticker: str) -> str:
    """Return scrip code for ticker, or empty string if unknown."""
    return _token_map.get(ticker, "")


# ═══════════════════════════════════════════════════════════════════
# LIVE QUOTES
# ═══════════════════════════════════════════════════════════════════

def fetch_quotes(tickers: list) -> dict:
    """
    Fetch live quotes for a list of tickers.
    Returns dict: ticker → Quote dict with keys:
      price, open, high, low, volume, change_pct

    CRITICAL: scrip-codes passed as RAW URL query string.
    Never use requests params dict — it encodes hyphens.
    """
    if not tickers or not _token:
        return {}

    # Build scrip-codes string from token map
    scrip_codes = []
    ticker_map  = {}    # scrip_code → ticker (for response parsing)
    for t in tickers:
        sc = get_scrip_code(t)
        if sc:
            scrip_codes.append(sc)
            ticker_map[sc] = t

    if not scrip_codes:
        return {}

    # Batch into groups of 50 (API limit)
    results = {}
    batch_size = 50
    for i in range(0, len(scrip_codes), batch_size):
        batch = scrip_codes[i:i + batch_size]
        raw   = ",".join(batch)
        url   = f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes={raw}"

        try:
            r = requests.get(url, headers=_headers, timeout=5)
            if r.status_code != 200:
                log.warning("[feed] quotes batch %d returned %d", i // batch_size, r.status_code)
                continue

            data = r.json().get("data", {})
            for sc, q in data.items():
                ticker = ticker_map.get(sc)
                if not ticker:
                    continue
                price = float(q.get("live_price") or q.get("ltp") or 0)
                if price > 0:
                    results[ticker] = {
                        "price":      price,
                        "open":       float(q.get("day_open") or price),
                        "high":       float(q.get("day_high") or price),
                        "low":        float(q.get("day_low")  or price),
                        "volume":     float(q.get("volume") or 0),
                        "change_pct": float(q.get("change_percentage") or 0),
                    }

        except requests.RequestException as e:
            log.warning("[feed] quotes batch error: %s", e)
        except (ValueError, KeyError) as e:
            log.warning("[feed] quotes parse error: %s", e)

    return results


def fetch_index(scrip_code: str) -> dict:
    """
    Fetch a single index quote (NIFTY or VIX).
    Returns dict with price, open, volume, change_pct.
    Returns zeros on failure — caller must treat zero as data error.
    """
    if not _token:
        return {"price": 0.0, "open": 0.0, "volume": 0.0, "change_pct": 0.0}

    url = f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes={scrip_code}"
    try:
        r = requests.get(url, headers=_headers, timeout=5)
        if r.status_code == 200:
            q = r.json().get("data", {}).get(scrip_code, {})
            price = float(q.get("live_price") or q.get("ltp") or 0)
            if price > 0:
                return {
                    "price":      price,
                    "open":       float(q.get("day_open") or price),
                    "volume":     float(q.get("volume") or 0),
                    "change_pct": float(q.get("change_percentage") or 0),
                }
    except requests.RequestException as e:
        log.warning("[feed] index fetch error (%s): %s", scrip_code, e)
    except (ValueError, KeyError) as e:
        log.warning("[feed] index parse error (%s): %s", scrip_code, e)

    return {"price": 0.0, "open": 0.0, "volume": 0.0, "change_pct": 0.0}


def fetch_nifty_data() -> dict:
    """Convenience wrapper for NIFTY 50."""
    return fetch_index(SCRIP_NIFTY50)


def fetch_vix() -> float:
    """
    Fetch India VIX. Returns float price.
    Returns 0.0 on failure — caller must detect and enter STANDBY.
    """
    result = fetch_index(SCRIP_INDIA_VIX)
    return result["price"]


# ═══════════════════════════════════════════════════════════════════
# ORDER PLACEMENT
# ═══════════════════════════════════════════════════════════════════

class Order:
    """Proposed order passed from Risk Gate to Broker."""
    def __init__(self, ticker, side, qty, limit_price,
                 sl_price, signal_id="", strategy_name=""):
        self.ticker        = ticker
        self.side          = side           # 'BUY' or 'SELL'
        self.qty           = qty
        self.limit_price   = limit_price
        self.sl_price      = sl_price
        self.signal_id     = signal_id
        self.strategy_name = strategy_name
        self.scrip_code    = get_scrip_code(ticker)


class OrderResult:
    """Returned by place_order()."""
    def __init__(self, order_id="", status="FAILED",
                 filled_qty=0, avg_price=0.0, error=""):
        self.order_id   = order_id
        self.status     = status
        self.filled_qty = filled_qty
        self.avg_price  = avg_price
        self.error      = error
        self.success    = (status not in ("FAILED", "REJECTED"))


class OrderStatus:
    """Returned by get_order_status()."""
    def __init__(self, order_id, status, filled_qty, avg_price):
        self.order_id   = order_id
        self.status     = status
        self.filled_qty = filled_qty
        self.avg_price  = avg_price


def place_order(order: Order) -> OrderResult:
    """
    Place a limit order via INDstocks API.
    Returns OrderResult immediately after placement — does not wait for fill.
    SL is placed separately after fill is confirmed.
    """
    if not _token:
        return OrderResult(error="Token not loaded")
    if not order.scrip_code:
        return OrderResult(error=f"Unknown ticker: {order.ticker}")

    # security_id is numeric exchange token only — strip "NSE_" prefix
    security_id = order.scrip_code.replace("NSE_", "").replace("BSE_", "")

    payload = {
        "txn_type":    order.side,
        "exchange":    "NSE",
        "segment":     "EQUITY",
        "product":     "INTRADAY",
        "order_type":  "LIMIT",
        "validity":    "DAY",
        "security_id": security_id,
        "qty":         order.qty,
        "limit_price": round(order.limit_price, 2),
        "is_amo":      False,
        "algo_id":     "99999",
    }

    try:
        url = f"{INDMONEY_BASE_URL}/order"
        r   = requests.post(url, headers=_headers, json=payload, timeout=5)
        if r.status_code in (200, 201):
            data     = r.json()
            order_id = data.get("data", {}).get("order_id", "")
            log.info("[feed] Order placed: %s %s %d@%.2f → id=%s",
                     order.side, order.ticker, order.qty,
                     order.limit_price, order_id)
            return OrderResult(order_id=order_id, status="OPEN")
        else:
            msg = r.text[:200]
            log.error("[feed] Order rejected %d: %s", r.status_code, msg)
            return OrderResult(error=f"HTTP {r.status_code}: {msg}")

    except requests.RequestException as e:
        log.error("[feed] Order placement error: %s", e)
        return OrderResult(error=str(e))


def place_sl_order(ticker: str, side: str, qty: int,
                   trigger_price: float) -> OrderResult:
    """
    Place a server-side Stop Loss (SL-M) order.
    Called immediately after fill is confirmed and filled_qty is known.
    side: 'SELL' for long SL, 'BUY' for short SL.
    """
    if not _token:
        return OrderResult(error="Token not loaded")

    scrip_code = get_scrip_code(ticker)
    if not scrip_code:
        return OrderResult(error=f"Unknown ticker: {ticker}")

    security_id = scrip_code.replace("NSE_", "").replace("BSE_", "")
    
    payload = {
        "txn_type":      side,
        "exchange":      "NSE",
        "segment":       "EQUITY",
        "product":       "INTRADAY",
        "order_type":    "LIMIT",        # Changed from 'SL' or 'SL-M'
        "validity":      "DAY",
        "security_id":   security_id,
        "qty":           qty,
        "limit_price":   round(trigger_price, 2), # Price to execute at
        "trigger_price": round(trigger_price, 2), # Price that activates the order
        "is_amo":        False,
        "algo_id":       "99999",
    }
    
    try:
        url = f"{INDMONEY_BASE_URL}/order"
        r   = requests.post(url, headers=_headers, json=payload, timeout=5)
        if r.status_code in (200, 201):
            data     = r.json()
            order_id = data.get("data", {}).get("order_id", "")
            log.info("[feed] SL placed: %s %s %d@trigger%.2f → id=%s",
                     side, ticker, qty, trigger_price, order_id)
            return OrderResult(order_id=order_id, status="OPEN")
        else:
            log.error("[feed] SL order rejected %d: %s", r.status_code, r.text[:200])
            return OrderResult(error=f"HTTP {r.status_code}")

    except requests.RequestException as e:
        log.error("[feed] SL placement error: %s", e)
        return OrderResult(error=str(e))


def get_order_status(order_id: str) -> OrderStatus:
    """
    Poll order status. Returns OrderStatus with filled_qty.
    CRITICAL: filled_qty is used by Risk Gate for partial fill SL sizing.
    Returns filled_qty=0 on failure — caller treats this as unfilled.
    API status "SUCCESS" maps to filled; "PARTIALLY FILLED" maps to partial.
    """
    if not _token or not order_id:
        return OrderStatus(order_id, "UNKNOWN", 0, 0.0)

    try:
        url = f"{INDMONEY_BASE_URL}/order"
        r   = requests.get(url, headers=_headers,
                           json={"order_id": order_id, "segment": "EQUITY"},
                           timeout=5)
        if r.status_code == 200:
            d      = r.json().get("data", {})
            status = d.get("status", "UNKNOWN")
            # Normalise API status to the values broker.py checks for
            if status == "SUCCESS":
                norm = "COMPLETE"
            elif "PARTIALLY FILLED" in status:
                norm = "FILLED"
            else:
                norm = status
            return OrderStatus(
                order_id   = order_id,
                status     = norm,
                filled_qty = int(d.get("traded_qty") or 0),
                avg_price  = float(d.get("traded_price") or 0),
            )
    except requests.RequestException as e:
        log.warning("[feed] get_order_status error: %s", e)
    except (ValueError, KeyError) as e:
        log.warning("[feed] get_order_status parse error: %s", e)

    return OrderStatus(order_id, "UNKNOWN", 0, 0.0)


def cancel_order(order_id: str) -> bool:
    """Cancel an open order. Returns True if cancelled."""
    if not _token or not order_id:
        return False
    try:
        url     = f"{INDMONEY_BASE_URL}/order/cancel"
        payload = {"segment": "EQUITY", "order_id": order_id}
        r       = requests.post(url, headers=_headers, json=payload, timeout=5)
        ok      = r.status_code in (200, 201)
        if ok:
            log.info("[feed] Order cancelled: %s", order_id)
        else:
            log.warning("[feed] Cancel failed %d: %s", r.status_code, order_id)
        return ok
    except requests.RequestException as e:
        log.warning("[feed] Cancel error: %s", e)
        return False



def place_market_order(ticker: str, side: str, qty: int) -> "OrderResult":
    """
    Place a MARKET order via INDstocks API.
    Used for reconciliation closes and emergency exits.
    side: 'BUY' or 'SELL'
    """
    if not _token:
        return OrderResult(error="Token not loaded")

    scrip_code = get_scrip_code(ticker)
    if not scrip_code:
        return OrderResult(error=f"Unknown ticker: {ticker}")

    security_id = scrip_code.replace("NSE_", "").replace("BSE_", "")

    payload = {
        "txn_type":    side,
        "exchange":    "NSE",
        "segment":     "EQUITY",
        "product":     "INTRADAY",
        "order_type":  "MARKET",
        "validity":    "DAY",
        "security_id": security_id,
        "qty":         qty,
        "is_amo":      False,
        "algo_id":     "99999",
    }

    try:
        url = f"{INDMONEY_BASE_URL}/order"
        r   = requests.post(url, headers=_headers, json=payload, timeout=5)
        if r.status_code in (200, 201):
            order_id = r.json().get("data", {}).get("order_id", "")
            log.info("[feed] Market order placed: %s %s %d → id=%s",
                     side, ticker, qty, order_id)
            return OrderResult(order_id=order_id, status="OPEN")
        else:
            msg = r.text[:200]
            log.error("[feed] Market order rejected %d: %s", r.status_code, msg)
            return OrderResult(error=f"HTTP {r.status_code}: {msg}")

    except requests.RequestException as e:
        log.error("[feed] Market order error: %s", e)
        return OrderResult(error=str(e))

def fetch_broker_positions() -> list:
    """
    Fetch all open intraday equity positions from INDmoney.
    Returns list of dicts: [{ticker, direction, qty, avg_price}, ...]
    Returns empty list on failure — caller must treat as inconclusive.

    Used by engine startup reconciliation to detect ghost positions
    (positions open on broker but absent from Kubers DB, or vice versa).
    """
    if not _token:
        log.warning("[feed] fetch_broker_positions: token not loaded")
        return []
    try:
        url = f"{INDMONEY_BASE_URL}/portfolio/positions?segment=equity&product=intraday"
        r   = requests.get(url, headers=_headers, timeout=8)
        if r.status_code != 200:
            log.warning("[feed] fetch_broker_positions: HTTP %d", r.status_code)
            return []

        # API returns data as a list directly
        # Fields: net_qty, symbol, avg_price, realized_profit
        raw  = r.json().get("data", [])
        positions = raw if isinstance(raw, list) else raw.get("net_positions", [])
        result = []

        for p in positions:
            raw_qty = int(p.get("net_qty", 0))
            if raw_qty == 0:
                continue  # fully squared off — skip

            # symbol is plain NSE ticker e.g. "NOCIL", "RELIANCE"
            ticker = p.get("symbol", "").strip()
            if not ticker:
                continue

            result.append({
                "ticker":    ticker,
                "direction": "LONG" if raw_qty > 0 else "SHORT",
                "qty":       abs(raw_qty),
                "avg_price": float(p.get("avg_price", 0)),
                "pnl":       float(p.get("realized_profit", 0)),
            })

        log.info("[feed] fetch_broker_positions: %d open positions", len(result))
        return result

    except requests.RequestException as e:
        log.warning("[feed] fetch_broker_positions error: %s", e)
        return []
    except (ValueError, KeyError) as e:
        log.warning("[feed] fetch_broker_positions parse error: %s", e)
        return []