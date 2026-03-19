"""
KUBER'S CALLING — execution/broker.py
=======================================
Layer 5: Order execution and position lifecycle.

v8 changes:
  - _compute_mfe_mae(): computes MFE/MAE from candle_store on every close.
  - _book_closed_position(): writes mfe_5m/10m/20m, mae_5m/10m to trade_log.
  - evaluate_exits(): to_close is now a 3-tuple (ticker, reason, override_price).
    SL_HIT carries actual fill price from order status — not market price.
    Fixes MHRIL/VGUARD discrepancy where Kubers booked -₹93 but IndMoney showed -₹19.
  - _close_position(): accepts override_price kwarg. Adds explicit rejection
    logging when market order escalation fails (TARGET_FORCE_BOOKED diagnosis).
  - Broker.__init__(): accepts candle_store_ref kwarg; engine injects after creation.

EXIT PRIORITY (evaluate_exits — non-negotiable order):
  1. SL hit   (price-based fallback; sl_order_id always empty on INDstocks equity)
  2. EOD      (NON-NEGOTIABLE)
  3. Time stop hard at TIME_STOP_HARD_MIN (NON-NEGOTIABLE)
  4. Profit target
  5. Time stop directional conviction (CONTRARIAN_THRESHOLD_PCT)
"""

import os, sys, uuid, logging, time, math
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    compute_trade_cost, compute_trade_cost_total,
    TIME_STOP_CHECK_MIN, TIME_STOP_PROGRESS_THRESHOLD,
    TIME_STOP_EXTENSION_THRESHOLD, TIME_STOP_EXTENSION_MIN,
    TIME_STOP_HARD_MIN, EOD_SQUAREOFF_TIME,
    ORDER_TTL_CANDLES, CANDLE_3M_SEC,
    SL_ATR_MULTIPLIER, ENABLE_PARTIAL_FILL_CHECK,
    CONTRARIAN_THRESHOLD_PCT, CONTRARIAN_ATR_MULT,
    MIN_ORDER_VALUE,
    EXIT_ORDER_TTL_CANDLES, EXIT_MARKET_ESCALATE,
    OUTCOME_HORIZONS,
    TRAILING_PROFIT_PCT, ENABLE_TRAILING_PROFIT,
)
from data.feed import (
    place_order, place_sl_order, cancel_order,
    get_order_status, Order as FeedOrder,
)
from database.vault import (
    write_signal, write_trade, upsert_position,
    delete_position, update_signal_outcome,
)
from risk.risk_gate import RiskManager

log = logging.getLogger("broker")

ORDER_TTL_SEC      = ORDER_TTL_CANDLES * CANDLE_3M_SEC
EXIT_ORDER_TTL_SEC = EXIT_ORDER_TTL_CANDLES * CANDLE_3M_SEC


# ─────────────────────────────────────────────────────────────────────
# MFE / MAE COMPUTATION  (v8 new)
# ─────────────────────────────────────────────────────────────────────

def _compute_mfe_mae(pos, candle_store_ref) -> dict:
    """
    Maximum Favourable Excursion (MFE) and Maximum Adverse Excursion (MAE)
    over the 5m, 10m, and 20m windows after entry.  Uses 3m candles from
    candle_store_ref.  All values in ₹ P&L terms on the actual position.

    MFE positive = trade moved in our favour by that amount at best.
    MAE negative = trade moved against us by that amount at worst.

    Returns dict with keys: mfe_5m, mfe_10m, mfe_20m, mae_5m, mae_10m.
    All values are None if candle data is unavailable — safe to pass to write_trade().
    """
    result = {"mfe_5m": None, "mfe_10m": None, "mfe_20m": None,
              "mae_5m": None, "mae_10m": None}
    try:
        if candle_store_ref is None:
            return result
        candles = candle_store_ref.get_candles(pos.ticker, "3m")
        if not candles:
            return result

        # Only candles that completed AFTER entry
        post = [c for c in candles if c.get("time", 0) > pos.entry_time]
        if not post:
            return result

        qty     = pos.qty
        is_long = (pos.direction == "LONG")

        # 5m ≈ 2 candles, 10m ≈ 3 candles, 20m ≈ 7 candles  (at 3m each)
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
                mae = (worst - pos.entry_price) * qty   # negative = adverse
            else:
                mfe = (pos.entry_price - worst) * qty
                mae = (pos.entry_price - best)  * qty   # negative = adverse

            result[mfe_key] = round(mfe, 2)
            if mae_key:
                result[mae_key] = round(mae, 2)

    except Exception as e:
        log.debug("[broker] MFE/MAE failed for %s: %s",
                  getattr(pos, "ticker", "?"), e)

    return result


# ─────────────────────────────────────────────────────────────────────
# LIVE POSITION
# ─────────────────────────────────────────────────────────────────────

class LivePosition:
    """Represents one open live position."""
    def __init__(self, ticker, direction, entry_price, qty,
                 sl_price, target_price, entry_time,
                 order_id, sl_order_id, signal_id, strategy_name, sector):
        self.ticker        = ticker
        self.direction     = direction
        self.entry_price   = entry_price
        self.qty           = qty
        self.sl_price      = sl_price
        self.target_price  = target_price
        self.entry_time    = entry_time
        self.order_id      = order_id
        self.sl_order_id   = sl_order_id
        self.signal_id     = signal_id
        self.strategy_name = strategy_name
        self.sector        = sector
        self.time_stop_extended = False
        self.entry_narrative    = ""
        self.trailing_active    = False   # True once target is hit (TARGET+ mode)
        self.peak_profit        = 0.0     # highest profit seen in TARGET+ mode (₹)
        # v8.1: ATR-based contrarian threshold stored at fill time.
        # = CONTRARIAN_ATR_MULT × atr_15m from signal_log.
        # Used by is_contrarian() instead of flat percentage.
        self.atr_threshold      = 0.0

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
        """
        True if price has moved more than atr_threshold rupees AGAINST
        the trade direction since entry.

        atr_threshold = CONTRARIAN_ATR_MULT × ATR(15m) stored at fill time.
        Falls back to CONTRARIAN_THRESHOLD_PCT if atr_threshold is zero
        (positions reloaded from DB on restart won't have ATR stored).

        ATR-based is correct because a flat percentage is broken across a
        universe spanning ₹157 (IGL) to ₹23,955 (SHREECEM):
          0.15% on IGL = ₹0.24 (literal tick noise)
          0.15% on SHREECEM = ₹35.93 (almost never fires)
        ATR-based: both close after half their natural noise range.
        """
        if self.direction == "SHORT":
            adverse_rs  = current_price - self.entry_price
        else:
            adverse_rs  = self.entry_price - current_price

        if self.atr_threshold > 0:
            return adverse_rs > self.atr_threshold
        else:
            # Fallback for DB-reloaded positions without ATR stored
            adverse_pct = adverse_rs / self.entry_price if self.entry_price > 0 else 0
            return adverse_pct > CONTRARIAN_THRESHOLD_PCT


# ─────────────────────────────────────────────────────────────────────
# BROKER
# ─────────────────────────────────────────────────────────────────────

class Broker:
    """
    Manages the full lifecycle of live orders.
    One instance per session. Wired to RiskManager.
    """

    def __init__(self, risk_manager: RiskManager, candle_store_ref=None):
        self._risk          = risk_manager
        self._positions     = {}   # ticker → LivePosition
        self._pending       = {}   # ticker → entry order pending fill
        self._pending_exits = {}   # ticker → exit order pending fill
        # v8: candle_store reference for MFE/MAE on close.
        # Injected by engine after both objects are created:
        #   _broker._candle_store = candle_store
        self._candle_store  = candle_store_ref
        self.reload_positions_from_db()

    def reload_positions_from_db(self):
        import sqlite3, logging, time
        from config import DB_LIVE_PATH
        from datetime import datetime
        log = logging.getLogger("broker")
        try:
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

    # ─────────────────────────────────────────────────────────────
    # SUBMIT ORDER
    # ─────────────────────────────────────────────────────────────

    def submit(self, ticker: str, side: str, qty: int,
               limit_price: float, sl_price: float, target_price: float,
               signal_id: str, strategy_name: str, sector: str) -> bool:
        """
        Place a live limit order.
        Returns True if order accepted by broker API.
        Note: duplicate-submit guard is in engine.py (_last_submit_time dict).
        """
        if ticker in self._positions or ticker in self._pending:
            log.debug("[broker] %s already has open order/position", ticker)
            return False

        order_value = qty * limit_price
        if order_value < MIN_ORDER_VALUE:
            log.info("[broker] %s rejected — order value ₹%.0f below MIN_ORDER_VALUE ₹%.0f "
                     "(qty=%d price=%.2f)",
                     ticker, order_value, MIN_ORDER_VALUE, qty, limit_price)
            return False

        order = FeedOrder(
            ticker        = ticker,
            side          = side,
            qty           = qty,
            limit_price   = limit_price,
            sl_price      = sl_price,
            signal_id     = signal_id,
            strategy_name = strategy_name,
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

    # ─────────────────────────────────────────────────────────────
    # POLL PENDING ORDERS
    # ─────────────────────────────────────────────────────────────

    def poll_pending(self):
        """
        Check fill status of all pending entry orders.
        On fill: create LivePosition, write to DB, update RiskManager.
        On expiry: cancel order.
        Called every engine cycle.
        """
        now       = time.time()
        to_remove = []

        for ticker, pend in self._pending.items():
            status = get_order_status(pend["order_id"])

            if status.status in ("COMPLETE", "FILLED") and status.filled_qty > 0:
                filled_qty = status.filled_qty
                avg_price  = status.avg_price or pend["limit_price"]

                # NOTE: No server-side SL order placed.
                # INDstocks equity API supports only LIMIT and MARKET.
                # SL is monitored by evaluate_exits() price-breach check every 2.5s.
                pos = LivePosition(
                    ticker        = ticker,
                    direction     = "LONG" if pend["order_side"] == "BUY" else "SHORT",
                    entry_price   = avg_price,
                    qty           = filled_qty,
                    sl_price      = pend["sl_price"],
                    target_price  = pend["target_price"],
                    entry_time    = now,
                    order_id      = pend["order_id"],
                    sl_order_id   = "",
                    signal_id     = pend["signal_id"],
                    strategy_name = pend["strategy"],
                    sector        = pend["sector"],
                )

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
                        # v8.1: store ATR-based contrarian threshold at fill time
                        # so is_contrarian() uses per-stock rupee distance, not flat %
                        pos.atr_threshold = float(atr_val) * CONTRARIAN_ATR_MULT
                    else:
                        pos.entry_narrative = ""
                        pos.atr_threshold   = 0.0
                except Exception:
                    pos.entry_narrative = ""
                    pos.atr_threshold   = 0.0

                try:
                    upsert_position({
                        "ticker":        ticker,
                        "direction":     pos.direction,
                        "entry_price":   avg_price,
                        "qty":           filled_qty,
                        "sl_price":      pend["sl_price"],
                        "target_price":  pend["target_price"],
                        "entry_time":    datetime.now().isoformat(),
                        "order_id":      pend["order_id"],
                        "sector":        pend["sector"],
                        "signal_id":     pend["signal_id"],
                        "strategy_name": pend["strategy"],
                    })
                except Exception as e:
                    log.error(
                        "[broker] CRITICAL: DB write failed for %s — "
                        "triggering kill switch. Error: %s", ticker, e
                    )
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
                log.info("[broker] Fill confirmed: %s %d@%.2f SL=%.2f (engine-monitored)",
                         ticker, filled_qty, avg_price, pend["sl_price"])

            elif now > pend["expiry"]:
                cancel_order(pend["order_id"])
                to_remove.append(ticker)
                log.info("[broker] Order expired unfilled: %s", ticker)
                update_signal_outcome(pend["signal_id"], {
                    "outcome_pnl":  0.0,
                    "exit_reason":  "EXPIRED_UNFILLED",
                    "hold_minutes": 0.0,
                })

        for t in to_remove:
            del self._pending[t]

    # ─────────────────────────────────────────────────────────────
    # EXIT EVALUATION
    # ─────────────────────────────────────────────────────────────

    def evaluate_exits(self, current_prices: dict):
        """
        Evaluate all open positions for exit conditions.

        EXIT PRIORITY ORDER — do not reorder:
          1. SL hit   (price-based — sl_order_id always empty on INDstocks equity)
          2. EOD      (NON-NEGOTIABLE)
          3. Time stop hard (NON-NEGOTIABLE)
          4. Profit target
          5. Time stop directional conviction check

        v8: to_close is a 3-tuple (ticker, reason, override_price).
        override_price is set for SL_HIT when we have the actual fill price from
        order status polling.  None means use current market price.
        This fixes the fill price discrepancy vs IndMoney for SL exits.
        """
        now_str  = datetime.now().strftime("%H:%M")
        eod      = now_str >= EOD_SQUAREOFF_TIME
        # (ticker, exit_reason, override_price|None)
        to_close = []

        for ticker, pos in self._positions.items():
            if ticker in self._pending_exits:
                continue

            price = current_prices.get(ticker, {})
            if isinstance(price, dict):
                price = price.get("price", 0)
            if price <= 0:
                continue

            mins = pos.hold_minutes()

            # ── 1. SL HIT — price-based (no server-side SL on INDstocks equity)
            if pos.sl_price > 0:
                if pos.direction == "SHORT" and price >= pos.sl_price:
                    log.info("[broker] SL breach %s price=%.2f sl=%.2f",
                             ticker, price, pos.sl_price)
                    to_close.append((ticker, "SL_HIT", price))
                    continue
                elif pos.direction == "LONG" and price <= pos.sl_price:
                    log.info("[broker] SL breach %s price=%.2f sl=%.2f",
                             ticker, price, pos.sl_price)
                    to_close.append((ticker, "SL_HIT", price))
                    continue

            # ── 2. EOD — HARD, non-negotiable
            if eod:
                to_close.append((ticker, "EOD", None))
                continue

            # ── 3. Time stop hard — non-negotiable
            if mins >= TIME_STOP_HARD_MIN:
                to_close.append((ticker, "TIME_STOP_HARD", None))
                continue

            # ── 4. Profit target / TARGET+ trailing mode
            if pos.progress_at(price) >= 1.0:
                if ENABLE_TRAILING_PROFIT and not pos.trailing_active:
                    # First time hitting target — enter TARGET+ mode
                    pos.trailing_active = True
                    pos.sl_price        = pos.entry_price   # move SL to breakeven
                    current_profit      = abs(price - pos.entry_price) * pos.qty
                    pos.peak_profit     = current_profit
                    log.info("[broker] %s TARGET+ activated — trailing from peak ₹%.0f",
                             ticker, current_profit)
                elif pos.trailing_active:
                    # Already in TARGET+ — update peak and check erosion
                    current_profit  = abs(price - pos.entry_price) * pos.qty
                    pos.peak_profit = max(pos.peak_profit, current_profit)
                    erosion         = (pos.peak_profit - current_profit) / pos.peak_profit if pos.peak_profit > 0 else 0
                    if erosion >= TRAILING_PROFIT_PCT:
                        log.info("[broker] %s TARGET+ exit — peak=₹%.0f current=₹%.0f erosion=%.0f%%",
                                 ticker, pos.peak_profit, current_profit, erosion * 100)
                        to_close.append((ticker, "TARGET_PLUS", None))
                else:
                    # ENABLE_TRAILING_PROFIT is False — close immediately as before
                    to_close.append((ticker, "TARGET", None))
                continue

            # ── 5. Time stop directional conviction
            # Before TIME_STOP_CHECK_MIN: hands off completely.
            # After: close only if price is actively CONTRARIAN (> CONTRARIAN_THRESHOLD_PCT adverse).
            # Neutral (flat, or moving toward target) = hold and wait.
            if mins >= TIME_STOP_CHECK_MIN and not pos.time_stop_extended:
                if pos.is_contrarian(price):
                    log.info("[broker] %s contrarian at %.2f (entry=%.2f %.1fm) — closing",
                             ticker, price, pos.entry_price, mins)
                    to_close.append((ticker, "TIME_STOP_CHECK", None))
                    continue
                elif pos.progress_at(price) > TIME_STOP_EXTENSION_THRESHOLD:
                    pos.time_stop_extended = True
                    log.info("[broker] %s time stop extended — progress %.0f%%",
                             ticker, pos.progress_at(price) * 100)
                # else: neutral — hold and wait

        for ticker, reason, override_price in to_close:
            self._close_position(ticker, current_prices, reason,
                                 override_price=override_price)

    # ─────────────────────────────────────────────────────────────
    # CLOSE POSITION
    # ─────────────────────────────────────────────────────────────

    def _close_position(self, ticker: str, current_prices: dict,
                        reason: str, override_price: float = None):
        """
        Initiate position close.

        SL_HIT: price already confirmed as breach level; book immediately.
                override_price is the exact breach price — not a market order fill.

        All other exits: place a limit close order, move position to _pending_exits.
                         Position stays in DB until fill confirmed by poll_pending_exits().
                         On limit failure: reissue as market immediately.
                         On market failure: force-book and log the INDmoney rejection
                         code for TARGET_FORCE_BOOKED diagnosis.
        """
        pos = self._positions.get(ticker)
        if not pos:
            return

        # Use override_price if provided (SL breach level), else current market price
        if override_price is not None and override_price > 0:
            price = override_price
        else:
            price = current_prices.get(ticker, {})
            if isinstance(price, dict):
                price = price.get("price", pos.entry_price)

        # ── SL_HIT: place market close on IndMoney first, then book.
        # The engine detects SL breach by price polling — INDstocks equity has no
        # server-side SL orders. So when Kubers sees an SL breach, the position is
        # still OPEN on IndMoney. We must send a market close order to actually
        # square it off, otherwise it becomes a ghost on IndMoney's side.
        if reason == "SL_HIT":
            exit_side = "SELL" if pos.direction == "LONG" else "BUY"
            sl_market = FeedOrder(
                ticker        = ticker,
                side          = exit_side,
                qty           = pos.qty,
                limit_price   = 0,   # market order
                sl_price      = 0,
                signal_id     = pos.signal_id,
                strategy_name = pos.strategy_name,
            )
            sl_result = place_order(sl_market)
            if sl_result.success:
                log.info("[broker] SL_HIT market close sent: %s %s qty=%d id=%s",
                         exit_side, ticker, pos.qty, sl_result.order_id)
            else:
                log.error("[broker] SL_HIT market close FAILED for %s: %s — "
                          "close manually on IndMoney", ticker, sl_result.error)
            self._positions.pop(ticker, None)
            self._book_closed_position(pos, price, reason)
            return

        # ── All other exits: limit close → pending_exits
        exit_side = "SELL" if pos.direction == "LONG" else "BUY"
        close_order = FeedOrder(
            ticker        = ticker,
            side          = exit_side,
            qty           = pos.qty,
            limit_price   = price,
            sl_price      = 0,
            signal_id     = pos.signal_id,
            strategy_name = pos.strategy_name,
        )
        result = place_order(close_order)

        if not result.success:
            # Limit rejected — reissue at market immediately
            log.warning("[broker] Close limit rejected %s (%s) err=%s — reissuing market",
                        ticker, reason, result.error)
            market_order = FeedOrder(
                ticker        = ticker,
                side          = exit_side,
                qty           = pos.qty,
                limit_price   = 0,
                sl_price      = 0,
                signal_id     = pos.signal_id,
                strategy_name = pos.strategy_name,
            )
            result = place_order(market_order)
            if not result.success:
                # v8: log the exact INDmoney rejection for TARGET_FORCE_BOOKED diagnosis
                # Common causes: after-hours submission, position already closed by broker,
                # scrip suspended, or INDmoney rejecting market orders near 15:15.
                log.error("[broker] Market exit ALSO rejected %s (%s) err=%s — "
                          "force-booking. Check INDmoney rejection. Possible: already "
                          "closed by broker, near-EOD, or scrip suspended.",
                          ticker, reason, result.error)

        if result.success:
            self._positions.pop(ticker, None)
            self._pending_exits[ticker] = {
                "pos":        pos,
                "order_id":   result.order_id,
                "reason":     reason,
                "exit_price": price,
                "expiry":     time.time() + EXIT_ORDER_TTL_SEC,
                "reissued":   False,
            }
            log.info("[broker] Exit order placed: %s %s %d@%.2f reason=%s id=%s",
                     exit_side, ticker, pos.qty, price, reason, result.order_id)
        else:
            log.error("[broker] CRITICAL: all exit orders failed %s — "
                      "force-booking at %.2f as %s_FORCE_BOOKED", ticker, price, reason)
            self._positions.pop(ticker, None)
            self._book_closed_position(pos, price, reason + "_FORCE_BOOKED")

    # ─────────────────────────────────────────────────────────────
    # POLL PENDING EXITS
    # ─────────────────────────────────────────────────────────────

    def poll_pending_exits(self, current_prices: dict):
        """
        Poll all pending exit orders for fill confirmation.
        On fill confirmed  → book trade at actual fill price.
        On expiry unfilled → reissue as market order once.
        On market expiry   → force-book at current price.
        Called every engine cycle.
        """
        now       = time.time()
        to_remove = []

        for ticker, pex in self._pending_exits.items():
            try:
                status = get_order_status(pex["order_id"])
            except Exception as e:
                log.warning("[broker] Exit poll failed %s: %s", ticker, e)
                continue

            if status.status in ("COMPLETE", "FILLED") and status.filled_qty > 0:
                actual_price = status.avg_price or pex["exit_price"]
                log.info("[broker] Exit fill confirmed: %s %d@%.2f reason=%s",
                         ticker, status.filled_qty, actual_price, pex["reason"])
                self._book_closed_position(pex["pos"], actual_price, pex["reason"])
                to_remove.append(ticker)

            elif now > pex["expiry"]:
                if not pex["reissued"]:
                    pos       = pex["pos"]
                    exit_side = "SELL" if pos.direction == "LONG" else "BUY"
                    current_p = current_prices.get(ticker, {})
                    if isinstance(current_p, dict):
                        current_p = current_p.get("price", pex["exit_price"])
                    market_order = FeedOrder(
                        ticker        = ticker,
                        side          = exit_side,
                        qty           = pos.qty,
                        limit_price   = 0,
                        sl_price      = 0,
                        signal_id     = pos.signal_id,
                        strategy_name = pos.strategy_name,
                    )
                    result = place_order(market_order)
                    if result.success:
                        pex["order_id"]   = result.order_id
                        pex["expiry"]     = now + ORDER_TTL_SEC
                        pex["reissued"]   = True
                        pex["exit_price"] = current_p
                        log.warning("[broker] Exit reissued as market: %s id=%s",
                                    ticker, result.order_id)
                    else:
                        log.error("[broker] Market exit rejected %s err=%s — force-booking",
                                  ticker, result.error)
                        self._book_closed_position(
                            pex["pos"], current_p, pex["reason"] + "_FORCE_BOOKED"
                        )
                        to_remove.append(ticker)
                else:
                    current_p = current_prices.get(ticker, {})
                    if isinstance(current_p, dict):
                        current_p = current_p.get("price", pex["exit_price"])
                    log.error("[broker] Market exit expired %s — force-booking at %.2f",
                              ticker, current_p)
                    self._book_closed_position(
                        pex["pos"], current_p, pex["reason"] + "_FORCE_BOOKED"
                    )
                    to_remove.append(ticker)

        for t in to_remove:
            del self._pending_exits[t]

    # ─────────────────────────────────────────────────────────────
    # BOOK CLOSED POSITION
    # ─────────────────────────────────────────────────────────────

    def _book_closed_position(self, pos, price: float, reason: str):
        """
        Write completed trade to DB.  Called exactly once per position close —
        either on confirmed fill or force-book.

        v8: computes MFE/MAE from candle_store and writes all 5 columns.
            Uses actual confirmed fill price (passed in by caller), not a stale
            market price snapshot.
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

        # v8: MFE/MAE — None if candle data unavailable; vault.write_trade accepts None
        excursions = _compute_mfe_mae(pos, self._candle_store)

        log.info("[broker] BOOKED %s %s %.2f→%.2f gross=%.0f cost=%.2f net=%.0f %s",
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

    # ─────────────────────────────────────────────────────────────
    # STATUS
    # ─────────────────────────────────────────────────────────────

    def get_open_positions(self, current_prices: dict = None) -> list:
        result = []
        for p in self._positions.values():
            cp = 0.0
            if current_prices:
                q  = current_prices.get(p.ticker, {})
                cp = float(q.get("price", 0)) if isinstance(q, dict) else float(q or 0)
            result.append({
                "ticker":          p.ticker,
                "direction":       p.direction,
                "entry_price":     p.entry_price,
                "qty":             p.qty,
                "hold_minutes":    round(p.hold_minutes(), 1),
                "sl_price":        p.sl_price,
                "target_price":    p.target_price,
                "current_price":   cp if cp > 0 else p.entry_price,
                "sector":          p.sector,
                "strategy_name":   p.strategy_name,
                "trailing_active": p.trailing_active,
                "peak_profit":     p.peak_profit,
                "closing":         False,
            })
        return result

    def get_pending_exits_detail(self) -> list:
        """Returns positions in _pending_exits for dashboard CLOSING... display."""
        result = []
        for ticker, pex in self._pending_exits.items():
            pos = pex["pos"]
            result.append({
                "ticker":          ticker,
                "direction":       pos.direction,
                "entry_price":     pos.entry_price,
                "qty":             pos.qty,
                "hold_minutes":    round(pos.hold_minutes(), 1),
                "sl_price":        pos.sl_price,
                "target_price":    pos.target_price,
                "current_price":   pex["exit_price"],
                "sector":          pos.sector,
                "strategy_name":   pos.strategy_name,
                "trailing_active": False,
                "peak_profit":     0.0,
                "closing":         True,   # ← dashboard shows CLOSING...
                "exit_reason":     pex["reason"],
            })
        return result

    def get_pending_count(self) -> int:
        return len(self._pending)

    def get_pending_exits_count(self) -> int:
        return len(self._pending_exits)

    def force_close_all(self, reason: str = "EOD_BROKER_SQUAREOFF"):
        """
        Book all open positions as closed without placing orders.
        Called at EOD when INDmoney has already squared off on their side.
        """
        for ticker in list(self._positions.keys()):
            pos = self._positions.pop(ticker, None)
            if not pos:
                continue
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
                "gross_pnl":       0.0,
                "slippage_rs":     0.0,
                "cost_brokerage":  0.0,
                "cost_stt":        0.0,
                "cost_exchange":   0.0,
                "cost_sebi":       0.0,
                "cost_stamp":      0.0,
                "cost_gst":        0.0,
                "cost_total":      0.0,
                "net_pnl":         0.0,
                "entry_narrative": pos.entry_narrative,
                "exit_narrative":  (
                    f"{pos.direction} {ticker} | Closed by broker EOD squareoff "
                    f"(INDmoney 15:20 auto-exit) | Entry={pos.entry_price:.2f} | "
                    f"Held {round(hold_mins, 1)}m | P&L recorded in broker app"
                ),
                "mfe_5m": None, "mfe_10m": None, "mfe_20m": None,
                "mae_5m": None, "mae_10m": None,
            })
            update_signal_outcome(pos.signal_id, {
                "outcome_pnl":  0.0,
                "exit_reason":  reason,
                "hold_minutes": round(hold_mins, 1),
            })
            delete_position(ticker)
            log.info("[broker] %s force-closed (EOD squareoff)", ticker)

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
            log.info("[broker] Pending entry cancelled at EOD: %s", ticker)
        self._pending.clear()

        for ticker, pex in list(self._pending_exits.items()):
            pos = pex["pos"]
            try:
                cancel_order(pex["order_id"])
            except Exception:
                pass
            self._book_closed_position(pos, pex["exit_price"], reason)
            log.info("[broker] Pending exit force-booked at EOD: %s", ticker)
        self._pending_exits.clear()


# ─────────────────────────────────────────────────────────────────────
# EXIT NARRATIVE BUILDER
# ─────────────────────────────────────────────────────────────────────

def _build_exit_narrative(pos, exit_price: float, reason: str, net_pnl: float) -> str:
    pnl_sign = "+" if net_pnl >= 0 else ""
    hold     = round(pos.hold_minutes(), 1)

    if reason == "TARGET_PLUS":
        detail = (f"TARGET+ trailing exit — peak profit ₹{pos.peak_profit:.0f}, "
                  f"exited at ₹{net_pnl:.0f} net after 10% erosion trigger")
    elif reason == "TIME_STOP_CHECK":
        if pos.entry_price > 0:
            adverse_pct = abs(exit_price - pos.entry_price) / pos.entry_price * 100
            detail = (f"Contrarian move detected at {hold}m — price moved "
                      f"{adverse_pct:.2f}% against {pos.direction} "
                      f"(entry={pos.entry_price:.2f} exit={exit_price:.2f})")
        else:
            detail = f"Contrarian move at {hold}m"
    else:
        detail = {
            "TARGET":         f"Profit target reached — {exit_price:.2f} hit target {pos.target_price:.2f}",
            "TIME_STOP_HARD": f"Hard time stop at {hold}m (non-negotiable limit)",
            "EOD":            f"End-of-day square-off",
            "SL_HIT":         f"Stop loss triggered at {exit_price:.2f} (SL was {pos.sl_price:.2f})",
            "KILL_SWITCH":    f"Kill switch activated — all positions closed",
        }.get(reason, reason)

    return (f"{pos.direction} {pos.ticker} | {detail} | "
            f"Entry={pos.entry_price:.2f} Exit={exit_price:.2f} "
            f"Net P&L={pnl_sign}{net_pnl:.2f} | Held {hold}m")