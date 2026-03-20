"""
KUBER'S CALLING — data/feed.py
================================
Layer 1: INDstocks live price feed and order placement.

v8 changes:
  - WebSocket order updates listener (primary fill confirmation channel)
  - Market order payload: limit_price omitted entirely (was ₹0 — caused all
    SL_HIT rejections. IndMoney validates LimitPriceMustBeAboveZero)
  - Status strings corrected: "SUCCESS" not "COMPLETE", "PARTIALLY FILLED" added
  - cancel_order: POST /order/cancel with segment+order_id (was DELETE)
  - fetch_trade_book: authoritative ground truth for startup reconciliation
  - Order class: order_type field added (LIMIT/MARKET)
"""

import os, sys, json, time, logging, threading
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    INDMONEY_BASE_URL, CREDS_FILE, TOKEN_KEY,
    SCRIP_NIFTY50, SCRIP_INDIA_VIX, ZERODHA_INSTRUMENTS,
    WS_ORDER_UPDATES_URL,
)

log = logging.getLogger("feed")

# ── Module-level state ───────────────────────────────────────────────
_token       = ""
_token_map   = {}
_headers     = {}

# ── WebSocket order event state ──────────────────────────────────────
# Keyed by order_id. broker.py reads this; feed.py writes it.
# Thread-safe via _ws_lock.
_ws_events    = {}          # order_id -> latest event dict
_ws_lock      = threading.Lock()
_ws_connected = False


# ===================================================================
# AUTH
# ===================================================================

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
    """Ping the API. Returns {ok, status, market_open, detail}."""
    if not _token:
        return {"ok": False, "status": 0, "market_open": False,
                "detail": "Token not loaded"}
    try:
        url = f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes={SCRIP_NIFTY50}"
        r   = requests.get(url, headers=_headers, timeout=8)
        code = r.status_code
        if code in (400, 401, 403):
            return {"ok": False, "status": code, "market_open": False,
                    "detail": f"Token rejected (HTTP {code})"}
        if code >= 500:
            return {"ok": False, "status": code, "market_open": False,
                    "detail": f"INDstocks server error ({code})"}
        market_open = (code == 200)
        detail = "CONNECTED — market data live" if market_open \
                 else f"Token OK (HTTP {code}) — market not yet open"
        return {"ok": True, "status": code, "market_open": market_open,
                "detail": detail}
    except requests.RequestException as e:
        return {"ok": False, "status": 0, "market_open": False,
                "detail": f"Network error: {e}"}


# ===================================================================
# WEBSOCKET ORDER UPDATES (primary fill confirmation channel)
# ===================================================================

def _on_ws_message(ws, message):
    """Process incoming WS message. Writes to _ws_events dict."""
    try:
        # Heartbeat messages are not JSON or don't have order_id — ignore
        event = json.loads(message)
        order_id = event.get("order_id")
        if not order_id:
            return
        with _ws_lock:
            _ws_events[order_id] = event
        log.debug("[feed] WS event: order=%s status=%s filled=%s remaining=%s",
                  order_id,
                  event.get("order_status", "?"),
                  event.get("filled_quantity", "?"),
                  event.get("remaining_quantity", "?"))
    except (json.JSONDecodeError, Exception):
        pass   # heartbeat or unparseable — silently ignore


def _on_ws_error(ws, error):
    global _ws_connected
    log.error("[feed] WS error: %s — falling back to REST polling", error)
    _ws_connected = False


def _on_ws_close(ws, close_status_code, close_msg):
    global _ws_connected
    log.warning("[feed] WS closed (code=%s) — REST polling is fallback",
                close_status_code)
    _ws_connected = False


def _on_ws_open(ws):
    global _ws_connected
    _ws_connected = True
    ws.send(json.dumps({"action": "subscribe", "mode": "order_updates"}))
    log.info("[feed] WS connected — subscribed to order updates")


def start_order_ws() -> bool:
    """
    Start WebSocket listener in a daemon background thread.
    Called once at engine startup. Token is valid all day — no reconnect needed.
    If WebSocket fails, system falls back to REST polling transparently.
    """
    if not _token:
        log.warning("[feed] Cannot start WS — token not loaded")
        return False

    def _run():
        try:
            import websocket as _websocket
            ws = _websocket.WebSocketApp(
                WS_ORDER_UPDATES_URL,
                header={"Authorization": _token},
                on_message=_on_ws_message,
                on_error=_on_ws_error,
                on_close=_on_ws_close,
                on_open=_on_ws_open,
            )
            ws.run_forever()
        except ImportError:
            log.warning("[feed] websocket-client not installed — REST polling only. "
                        "Run: pip install websocket-client")
        except Exception as e:
            log.error("[feed] WS thread error: %s", e)

    t = threading.Thread(target=_run, daemon=True, name="ws-order-updates")
    t.start()
    log.info("[feed] Order WS thread started")
    return True


def get_ws_event(order_id: str) -> dict:
    """Get latest WS event for order_id. Returns None if no event yet."""
    with _ws_lock:
        return _ws_events.get(order_id)


def clear_ws_event(order_id: str):
    """Clear event after broker has processed it."""
    with _ws_lock:
        _ws_events.pop(order_id, None)


def is_ws_connected() -> bool:
    return _ws_connected


# ===================================================================
# TOKEN MAP — NSE ticker → scrip code
# ===================================================================

def forge_token_map(from_universe: list = None) -> int:
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
    return _token_map.get(ticker, "")


# ===================================================================
# LIVE QUOTES
# ===================================================================

def fetch_quotes(tickers: list) -> dict:
    if not tickers or not _token:
        return {}
    scrip_codes = []
    ticker_map  = {}
    for t in tickers:
        sc = get_scrip_code(t)
        if sc:
            scrip_codes.append(sc)
            ticker_map[sc] = t
    if not scrip_codes:
        return {}
    results = {}
    batch_size = 50
    for i in range(0, len(scrip_codes), batch_size):
        batch = scrip_codes[i:i + batch_size]
        raw   = ",".join(batch)
        url   = f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes={raw}"
        try:
            r = requests.get(url, headers=_headers, timeout=5)
            if r.status_code != 200:
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
    return {"price": 0.0, "open": 0.0, "volume": 0.0, "change_pct": 0.0}


def fetch_nifty_data() -> dict:
    return fetch_index(SCRIP_NIFTY50)


def fetch_vix() -> float:
    result = fetch_index(SCRIP_INDIA_VIX)
    return result["price"]


# ===================================================================
# TRADE BOOK — authoritative ground truth (used at startup + 15:10)
# ===================================================================

def fetch_trade_book() -> list:
    """
    Fetch all executed equity trades from IndMoney trade book.
    Returns list of fill dicts with exch_order_id, quantity, price.
    Used at startup reconciliation and 15:10 force-close verification.
    """
    if not _token:
        return []
    try:
        r = requests.get(
            f"{INDMONEY_BASE_URL}/trade-book?segment=EQUITY",
            headers=_headers, timeout=10
        )
        if r.status_code == 200:
            data = r.json().get("data", [])
            return data if isinstance(data, list) else []
        log.warning("[feed] fetch_trade_book HTTP %d", r.status_code)
    except Exception as e:
        log.warning("[feed] fetch_trade_book error: %s", e)
    return []


def fetch_broker_positions() -> list:
    """
    Fetch all open intraday equity positions from IndMoney.
    Returns list of {ticker, direction, qty, avg_price}.
    Used by position count heartbeat (Principle 1.11 backup).
    """
    if not _token:
        return []
    try:
        url = f"{INDMONEY_BASE_URL}/portfolio/positions?segment=equity&product=intraday"
        r   = requests.get(url, headers=_headers, timeout=8)
        if r.status_code != 200:
            log.warning("[feed] fetch_broker_positions: HTTP %d", r.status_code)
            return []
        raw  = r.json().get("data", [])
        positions = raw if isinstance(raw, list) else raw.get("net_positions", [])
        result = []
        for p in positions:
            raw_qty = int(p.get("net_qty", 0))
            if raw_qty == 0:
                continue
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
    except Exception as e:
        log.warning("[feed] fetch_broker_positions error: %s", e)
        return []


# ===================================================================
# ORDER PLACEMENT
# ===================================================================

# IndMoney order status strings → normalised values
# v8: corrected from "COMPLETE"/"FILLED" which do not exist in IndMoney API
_STATUS_MAP = {
    "SUCCESS":                        "COMPLETE",
    "PARTIALLY FILLED":               "PARTIAL",
    "PARTIALLY FILLED - CANCELLED":   "PARTIAL_DONE",  # partial filled, rest cancelled
    "PARTIALLY FILLED - EXPIRED":     "PARTIAL_DONE",  # partial filled, rest expired
    "CANCELLED":                      "CANCELLED",
    "FAILED":                         "FAILED",
    "EXPIRED":                        "EXPIRED",
    "REJECTED":                       "REJECTED",
    "PENDING":                        "PENDING",
    "PROCESSING":                     "PENDING",
    "INITIATED":                      "PENDING",
    "QUEUED":                         "PENDING",
    "ABORTED":                        "FAILED",
}


class Order:
    """Proposed order passed from Risk Gate to Broker."""
    def __init__(self, ticker, side, qty, limit_price,
                 sl_price, signal_id="", strategy_name="",
                 order_type="LIMIT"):
        self.ticker        = ticker
        self.side          = side           # 'BUY' or 'SELL'
        self.qty           = qty
        self.limit_price   = limit_price
        self.sl_price      = sl_price
        self.signal_id     = signal_id
        self.strategy_name = strategy_name
        self.order_type    = order_type     # 'LIMIT' or 'MARKET'
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
    def __init__(self, order_id, status, filled_qty, avg_price,
                 requested_qty=0, remaining_qty=0):
        self.order_id      = order_id
        self.status        = status
        self.filled_qty    = filled_qty
        self.avg_price     = avg_price
        self.requested_qty = requested_qty
        self.remaining_qty = remaining_qty


def place_order(order: Order) -> OrderResult:
    """
    Place a LIMIT or MARKET order via IndMoney API.

    v8 fix — MARKET orders:
      limit_price field is OMITTED ENTIRELY from the payload.
      IndMoney validates LimitPriceMustBeAboveZero and rejects any order
      with limit_price=0, even if order_type is MARKET.
      Previous code sent limit_price=0 — this caused every SL_HIT ghost.
    """
    if not _token:
        return OrderResult(error="Token not loaded")
    if not order.scrip_code:
        return OrderResult(error=f"Unknown ticker: {order.ticker}")

    security_id = order.scrip_code.replace("NSE_", "").replace("BSE_", "")

    payload = {
        "txn_type":    order.side,
        "exchange":    "NSE",
        "segment":     "EQUITY",
        "product":     "INTRADAY",
        "order_type":  order.order_type,   # "LIMIT" or "MARKET"
        "validity":    "DAY",
        "security_id": security_id,
        "qty":         order.qty,
        "is_amo":      False,
        "algo_id":     "99999",
    }

    # CRITICAL: limit_price only included for LIMIT orders.
    # For MARKET orders, omitting limit_price entirely is correct.
    # IndMoney rejects market orders that include a limit_price field.
    if order.order_type == "LIMIT":
        payload["limit_price"] = round(order.limit_price, 2)

    try:
        url = f"{INDMONEY_BASE_URL}/order"
        r   = requests.post(url, headers=_headers, json=payload, timeout=5)
        if r.status_code in (200, 201):
            data     = r.json()
            order_id = data.get("data", {}).get("order_id", "")
            log.info("[feed] Order placed: %s %s %d type=%s id=%s",
                     order.side, order.ticker, order.qty,
                     order.order_type, order_id)
            return OrderResult(order_id=order_id, status="OPEN")
        else:
            msg = r.text[:200]
            log.error("[feed] Order rejected %d: %s payload=%s",
                      r.status_code, msg, payload)
            return OrderResult(error=f"HTTP {r.status_code}: {msg}")
    except requests.RequestException as e:
        log.error("[feed] Order placement error: %s", e)
        return OrderResult(error=str(e))


def place_sl_order(ticker: str, side: str, qty: int,
                   trigger_price: float) -> OrderResult:
    """Place a server-side SL order. Note: IndMoney equity does not support SL-M."""
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
        "order_type":  "LIMIT",
        "validity":    "DAY",
        "security_id": security_id,
        "qty":         qty,
        "limit_price": round(trigger_price, 2),
        "is_amo":      False,
        "algo_id":     "99999",
    }
    try:
        r = requests.post(f"{INDMONEY_BASE_URL}/order",
                          headers=_headers, json=payload, timeout=5)
        if r.status_code in (200, 201):
            order_id = r.json().get("data", {}).get("order_id", "")
            log.info("[feed] SL placed: %s %s %d@%.2f id=%s",
                     side, ticker, qty, trigger_price, order_id)
            return OrderResult(order_id=order_id, status="OPEN")
        else:
            log.error("[feed] SL rejected %d: %s", r.status_code, r.text[:200])
            return OrderResult(error=f"HTTP {r.status_code}")
    except requests.RequestException as e:
        log.error("[feed] SL placement error: %s", e)
        return OrderResult(error=str(e))


def get_order_status(order_id: str) -> OrderStatus:
    """
    Poll order status via REST. Fallback when WebSocket event not yet received.

    v8 fix: status strings corrected to IndMoney actual values.
    Previous code checked "COMPLETE" and "FILLED" — neither exists.
    Actual terminal status is "SUCCESS". Partial is "PARTIALLY FILLED".
    """
    if not _token or not order_id:
        return OrderStatus(order_id, "UNKNOWN", 0, 0.0)
    try:
        url = f"{INDMONEY_BASE_URL}/order"
        r   = requests.get(url, headers=_headers,
                           json={"order_id": order_id, "segment": "EQUITY"},
                           timeout=5)
        if r.status_code == 200:
            d          = r.json().get("data", {})
            status_raw = d.get("status", "UNKNOWN")
            norm       = _STATUS_MAP.get(status_raw, "PENDING")
            traded_qty = int(d.get("traded_qty") or 0)
            req_qty    = int(d.get("requested_qty") or 0)
            avg_price  = float(d.get("traded_price") or 0)

            # Compute remaining from requested - traded
            remaining  = max(0, req_qty - traded_qty)

            return OrderStatus(
                order_id      = order_id,
                status        = norm,
                filled_qty    = traded_qty,
                avg_price     = avg_price,
                requested_qty = req_qty,
                remaining_qty = remaining,
            )
    except requests.RequestException as e:
        log.warning("[feed] get_order_status error: %s", e)
    except (ValueError, KeyError) as e:
        log.warning("[feed] get_order_status parse error: %s", e)
    return OrderStatus(order_id, "UNKNOWN", 0, 0.0)


def cancel_order(order_id: str) -> bool:
    """
    Cancel a pending order.

    v8 fix: endpoint is POST /order/cancel with {segment, order_id}.
    Previous code used DELETE /order/{id} — incorrect endpoint.

    OrderCannotBeCancelled response is handled gracefully:
    it means IndMoney is mid-fill or order already terminal — not an error.
    """
    if not _token or not order_id:
        return False
    try:
        payload = {"segment": "EQUITY", "order_id": order_id}
        r = requests.post(
            f"{INDMONEY_BASE_URL}/order/cancel",
            headers=_headers, json=payload, timeout=5
        )
        if r.status_code in (200, 201):
            data = r.json()
            if data.get("status") == "success":
                log.info("[feed] Order cancelled: %s", order_id)
                return True
            # OrderCannotBeCancelled = already terminal or mid-fill — treat as OK
            err = str(data)
            if "OrderCannotBeCancelled" in err or "cannot be cancelled" in err.lower():
                log.info("[feed] Order %s already terminal (cancel not needed)", order_id)
                return True
            log.warning("[feed] Cancel unexpected response: %s", err[:100])
            return False
        log.warning("[feed] Cancel HTTP %d for %s", r.status_code, order_id)
        return False
    except requests.RequestException as e:
        log.warning("[feed] Cancel error: %s", e)
        return False


def place_market_order(ticker: str, side: str, qty: int) -> "OrderResult":
    """
    Convenience function: place a MARKET order.
    Delegates to place_order with order_type=MARKET.
    """
    scrip_code = get_scrip_code(ticker)
    if not scrip_code:
        return OrderResult(error=f"Unknown ticker: {ticker}")
    order = Order(
        ticker     = ticker,
        side       = side,
        qty        = qty,
        limit_price= 0,
        sl_price   = 0,
        order_type = "MARKET",
    )
    return place_order(order)
