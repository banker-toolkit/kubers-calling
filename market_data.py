"""
MARKET DATA & ORDER EXECUTION
IndMoney (INDstocks) API wrapper.
All API calls go through here — one place to fix if endpoints change.
"""
import requests, json, os, time, uuid
from config import INDMONEY_BASE_URL, CREDS_FILE, TOKEN_KEY, ZERODHA_INSTRUMENTS

# ──────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────

_headers = {}
_token_loaded = False

def load_token() -> bool:
    global _headers, _token_loaded
    if not os.path.exists(CREDS_FILE):
        print(f"[API] ❌ {CREDS_FILE} not found. Paste your token first.")
        return False
    try:
        with open(CREDS_FILE) as f:
            data = json.load(f)
        token = data.get(TOKEN_KEY, "")
        if not token:
            print("[API] ❌ Token empty in creds file.")
            return False
        # IndMoney: no Bearer prefix — raw JWT
        _headers = {"Authorization": token, "Content-Type": "application/json"}
        _token_loaded = True
        return True
    except Exception as e:
        print(f"[API] ❌ Could not load token: {e}")
        return False

def verify_connection() -> bool:
    """Ping the profile endpoint to confirm token is valid."""
    if not _token_loaded:
        return False
    try:
        res = requests.get(f"{INDMONEY_BASE_URL}/user/profile",
                           headers=_headers, timeout=5)
        if res.status_code == 200:
            print("[API] ✅ IndMoney connection verified.")
            return True
        print(f"[API] ❌ Profile ping failed: {res.status_code} — {res.text[:100]}")
        return False
    except Exception as e:
        print(f"[API] ❌ Connection error: {e}")
        return False


# ──────────────────────────────────────────────
# TOKEN MAPPING (NSE instrument → exchange token)
# ──────────────────────────────────────────────

_universal_tokens: dict = {}    # {TICKER: exchange_token}
_security_ids: dict = {}         # {TICKER: scrip_string}

def forge_token_map() -> int:
    """
    Fetches Zerodha public instrument list.
    Maps NSE symbols to exchange tokens (broker-agnostic).
    Returns count of tokens mapped.
    """
    global _universal_tokens, _security_ids
    try:
        res = requests.get(ZERODHA_INSTRUMENTS, timeout=15)
        if res.status_code != 200:
            print(f"[API] ⚠️  Token forge failed: HTTP {res.status_code}")
            return 0
        lines = res.text.strip().split('\n')
        headers = lines[0].split(',')
        idx_sym   = headers.index('tradingsymbol')
        idx_token = headers.index('exchange_token')
        idx_exch  = headers.index('exchange')
        count = 0
        for line in lines[1:]:
            if not line.strip():
                continue
            cols = line.split(',')
            if len(cols) > idx_exch and cols[idx_exch] == 'NSE':
                sym = cols[idx_sym].strip()
                tok = cols[idx_token].strip()
                _universal_tokens[sym] = tok
                _security_ids[sym] = tok  # IndMoney uses raw token as security_id
                count += 1
        print(f"[API] ✅ {count} NSE tokens mapped.")
        return count
    except Exception as e:
        print(f"[API] ❌ Token forge error: {e}")
        return 0

def get_security_id(ticker: str) -> str:
    return _security_ids.get(ticker, ticker)


# ──────────────────────────────────────────────
# MARKET DATA
# ──────────────────────────────────────────────

_last_prices: dict = {}
_last_volumes: dict = {}
_fetch_error_count = 0
_api_diagnosed = False

def diagnose_api():
    """One-time confirmation that scrip-codes format works."""
    global _api_diagnosed
    if _api_diagnosed:
        return
    _api_diagnosed = True
    sid = _security_ids.get("RELIANCE", "2885")
    scrip = f"NSE_{sid}"
    try:
        res = requests.get(f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes={scrip}",
                           headers=_headers, timeout=5)
        if res.status_code == 200:
            data = res.json().get("data", {})
            price = data.get(scrip, {}).get("live_price", 0)
            print(f"[API] ✅ Quote API working — RELIANCE: ₹{price}")
        else:
            print(f"[API] ❌ Quote API still failing: {res.status_code} {res.text[:200]}")
    except Exception as e:
        print(f"[API] ❌ Quote API exception: {e}")

def fetch_quotes(tickers: list) -> dict:
    """
    Fetches live quotes. Batches of 10 max. Logs errors visibly.
    Returns {ticker: price} dict.
    """
    global _last_prices, _last_volumes, _fetch_error_count
    if not _token_loaded or not tickers:
        return _last_prices

    diagnose_api()

    # Process in batches of 10 to avoid API limits
    for i in range(0, len(tickers), 10):
        batch = tickers[i:i+10]
        _fetch_batch(batch)

    return _last_prices

def _fetch_batch(tickers: list):
    global _last_prices, _last_volumes, _fetch_error_count

    # Build scrip-codes: NSE_INSTRUMENTTOKEN for each ticker
    scrip_to_ticker = {}
    for t in tickers:
        sid = _security_ids.get(t)
        if sid:
            scrip = f"NSE_{sid}"
            scrip_to_ticker[scrip] = t

    if not scrip_to_ticker:
        return

    try:
        # IMPORTANT: pass scrip-codes as raw query string to avoid percent-encoding of hyphen
        query = "scrip-codes=" + ",".join(scrip_to_ticker.keys())
        res = requests.get(f"{INDMONEY_BASE_URL}/market/quotes/full?{query}",
                           headers=_headers, timeout=8)

        if res.status_code != 200:
            _fetch_error_count += 1
            if _fetch_error_count <= 3:
                print(f"[API] ❌ quotes HTTP {res.status_code}: {res.text[:200]}")
            return

        raw  = res.json()
        data = raw.get("data", {})
        _fetch_error_count = 0

        # Response keys are "NSE_3045" format
        for scrip, q in data.items():
            ticker = scrip_to_ticker.get(scrip)
            if not ticker:
                continue
            price  = float(q.get("live_price") or 0)
            volume = float(q.get("volume") or 0)
            if price > 0:
                _last_prices[ticker]  = price
                _last_volumes[ticker] = volume

    except Exception as e:
        _fetch_error_count += 1
        if _fetch_error_count <= 3:
            print(f"[API] ❌ fetch_quotes exception: {e}")

def get_last_volume(ticker: str) -> float:
    return _last_volumes.get(ticker, 0.0)

def fetch_nifty_data() -> dict:
    """Fetches NIFTY 50. scrip-code = NSE_256265"""
    try:
        res = requests.get(f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes=NSE_256265",
                           headers=_headers, timeout=5)
        if res.status_code == 200:
            q = res.json().get("data", {}).get("NSE_256265", {})
            price = float(q.get("live_price") or 0)
            if price > 0:
                return {
                    "price":  price,
                    "open":   float(q.get("day_open") or price),
                    "volume": float(q.get("volume") or 0),
                }
    except Exception:
        pass
    return {"price": 0, "open": 0, "volume": 0}

def fetch_vix() -> float:
    """Fetches India VIX. scrip-code = NSE_264969"""
    try:
        res = requests.get(f"{INDMONEY_BASE_URL}/market/quotes/full?scrip-codes=NSE_264969",
                           headers=_headers, timeout=5)
        if res.status_code == 200:
            q = res.json().get("data", {}).get("NSE_264969", {})
            price = float(q.get("live_price") or 0)
            if price > 0:
                return price
    except Exception:
        pass
    return 15.0


# ──────────────────────────────────────────────
# ORDER EXECUTION
# ──────────────────────────────────────────────

def place_limit_order(ticker: str, direction: str, qty: int,
                      limit_price: float) -> tuple:
    """
    Places a LIMIT entry order.
    direction: 'BUY' or 'SELL'
    Returns (success, order_id, message)
    """
    if not _token_loaded:
        return False, None, "API not connected"

    sec_id = get_security_id(ticker)
    payload = {
        "txn_type":   direction,
        "exchange":   "NSE",
        "segment":    "EQUITY",
        "product":    "INTRADAY",
        "order_type": "LIMIT",
        "validity":   "DAY",
        "security_id": str(sec_id),
        "qty":        int(qty),
        "price":      round(float(limit_price), 2),
        "is_amo":     False,
        "algo_id":    "99999",
    }
    t0 = time.time()
    try:
        res = requests.post(f"{INDMONEY_BASE_URL}/order",
                            headers=_headers, json=payload, timeout=8)
        latency_ms = (time.time() - t0) * 1000
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "success":
                oid = data.get("data", {}).get("order_id",
                      f"ORD-{str(uuid.uuid4())[:6].upper()}")
                return True, oid, latency_ms
            return False, None, data.get("message", "Exchange rejected")
        return False, None, f"HTTP {res.status_code}"
    except Exception as e:
        return False, None, str(e)

def place_sl_order(ticker: str, direction: str, qty: int,
                   trigger_price: float, sl_price: float) -> tuple:
    """
    Places a Stop-Loss LIMIT order on NSE exchange.
    This is the server-side SL — fires at entry millisecond.
    direction for SL is opposite of trade: BUY trade → SELL SL
    Returns (success, sl_order_id, message)
    """
    if not _token_loaded:
        return False, None, "API not connected"

    sl_direction = "SELL" if direction == "BUY" else "BUY"
    sec_id = get_security_id(ticker)
    payload = {
        "txn_type":      sl_direction,
        "exchange":      "NSE",
        "segment":       "EQUITY",
        "product":       "INTRADAY",
        "order_type":    "SL",
        "validity":      "DAY",
        "security_id":   str(sec_id),
        "qty":           int(qty),
        "price":         round(float(sl_price), 2),
        "trigger_price": round(float(trigger_price), 2),
        "is_amo":        False,
        "algo_id":       "99999",
    }
    try:
        res = requests.post(f"{INDMONEY_BASE_URL}/order",
                            headers=_headers, json=payload, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "success":
                oid = data.get("data", {}).get("order_id",
                      f"SL-{str(uuid.uuid4())[:6].upper()}")
                return True, oid, "SL resting on NSE"
            return False, None, data.get("message", "SL rejected")
        return False, None, f"HTTP {res.status_code}"
    except Exception as e:
        return False, None, str(e)

def place_market_order(ticker: str, direction: str, qty: int) -> tuple:
    """Market order — used ONLY for exits (time stop, EOD, kill switch)."""
    if not _token_loaded:
        return False, None, "API not connected"

    sec_id = get_security_id(ticker)
    payload = {
        "txn_type":   direction,
        "exchange":   "NSE",
        "segment":    "EQUITY",
        "product":    "INTRADAY",
        "order_type": "MARKET",
        "validity":   "DAY",
        "security_id": str(sec_id),
        "qty":        int(qty),
        "is_amo":     False,
        "algo_id":    "99999",
    }
    try:
        res = requests.post(f"{INDMONEY_BASE_URL}/order",
                            headers=_headers, json=payload, timeout=8)
        if res.status_code == 200:
            data = res.json()
            if data.get("status") == "success":
                oid = data.get("data", {}).get("order_id",
                      f"EXT-{str(uuid.uuid4())[:6].upper()}")
                return True, oid, "Exit order sent"
            return False, None, data.get("message", "Exit rejected")
        return False, None, f"HTTP {res.status_code}"
    except Exception as e:
        return False, None, str(e)

def cancel_order(order_id: str) -> bool:
    """Cancels an unfilled limit order."""
    try:
        res = requests.delete(f"{INDMONEY_BASE_URL}/order/{order_id}",
                              headers=_headers, timeout=5)
        return res.status_code == 200
    except Exception:
        return False

def calculate_charges(direction: str, price: float, qty: int) -> float:
    """Calculates all-in transaction cost for Indian equity intraday."""
    turnover   = price * qty
    brokerage  = min(20.0, turnover * 0.0005)
    txn_charge = turnover * 0.0000345
    gst        = (brokerage + txn_charge) * 0.18
    stt        = (turnover * 0.00025) if direction in ["SELL", "SHORT"] else 0.0
    stamp      = (turnover * 0.00003) if direction in ["BUY", "COVER"] else 0.0
    sebi       = turnover * 0.000001
    return round(brokerage + txn_charge + gst + stt + stamp + sebi, 2)
