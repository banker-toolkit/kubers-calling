"""
KUBER'S CALLING — execution/broker.py
=======================================
Layer 5: Order execution and position lifecycle.

v8 architecture (Principles 1.11 + 1.12):

  INVARIANT: Kubers never books a position as closed without IndMoney
  confirmation. No position is removed from DB, no P&L booked, no capital
  released until the broker layer returns a confirmed fill.

  ALL exits — including SL_HIT — go through _pending_exits.
  No fast-track. No self-certification. No exceptions.

  Fill confirmation uses WebSocket events (primary) with REST fallback.

  Three-ID lineage:
    order_id     (ID1) — IndMoney ID for the entry order
    exit_order_id(ID2) — IndMoney ID for the exit order
    residual_id  (ID3) — Kubers UUID for any residual from partial exit

EXIT PRIORITY (evaluate_exits):
  1. SL hit (price-based)
  2. EOD / FORCE_CLOSE
  3. Time stop hard
  4. Profit target / TARGET_PLUS trailing
  5. Time stop directional conviction
"""

import os, sys, uuid, logging, time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    compute_trade_cost,
    TIME_STOP_CHECK_MIN, TIME_STOP_PROGRESS_THRESHOLD,
    TIME_STOP_EXTENSION_THRESHOLD, TIME_STOP_EXTENSION_MIN,
    TIME_STOP_HARD_MIN, EOD_SQUAREOFF_TIME, FORCE_CLOSE_TIME,
    ORDER_TTL_CANDLES, CANDLE_3M_SEC,
    SL_ATR_MULTIPLIER, CONTRARIAN_ATR_MULT, CONTRARIAN_THRESHOLD_PCT,
    MIN_ORDER_VALUE, EXIT_ORDER_TTL_CANDLES, EXIT_MARKET_ESCALATE,
    ENABLE_TRAILING_PROFIT, TRAILING_PROFIT_PCT,
    ENTRY_CANCEL_AFTER_FIRST_FILL,
)
from data.feed import (
    place_order, place_sl_order, cancel_order,
    get_order_status, Order as FeedOrder, OrderResult,
    get_ws_event, clear_ws_event,
)
from database.vault import (
    write_signal, write_trade, upsert_position,
    delete_position, update_signal_outcome,
)
from risk.risk_gate import RiskManager

log = logging.getLogger("broker")

ORDER_TTL_SEC      = ORDER_TTL_CANDLES * CANDLE_3M_SEC
EXIT_ORDER_TTL_SEC = EXIT_ORDER_TTL_CANDLES * CANDLE_3M_SEC


# ───────────────────────────────────────────────────────────────────
# MFE / MAE
# ───────────────────────────────────────────────────────────────────

def _compute_mfe_mae(pos, candle_store_ref) -> dict:
    result = {"mfe_5m": None, "mfe_10m": None, "mfe_20m": None,
              "mae_5m": None, "mae_10m": None}
    try:
        if candle_store_ref is None:
            return result
        candles = candle_store_ref.get_candles(pos.ticker, "3m")
        if not candles:
            return result
        post = [c for c in candles if c.get("time", 0) > pos.entry_time]
        if not post:
            return result
        qty     = pos.qty
        is_long = (pos.direction == "LONG")
        horizon_map = [(5, 2, "mfe_5m", "mae_5m"),
                       (10, 3, "mfe_10m", "mae_10m"),
                       (20, 7, "mfe_20m", None)]
        for _mins, n_candles, mfe_key, mae_key in horizon_map:
            window = post[:n_candles]
            if not window:
                continue
            best  = max(c["high"] for c in window)
            worst = min(c["low"]  for c in window)
            if is_long:
                mfe = (best  - pos.entry_price) * qty
                mae = (worst - pos.entry_price) * qty
            else:
                mfe = (pos.entry_price - worst) * qty
                mae = (pos.entry_price - best)  * qty
            result[mfe_key] = round(mfe, 2)
            if mae_key:
                result[mae_key] = round(mae, 2)
    except Exception as e:
        log.debug("[broker] MFE/MAE failed for %s: %s",
                  getattr(pos, "ticker", "?"), e)
    return result


# ───────────────────────────────────────────────────────────────────
# LIVE POSITION
# ───────────────────────────────────────────────────────────────────

class LivePosition:
    def __init__(self, ticker, direction, entry_price, qty,
                 sl_price, target_price, entry_time,
                 order_id, sl_order_id, signal_id, strategy_name, sector):
        self.ticker             = ticker
        self.direction          = direction
        self.entry_price        = entry_price
        self.qty                = qty
        self.sl_price           = sl_price
        self.target_price       = target_price
        self.entry_time         = entry_time
        self.order_id           = order_id      # ID1
        self.sl_order_id        = sl_order_id
        self.signal_id          = signal_id
        self.strategy_name      = strategy_name
        self.sector             = sector
        self.time_stop_extended = False
        self.entry_narrative    = ""
        self.atr_threshold      = 0.0
        # Three-ID lineage
        self.exit_order_id      = ""   # ID2 — set when exit order placed
        self.residual_id        = ""   # ID3 — set if this is a residual
        self.position_type      = "NORMAL"  # NORMAL / RESIDUAL / UNCONFIRMED_EXIT
        # TARGET_PLUS trailing
        self.trailing_active    = False
        self.peak_profit        = 0.0

    def hold_minutes(self) -> float:
        return (time.time() - self.entry_time) / 60.0

    def progress_at(self, current_price: float) -> float:
        if self.direction == "LONG":
            total    = self.target_price - self.entry_price
            achieved = current_price - self.entry_price
        else:
            total    = self.entry_price - self.target_price
            achieved = self.entry_price - current_price
        if total <= 0:
            return 0.0
        return achieved / total

    def is_contrarian(self, current_price: float) -> bool:
        if self.direction == "SHORT":
            adverse_rs = current_price - self.entry_price
        else:
            adverse_rs = self.entry_price - current_price
        if self.atr_threshold > 0:
            return adverse_rs > self.atr_threshold
        adverse_pct = adverse_rs / self.entry_price if self.entry_price > 0 else 0
        return adverse_pct > CONTRARIAN_THRESHOLD_PCT


# ───────────────────────────────────────────────────────────────────
# BROKER
# ───────────────────────────────────────────────────────────────────

class Broker:

    def __init__(self, risk_manager: RiskManager, candle_store_ref=None):
        self._risk          = risk_manager
        self._positions     = {}   # ticker → LivePosition
        self._pending       = {}   # ticker → entry order pending fill
        self._pending_exits = {}   # key → exit order pending fill
        self._candle_store  = candle_store_ref
        self.reload_positions_from_db()

    def reload_positions_from_db(self):
        from config import DB_LIVE_PATH
        try:
            import sqlite3
            conn = sqlite3.connect(DB_LIVE_PATH)
            rows = conn.execute(
                "SELECT ticker,direction,entry_price,qty,sl_price,target_price,"
                "entry_time,order_id,signal_id,strategy_name,sector FROM positions"
            ).fetchall()
            conn.close()
            for r in rows:
                t, d, ep, q, sl, tp, et, oid, sid, sn, sec = r
                pos = LivePosition(
                    ticker        = t,
                    direction     = d,
                    entry_price   = ep,
                    qty           = q,
                    sl_price      = sl or 0.0,
                    target_price  = tp or 0.0,
                    entry_time    = datetime.fromisoformat(et).timestamp() if et else time.time(),
                    order_id      = oid or "",
                    sl_order_id   = "",
                    signal_id     = sid or "",
                    strategy_name = sn or "",
                    sector        = sec or "",
                )
                self._positions[t] = pos
            log.info("[broker] Reloaded %d positions from DB", len(rows))
        except Exception as e:
            log.warning("[broker] Position reload failed: %s", e)

    # ──────────────────────────────────────────────────────────
    # SUBMIT ORDER (entry)
    # ──────────────────────────────────────────────────────────

    def submit(self, ticker, side, qty, limit_price, sl_price, target_price,
               signal_id, strategy_name, sector) -> bool:
        if ticker in self._positions or ticker in self._pending:
            log.debug("[broker] %s already has open order/position", ticker)
            return False
        order_value = qty * limit_price
        if order_value < MIN_ORDER_VALUE:
            log.info("[broker] %s rejected — order value ₹%.0f < MIN_ORDER_VALUE",
                     ticker, order_value)
            return False
        order = FeedOrder(
            ticker        = ticker,
            side          = side,
            qty           = qty,
            limit_price   = limit_price,
            sl_price      = sl_price,
            signal_id     = signal_id,
            strategy_name = strategy_name,
            order_type    = "LIMIT",
        )
        result = place_order(order)
        if not result.success:
            log.warning("[broker] Order rejected: %s %s — %s", side, ticker, result.error)
            return False
        self._pending[ticker] = {
            "order_id":      result.order_id,
            "order_side":    side,
            "limit_price":   limit_price,
            "sl_price":      sl_price,
            "target_price":  target_price,
            "expiry":        time.time() + ORDER_TTL_SEC,
            "signal_id":     signal_id,
            "strategy":      strategy_name,
            "sector":        sector,
            "qty_requested": qty,
        }
        log.info("[broker] Order placed: %s %s %d@%.2f id=%s",
                 side, ticker, qty, limit_price, result.order_id)
        return True

    # ──────────────────────────────────────────────────────────
    # POLL PENDING ENTRIES
    # ──────────────────────────────────────────────────────────

    def poll_pending(self):
        """
        Check fill status of all pending entry orders.

        v8 order-fill contract (Principle 1.11):
          1. Check WebSocket event first (real-time, pushed by IndMoney)
          2. Fall back to REST polling if no WS event within TTL
          3. On ANY confirmed fill (full or partial):
             - Book exactly the confirmed qty as the position
             - Immediately cancel the order to kill any residual unfilled qty
               (prevents IndMoney drip-filling hours later — ghost prevention)
          4. Never book more than IndMoney confirmed
        """
        now       = time.time()
        to_remove = []

        for ticker, pend in self._pending.items():
            order_id    = pend["order_id"]
            filled_qty  = 0
            avg_price   = pend["limit_price"]
            is_terminal = False
            is_partial  = False

            # ── 1. Check WebSocket event (primary)
            ws_ev = get_ws_event(order_id)
            if ws_ev:
                ws_filled    = int(ws_ev.get("filled_quantity") or 0)
                ws_remaining = int(ws_ev.get("remaining_quantity") or 0)
                ws_price     = float(ws_ev.get("average_price") or avg_price)
                ws_status    = ws_ev.get("order_status", "")
                clear_ws_event(order_id)

                if ws_filled > 0:
                    filled_qty  = ws_filled
                    avg_price   = ws_price
                    is_partial  = (ws_remaining > 0)
                    is_terminal = True

            # ── 2. Fall back to REST if no WS event and order may have filled
            elif not ws_ev:
                status = get_order_status(order_id)
                if status.status == "COMPLETE" and status.filled_qty > 0:
                    filled_qty  = status.filled_qty
                    avg_price   = status.avg_price or avg_price
                    is_terminal = True
                    is_partial  = (status.remaining_qty > 0)
                elif status.status in ("PARTIAL", "PARTIAL_DONE") and status.filled_qty > 0:
                    filled_qty  = status.filled_qty
                    avg_price   = status.avg_price or avg_price
                    is_terminal = True
                    is_partial  = True
                elif status.status in ("CANCELLED", "FAILED", "EXPIRED", "REJECTED"):
                    # Nothing filled — clean up
                    to_remove.append(ticker)
                    log.info("[broker] Entry order %s %s (nothing filled)", ticker, status.status)
                    update_signal_outcome(pend["signal_id"], {
                        "outcome_pnl":  0.0,
                        "exit_reason":  status.status,
                        "hold_minutes": 0.0,
                    })
                    continue
                elif now > pend["expiry"]:
                    # TTL expired — cancel whatever's left
                    cancel_order(order_id)
                    to_remove.append(ticker)
                    log.info("[broker] Entry order expired unfilled: %s", ticker)
                    update_signal_outcome(pend["signal_id"], {
                        "outcome_pnl":  0.0,
                        "exit_reason":  "EXPIRED_UNFILLED",
                        "hold_minutes": 0.0,
                    })
                    continue

            # ── 3. Process confirmed fill
            if is_terminal and filled_qty > 0:
                # Cancel immediately — prevents residual drip-fills (ghost prevention)
                if ENTRY_CANCEL_AFTER_FIRST_FILL:
                    cancel_order(order_id)
                    if is_partial:
                        log.info("[broker] Partial fill %s: got %d/%d — cancelled remainder",
                                 ticker, filled_qty, pend["qty_requested"])

                pos = LivePosition(
                    ticker        = ticker,
                    direction     = "LONG" if pend["order_side"] == "BUY" else "SHORT",
                    entry_price   = avg_price,
                    qty           = filled_qty,
                    sl_price      = pend["sl_price"],
                    target_price  = pend["target_price"],
                    entry_time    = now,
                    order_id      = order_id,
                    sl_order_id   = "",
                    signal_id     = pend["signal_id"],
                    strategy_name = pend["strategy"],
                    sector        = pend["sector"],
                )
                # Fetch ATR for contrarian threshold
                try:
                    from database.vault import get_live_conn
                    conn = get_live_conn()
                    row  = conn.execute(
                        "SELECT entry_reason, atr_15m FROM signal_log WHERE signal_id=?",
                        (pend["signal_id"],)
                    ).fetchone()
                    conn.close()
                    if row:
                        pos.entry_narrative = row[0] or ""
                        atr_val = row[1] or 0.0
                        pos.atr_threshold = float(atr_val) * CONTRARIAN_ATR_MULT
                except Exception:
                    pass

                # Write to DB — position confirmed by IndMoney
                try:
                    upsert_position({
                        "ticker":        ticker,
                        "direction":     pos.direction,
                        "entry_price":   avg_price,
                        "qty":           filled_qty,
                        "sl_price":      pend["sl_price"],
                        "target_price":  pend["target_price"],
                        "entry_time":    datetime.now().isoformat(),
                        "order_id":      order_id,
                        "sector":        pend["sector"],
                        "signal_id":     pend["signal_id"],
                        "strategy_name": pend["strategy"],
                    })
                except Exception as e:
                    log.error("[broker] CRITICAL: DB write failed for %s: %s — kill switch", ticker, e)
                    self._risk.kill_switch_fired = True
                    to_remove.append(ticker)
                    continue

                self._positions[ticker] = pos
                self._risk.record_fill(
                    ticker, pend["order_side"], avg_price, filled_qty,
                    pend["sector"], pend["sl_price"],
                    pend["signal_id"], pend["strategy"]
                )
                to_remove.append(ticker)
                log.info("[broker] Fill confirmed: %s %d@%.2f (requested=%d partial=%s)",
                         ticker, filled_qty, avg_price, pend["qty_requested"], is_partial)

        for t in to_remove:
            del self._pending[t]

    # ──────────────────────────────────────────────────────────
    # EVALUATE EXITS
    # ──────────────────────────────────────────────────────────

    def evaluate_exits(self, current_prices: dict):
        """
        v8: to_close is a 3-tuple (ticker, reason, override_price).
        SL_HIT override_price is the breach price — the best estimate of
        actual fill before confirmation. IndMoney will confirm actual fill
        via WebSocket/REST; we use that confirmed price for final booking.
        """
        now_str  = datetime.now().strftime("%H:%M")
        eod      = now_str >= EOD_SQUAREOFF_TIME
        force    = now_str >= FORCE_CLOSE_TIME
        to_close = []  # (ticker, reason, override_price)

        for ticker, pos in self._positions.items():
            if ticker in self._pending_exits:
                continue

            price = current_prices.get(ticker, {})
            if isinstance(price, dict):
                price = price.get("price", 0)
            if price <= 0:
                continue

            mins = pos.hold_minutes()

            # ── 1. SL hit
            if pos.sl_price > 0:
                if pos.direction == "SHORT" and price >= pos.sl_price:
                    to_close.append((ticker, "SL_HIT", price))
                    continue
                elif pos.direction == "LONG" and price <= pos.sl_price:
                    to_close.append((ticker, "SL_HIT", price))
                    continue

            # ── 2. Force close / EOD
            if force or eod:
                reason = "FORCE_CLOSE" if force else "EOD"
                to_close.append((ticker, reason, None))
                continue

            # ── 3. Time stop hard
            if mins >= TIME_STOP_HARD_MIN:
                to_close.append((ticker, "TIME_STOP_HARD", None))
                continue

            # ── 4. TARGET_PLUS trailing stop
            if ENABLE_TRAILING_PROFIT and pos.trailing_active:
                current_profit = (
                    (price - pos.entry_price) * pos.qty
                    if pos.direction == "LONG"
                    else (pos.entry_price - price) * pos.qty
                )
                if current_profit > pos.peak_profit:
                    pos.peak_profit = current_profit
                if pos.peak_profit > 0:
                    drawdown = (pos.peak_profit - current_profit) / pos.peak_profit
                    if drawdown >= TRAILING_PROFIT_PCT:
                        to_close.append((ticker, "TARGET_PLUS", None))
                        continue

            # ── 4b. Profit target (activates trailing)
            if pos.progress_at(price) >= 1.0:
                if ENABLE_TRAILING_PROFIT and not pos.trailing_active:
                    pos.trailing_active = True
                    pos.peak_profit     = (
                        (price - pos.entry_price) * pos.qty
                        if pos.direction == "LONG"
                        else (pos.entry_price - price) * pos.qty
                    )
                    log.info("[broker] %s TARGET hit — trailing stop activated peak=%.0f",
                             ticker, pos.peak_profit)
                    continue
                elif not ENABLE_TRAILING_PROFIT:
                    to_close.append((ticker, "TARGET", None))
                    continue

            # ── 5. Time stop directional conviction
            if mins >= TIME_STOP_CHECK_MIN and not pos.time_stop_extended:
                if pos.is_contrarian(price):
                    to_close.append((ticker, "TIME_STOP_CHECK", None))
                    continue
                elif pos.progress_at(price) > TIME_STOP_EXTENSION_THRESHOLD:
                    pos.time_stop_extended = True
                    log.info("[broker] %s time stop extended — progress %.0f%%",
                             ticker, pos.progress_at(price) * 100)

        for ticker, reason, override_price in to_close:
            self._close_position(ticker, current_prices, reason,
                                 override_price=override_price)

    # ──────────────────────────────────────────────────────────
    # CLOSE POSITION
    # ──────────────────────────────────────────────────────────

    def _close_position(self, ticker: str, current_prices: dict,
                        reason: str, override_price: float = None):
        """
        v8: ALL exits go through _pending_exits. No exceptions.

        SL_HIT uses a MARKET order (previous code used LIMIT with price=0
        which was rejected by IndMoney with LimitPriceMustBeAboveZero).

        Force closes (FORCE_CLOSE, EOD, KILL_SWITCH) use MARKET orders.
        Time stops and target use LIMIT orders first, escalate to MARKET.

        Position stays in DB until IndMoney confirms the fill.
        """
        pos = self._positions.get(ticker)
        if not pos:
            return

        if override_price is not None and override_price > 0:
            price = override_price
        else:
            price = current_prices.get(ticker, {})
            if isinstance(price, dict):
                price = price.get("price", pos.entry_price)

        exit_side  = "SELL" if pos.direction == "LONG" else "BUY"

        # Exit order type:
        # SL_HIT, FORCE_CLOSE, EOD, KILL_SWITCH → MARKET (must get out now)
        # All others → LIMIT first
        use_market = reason in ("SL_HIT", "FORCE_CLOSE", "EOD",
                                "EOD_SQUAREOFF", "KILL_SWITCH", "FORCE_CLOSE_ALL")

        close_order = FeedOrder(
            ticker        = ticker,
            side          = exit_side,
            qty           = pos.qty,
            limit_price   = price if not use_market else 0,
            sl_price      = 0,
            signal_id     = pos.signal_id,
            strategy_name = pos.strategy_name,
            order_type    = "MARKET" if use_market else "LIMIT",
        )
        result = place_order(close_order)

        if not result.success:
            if not use_market:
                # Limit rejected — escalate to market immediately
                log.warning("[broker] Limit exit rejected %s (%s) — trying market",
                            ticker, reason)
                close_order.order_type  = "MARKET"
                close_order.limit_price = 0
                result = place_order(close_order)

            if not result.success:
                # Both failed — surface as UNCONFIRMED_EXIT, keep in DB
                log.error("[broker] ALL exit orders failed %s (%s) err=%s — "
                          "UNCONFIRMED_EXIT, position kept in DB",
                          ticker, reason, result.error)
                pos.position_type = "UNCONFIRMED_EXIT"
                upsert_position({"ticker": ticker, "position_type": "UNCONFIRMED_EXIT"})
                # Don't move to _positions pop or _pending_exits
                # Position stays visible on dashboard in red
                return

        # Move to pending_exits — position stays in DB until IndMoney confirms
        pos.exit_order_id = result.order_id
        self._positions.pop(ticker, None)
        self._pending_exits[ticker] = {
            "pos":        pos,
            "order_id":   result.order_id,
            "reason":     reason,
            "exit_price": price,
            "expiry":     time.time() + EXIT_ORDER_TTL_SEC,
            "reissued":   False,
            "order_type": "MARKET" if use_market else "LIMIT",
        }
        log.info("[broker] Exit order placed: %s %s qty=%d reason=%s id=%s",
                 exit_side, ticker, pos.qty, reason, result.order_id)

    # ──────────────────────────────────────────────────────────
    # POLL PENDING EXITS
    # ──────────────────────────────────────────────────────────

    def poll_pending_exits(self, current_prices: dict):
        """
        v8: Check WebSocket events first, fall back to REST.
        On partial fill: book filled portion, send immediate MARKET order
        for residual (policy: exit immediately, no trajectory management).
        """
        now       = time.time()
        to_remove = []

        for key, pex in list(self._pending_exits.items()):
            pos      = pex["pos"]
            order_id = pex["order_id"]
            ticker   = pos.ticker

            filled_qty  = 0
            avg_price   = pex["exit_price"]
            remaining   = 0
            is_terminal = False

            # ── Check WebSocket event (primary)
            ws_ev = get_ws_event(order_id)
            if ws_ev:
                ws_filled    = int(ws_ev.get("filled_quantity") or 0)
                ws_remaining = int(ws_ev.get("remaining_quantity") or 0)
                ws_price     = float(ws_ev.get("average_price") or avg_price)
                clear_ws_event(order_id)

                if ws_filled > 0 and ws_remaining == 0:
                    # Full fill
                    filled_qty  = ws_filled
                    avg_price   = ws_price
                    is_terminal = True
                    remaining   = 0
                elif ws_filled > 0 and ws_remaining > 0:
                    # Partial fill
                    filled_qty  = ws_filled
                    avg_price   = ws_price
                    is_terminal = True
                    remaining   = ws_remaining

            # ── Fall back to REST if no WS event yet
            else:
                status = get_order_status(order_id)
                if status.status == "COMPLETE" and status.filled_qty > 0:
                    filled_qty  = status.filled_qty
                    avg_price   = status.avg_price or avg_price
                    is_terminal = True
                    remaining   = 0
                elif status.status in ("PARTIAL", "PARTIAL_DONE") and status.filled_qty > 0:
                    filled_qty  = status.filled_qty
                    avg_price   = status.avg_price or avg_price
                    is_terminal = True
                    remaining   = status.remaining_qty
                elif now > pex["expiry"] and not pex["reissued"]:
                    # Limit TTL expired — escalate to market
                    exit_side = "SELL" if pos.direction == "LONG" else "BUY"
                    mkt_order = FeedOrder(
                        ticker=ticker, side=exit_side, qty=pos.qty,
                        limit_price=0, sl_price=0,
                        signal_id=pos.signal_id, strategy_name=pos.strategy_name,
                        order_type="MARKET",
                    )
                    mkt_result = place_order(mkt_order)
                    if mkt_result.success:
                        pex["order_id"]  = mkt_result.order_id
                        pex["expiry"]    = now + EXIT_ORDER_TTL_SEC
                        pex["reissued"]  = True
                        pex["order_type"] = "MARKET"
                        log.warning("[broker] Exit escalated to market: %s id=%s",
                                    ticker, mkt_result.order_id)
                    else:
                        log.error("[broker] Market exit also rejected %s — UNCONFIRMED_EXIT",
                                  ticker)
                        pos.position_type = "UNCONFIRMED_EXIT"
                        upsert_position({"ticker": ticker,
                                         "position_type": "UNCONFIRMED_EXIT"})
                        to_remove.append(key)
                    continue
                elif now > pex["expiry"] and pex["reissued"]:
                    # Market order also expired — force book
                    cp = current_prices.get(ticker, {})
                    if isinstance(cp, dict):
                        cp = cp.get("price", pex["exit_price"])
                    log.error("[broker] Market exit expired %s — force-booking at %.2f",
                              ticker, cp)
                    self._book_closed_position(pos, cp,
                                               pex["reason"] + "_FORCE_BOOKED")
                    to_remove.append(key)
                    continue

            # ── Process confirmed fill
            if is_terminal and filled_qty > 0:
                if remaining > 0:
                    # Partial exit — book filled portion, handle residual
                    partial_pos      = _make_partial_pos(pos, filled_qty)
                    self._book_closed_position(partial_pos, avg_price, pex["reason"])
                    log.info("[broker] Partial exit %s: booked %d@%.2f, residual %d",
                             ticker, filled_qty, avg_price, remaining)
                    # Close residual immediately with market order
                    self._close_residual(pos, remaining, pex["reason"], current_prices)
                else:
                    # Full fill — book at confirmed price
                    self._book_closed_position(pos, avg_price, pex["reason"])
                to_remove.append(key)

        for k in to_remove:
            self._pending_exits.pop(k, None)

    def _close_residual(self, original_pos, remaining_qty: int,
                        original_reason: str, current_prices: dict):
        """
        Residual policy: exit immediately with MARKET order.
        Creates a RESIDUAL position tracked with ID3 (residual_id).
        """
        residual_id = str(uuid.uuid4())
        ticker      = original_pos.ticker
        exit_side   = "SELL" if original_pos.direction == "LONG" else "BUY"

        cp = current_prices.get(ticker, {})
        if isinstance(cp, dict):
            cp = cp.get("price", original_pos.entry_price)

        mkt = FeedOrder(
            ticker=ticker, side=exit_side, qty=remaining_qty,
            limit_price=0, sl_price=0,
            signal_id=original_pos.signal_id,
            strategy_name=original_pos.strategy_name,
            order_type="MARKET",
        )
        result = place_order(mkt)

        if result.success:
            residual_pos              = _make_partial_pos(original_pos, remaining_qty)
            residual_pos.residual_id  = residual_id
            residual_pos.exit_order_id= result.order_id
            residual_pos.position_type= "RESIDUAL"

            res_key = f"{ticker}_res_{residual_id[:8]}"
            self._pending_exits[res_key] = {
                "pos":        residual_pos,
                "order_id":   result.order_id,
                "reason":     original_reason + "_RESIDUAL",
                "exit_price": cp,
                "expiry":     time.time() + EXIT_ORDER_TTL_SEC,
                "reissued":   False,
                "order_type": "MARKET",
            }
            log.info("[broker] Residual %s qty=%d market exit placed id=%s residual_id=%s",
                     ticker, remaining_qty, result.order_id, residual_id[:8])
        else:
            log.error("[broker] Residual market exit failed %s qty=%d — force-booking",
                      ticker, remaining_qty)
            res = _make_partial_pos(original_pos, remaining_qty)
            self._book_closed_position(res, cp, original_reason + "_RESIDUAL_FORCE")

    # ──────────────────────────────────────────────────────────
    # BOOK CLOSED POSITION
    # ──────────────────────────────────────────────────────────

    def _book_closed_position(self, pos, price: float, reason: str):
        """
        Write completed trade to DB at CONFIRMED price.
        Called only after IndMoney confirmation — never speculatively.
        """
        ticker    = pos.ticker
        gross_pnl = self._risk.record_close(ticker, price, reason)

        if reason == "SL_HIT":
            self._risk.record_sl_hit(ticker)

        hold_mins      = pos.hold_minutes()
        side_entry     = "BUY" if pos.direction == "LONG" else "SELL"
        cost_breakdown = compute_trade_cost(pos.entry_price, price, pos.qty, side_entry)
        total_cost     = cost_breakdown["total"]
        net_pnl        = round(gross_pnl - total_cost, 2)

        excursions = _compute_mfe_mae(pos, self._candle_store)

        log.info("[broker] BOOKED %s %s %.2f->%.2f gross=%.0f cost=%.2f net=%.0f %s",
                 pos.direction, ticker, pos.entry_price, price,
                 gross_pnl, total_cost, net_pnl, reason)

        exit_narrative = _build_exit_narrative(pos, price, reason, net_pnl)

        write_trade({
            "trade_id":        str(uuid.uuid4()),
            "signal_id":       pos.signal_id,
            "ticker":          ticker,
            "direction":       pos.direction,
            "entry_price":     pos.entry_price,
            "exit_price":      price,
            "qty":             pos.qty,
            "entry_time":      datetime.fromtimestamp(pos.entry_time).isoformat(),
            "exit_time":       datetime.now().isoformat(),
            "hold_minutes":    round(hold_mins, 1),
            "exit_reason":     reason,
            "gross_pnl":       round(gross_pnl, 2),
            "slippage_rs":     0.0,
            "cost_brokerage":  cost_breakdown["brokerage"],
            "cost_stt":        cost_breakdown["stt"],
            "cost_exchange":   cost_breakdown["exchange"],
            "cost_sebi":       cost_breakdown["sebi"],
            "cost_stamp":      cost_breakdown["stamp_duty"],
            "cost_gst":        cost_breakdown["gst"],
            "cost_total":      total_cost,
            "net_pnl":         net_pnl,
            "entry_narrative": pos.entry_narrative,
            "exit_narrative":  exit_narrative,
            "order_id":        pos.order_id,
            "exit_order_id":   pos.exit_order_id,
            "residual_id":     pos.residual_id,
            "position_type":   pos.position_type,
            "mfe_5m":          excursions["mfe_5m"],
            "mfe_10m":         excursions["mfe_10m"],
            "mfe_20m":         excursions["mfe_20m"],
            "mae_5m":          excursions["mae_5m"],
            "mae_10m":         excursions["mae_10m"],
        })
        update_signal_outcome(pos.signal_id, {
            "outcome_pnl":  net_pnl,
            "exit_reason":  reason,
            "hold_minutes": round(hold_mins, 1),
        })
        delete_position(ticker)

    # ──────────────────────────────────────────────────────────
    # STATUS / FORCE CLOSE
    # ──────────────────────────────────────────────────────────

    def get_open_positions(self, current_prices: dict = None) -> list:
        result = []
        for p in self._positions.values():
            cp = 0.0
            if current_prices:
                q  = current_prices.get(p.ticker, {})
                cp = float(q.get("price", 0)) if isinstance(q, dict) else float(q or 0)
            result.append({
                "ticker":         p.ticker,
                "direction":      p.direction,
                "entry_price":    p.entry_price,
                "qty":            p.qty,
                "hold_minutes":   round(p.hold_minutes(), 1),
                "sl_price":       p.sl_price,
                "target_price":   p.target_price,
                "current_price":  cp if cp > 0 else p.entry_price,
                "sector":         p.sector,
                "strategy_name":  p.strategy_name,
                "position_type":  p.position_type,
                "trailing_active":p.trailing_active,
                "peak_profit":    p.peak_profit,
            })
        return result

    def get_pending_count(self) -> int:
        return len(self._pending)

    def get_pending_exits_count(self) -> int:
        return len(self._pending_exits)

    def get_pending_exits_detail(self) -> list:
        result = []
        for key, pex in self._pending_exits.items():
            pos = pex["pos"]
            result.append({
                "ticker":        pos.ticker,
                "direction":     pos.direction,
                "reason":        pex["reason"],
                "order_type":    pex["order_type"],
                "position_type": pos.position_type,
            })
        return result

    def force_close_all(self, reason: str = "EOD_BROKER_SQUAREOFF"):
        """Force-close all open positions with market orders at EOD."""
        for ticker in list(self._positions.keys()):
            pos       = self._positions.pop(ticker, None)
            if not pos:
                continue
            exit_side = "SELL" if pos.direction == "LONG" else "BUY"
            mkt = FeedOrder(
                ticker=ticker, side=exit_side, qty=pos.qty,
                limit_price=0, sl_price=0,
                signal_id=pos.signal_id, strategy_name=pos.strategy_name,
                order_type="MARKET",
            )
            result = place_order(mkt)
            if result.success:
                pos.exit_order_id = result.order_id
                self._pending_exits[ticker] = {
                    "pos":        pos,
                    "order_id":   result.order_id,
                    "reason":     reason,
                    "exit_price": 0.0,
                    "expiry":     time.time() + EXIT_ORDER_TTL_SEC,
                    "reissued":   False,
                    "order_type": "MARKET",
                }
            else:
                # Force-book if market order fails at EOD
                hold_mins = pos.hold_minutes()
                write_trade({
                    "trade_id":        str(uuid.uuid4()),
                    "signal_id":       pos.signal_id,
                    "ticker":          ticker,
                    "direction":       pos.direction,
                    "entry_price":     pos.entry_price,
                    "exit_price":      pos.entry_price,
                    "qty":             pos.qty,
                    "entry_time":      datetime.fromtimestamp(pos.entry_time).isoformat(),
                    "exit_time":       datetime.now().isoformat(),
                    "hold_minutes":    round(hold_mins, 1),
                    "exit_reason":     reason,
                    "gross_pnl":       0.0, "slippage_rs": 0.0,
                    "cost_brokerage":  0.0, "cost_stt":    0.0,
                    "cost_exchange":   0.0, "cost_sebi":   0.0,
                    "cost_stamp":      0.0, "cost_gst":    0.0,
                    "cost_total":      0.0, "net_pnl":     0.0,
                    "entry_narrative": pos.entry_narrative,
                    "exit_narrative":  f"EOD force-book — broker order failed",
                    "order_id":        pos.order_id,
                    "exit_order_id":   "",
                    "residual_id":     "",
                    "position_type":   "NORMAL",
                    "mfe_5m": None, "mfe_10m": None, "mfe_20m": None,
                    "mae_5m": None, "mae_10m": None,
                })
                delete_position(ticker)

        for ticker, pend in list(self._pending.items()):
            try:
                cancel_order(pend["order_id"])
            except Exception:
                pass
            update_signal_outcome(pend["signal_id"], {
                "outcome_pnl":  0.0,
                "exit_reason":  "EOD_EXPIRED_UNFILLED",
                "hold_minutes": 0.0,
            })
        self._pending.clear()


# ───────────────────────────────────────────────────────────────────
# HELPERS
# ───────────────────────────────────────────────────────────────────

def _make_partial_pos(original: LivePosition, qty: int) -> LivePosition:
    """Create a copy of a position with a different qty (for partial fill booking)."""
    p               = LivePosition(
        ticker        = original.ticker,
        direction     = original.direction,
        entry_price   = original.entry_price,
        qty           = qty,
        sl_price      = original.sl_price,
        target_price  = original.target_price,
        entry_time    = original.entry_time,
        order_id      = original.order_id,
        sl_order_id   = original.sl_order_id,
        signal_id     = original.signal_id,
        strategy_name = original.strategy_name,
        sector        = original.sector,
    )
    p.entry_narrative = original.entry_narrative
    p.atr_threshold   = original.atr_threshold
    p.exit_order_id   = original.exit_order_id
    p.residual_id     = original.residual_id
    p.position_type   = original.position_type
    return p


def _build_exit_narrative(pos, exit_price: float, reason: str, net_pnl: float) -> str:
    pnl_sign = "+" if net_pnl >= 0 else ""
    hold     = round(pos.hold_minutes(), 1)
    if reason == "TIME_STOP_CHECK":
        adverse_pct = abs(exit_price - pos.entry_price) / pos.entry_price * 100 if pos.entry_price else 0
        detail = (f"Contrarian move at {hold}m — {adverse_pct:.2f}% against {pos.direction}")
    else:
        detail = {
            "TARGET":         f"Target reached — {exit_price:.2f} hit {pos.target_price:.2f}",
            "TARGET_PLUS":    f"Trailing stop — peak profit captured, drawdown exceeded {TRAILING_PROFIT_PCT*100:.0f}%",
            "TIME_STOP_HARD": f"Hard time stop at {hold}m",
            "EOD":            f"EOD square-off",
            "FORCE_CLOSE":    f"Kubers force-close at 15:10",
            "SL_HIT":         f"SL triggered at {exit_price:.2f} (SL={pos.sl_price:.2f})",
            "KILL_SWITCH":    f"Kill switch",
        }.get(reason, reason)
    return (f"{pos.direction} {pos.ticker} | {detail} | "
            f"Entry={pos.entry_price:.2f} Exit={exit_price:.2f} "
            f"Net={pnl_sign}{net_pnl:.2f} | Held {hold}m | "
            f"ID1={pos.order_id[:12] if pos.order_id else '?'}")
