"""
KUBER'S CALLING — risk/risk_gate.py
=====================================
Layer 4: Single risk checkpoint. Every order — live, shadow, EOD,
kill switch — passes through validate_order() before execution.

PRINCIPLE: Nothing bypasses this layer. If it does, it is a bug.

All thresholds come from config.py. None are hardcoded here.
"""

import math
import os
import sys
import time
from datetime import datetime, time as dtime
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import (
    DEFAULT_GLOBAL_LIMIT,
    DEFAULT_PER_STOCK_LIMIT,
    DEFAULT_EQUITY_FLOOR,
    MAX_SECTOR_POSITIONS,
    MAX_LONGS_IN_DOWN_MARKET,
    MAX_SHORTS_IN_UP_MARKET,
    NIFTY_DIRECTIONAL_THRESHOLD,
    NIFTY_HARD_DIRECTIONAL_THRESHOLD,
    OPEN_PROTECTION_END,
    OPEN_POSITION_SIZE_PCT,
    GAP_DAY_NIFTY_THRESHOLD,
    GAP_DAY_PROTECTION_END,
    ENABLE_VELOCITY_CAP,
    VELOCITY_CAP_MAX_ENTRIES,
    VELOCITY_CAP_WINDOW_SEC,
    ENABLE_MIDDAY_BLACKOUT,
    MIDDAY_BLACKOUT_START,
    MIDDAY_BLACKOUT_END,
    ENABLE_GAP_DAY_EXTENSION,
    ENABLE_PARTIAL_FILL_CHECK,
    STARTING_EQUITY,
    SL_COOLDOWN_MIN,
    SESSION_START,
    MIN_ORDER_VALUE,
    MAX_ORDER_VALUE,
)

from database.vault import (
    upsert_position,
    delete_position,
    load_open_positions,
)


class RejectionReason:
    KILL_SWITCH        = "KILL_SWITCH"
    GLOBAL_LIMIT       = "GLOBAL_LIMIT"
    PER_STOCK_LIMIT    = "PER_STOCK_LIMIT"
    SECTOR_CAP         = "SECTOR_CAP"
    DIRECTIONAL_LONG   = "DIRECTIONAL_LONG_CAP"
    DIRECTIONAL_SHORT  = "DIRECTIONAL_SHORT_CAP"
    OPEN_PROTECTION    = "OPEN_PROTECTION_NO_SHORT"
    VELOCITY_CAP       = "VELOCITY_CAP"
    MIDDAY_BLACKOUT    = "MIDDAY_BLACKOUT"
    SL_COOLDOWN        = "SL_COOLDOWN"
    APPROVED           = "APPROVED"
    PARTIAL_FILL       = "PARTIAL_FILL_DETECTED"


class OrderResult:
    """Returned by validate_order(). Approved if qty > 0."""
    def __init__(self, qty: int, reason: str, adjusted_price: float = 0.0):
        self.qty            = qty
        self.reason         = reason
        self.approved       = (qty > 0)
        self.adjusted_price = adjusted_price

    def __repr__(self):
        return f"OrderResult(qty={self.qty}, approved={self.approved}, reason={self.reason})"


class RiskManager:
    """
    Stateful risk manager. One instance lives for the entire session.
    Recovered from DB on restart via load_open_positions().

    All thresholds are read from config at instantiation.
    Dashboard can update global_limit, per_stock_limit, equity_floor
    at runtime by calling update_live_params().
    """

    def __init__(self):
        # ── Capital limits (dashboard-tunable at runtime)
        self.global_limit    = DEFAULT_GLOBAL_LIMIT
        self.per_stock_limit = DEFAULT_PER_STOCK_LIMIT
        self.equity_floor    = DEFAULT_EQUITY_FLOOR
        self.max_order_value = MAX_ORDER_VALUE

        # ── Live state
        self.live_positions  = {}    # ticker → position dict
        self.kill_switch_fired = False
        self.current_equity  = float(STARTING_EQUITY)
        self.session_pnl     = 0.0

        # ── Velocity cap state
        self._entry_timestamps = deque()  # timestamps of recent entries

        # ── SL cooldown — {ticker: timestamp_of_sl_hit}
        # Blocks re-entry on same ticker for SL_COOLDOWN_MIN minutes after SL hit.
        self._sl_cooldown = {}   # ticker → float (time.time() of SL hit)

        # ── Recover open positions from DB on startup
        self._recover_positions()

    # ─────────────────────────────────────────────────────────────
    # PRIMARY METHOD — validate_order()
    # ─────────────────────────────────────────────────────────────

    def validate_order(
        self,
        ticker:     str,
        side:       str,    # 'BUY' or 'SELL'
        price:      float,
        qty:        int,
        sector:     str,
        vol_z:      float,
        nifty_change: float,  # fractional change from open, e.g. -0.009
    ) -> tuple:
        """
        Validate a proposed order against all risk rules.

        Returns: (approved_qty: int, reason: str)
        approved_qty == 0 means rejected.
        approved_qty < qty means size-reduced.
        """
        now_str = datetime.now().strftime("%H:%M")

        # ── 1. Kill switch — hard stop
        if self.kill_switch_fired:
            return 0, RejectionReason.KILL_SWITCH

        # ── 1.5. SL cooldown — block re-entry on same ticker after SL hit
        if ticker in self._sl_cooldown:
            elapsed_min = (time.time() - self._sl_cooldown[ticker]) / 60  # 60 = seconds per minute (unit conversion, not a threshold)
            if elapsed_min < SL_COOLDOWN_MIN:
                remaining = int(SL_COOLDOWN_MIN - elapsed_min)
                return 0, f"{RejectionReason.SL_COOLDOWN}:{remaining}m"
            else:
                # Cooldown expired — clear it
                del self._sl_cooldown[ticker]

        # ── 2. Midday blackout
        if ENABLE_MIDDAY_BLACKOUT:
            if self._time_in_window(now_str, MIDDAY_BLACKOUT_START, MIDDAY_BLACKOUT_END):
                return 0, RejectionReason.MIDDAY_BLACKOUT

        # ── 3. Open protection — no shorts
        if self._is_open_protection_window(nifty_change):
            if side == "SELL":
                return 0, RejectionReason.OPEN_PROTECTION

        # ── 4. Velocity cap — too many entries in rolling window
        if ENABLE_VELOCITY_CAP and self.check_velocity_cap():
            return 0, RejectionReason.VELOCITY_CAP

        # ── 5. Sector concentration cap
        req_direction = "LONG" if side == "BUY" else "SHORT"
        sector_count = sum(
            1 for pos in self.live_positions.values()
            if pos.get("sector") == sector and pos.get("direction") == req_direction
        )
        if sector_count >= MAX_SECTOR_POSITIONS:
            return 0, RejectionReason.SECTOR_CAP

        # ── 6. Directional market caps
        if nifty_change < -NIFTY_HARD_DIRECTIONAL_THRESHOLD:
            long_count = sum(
                1 for p in self.live_positions.values()
                if p.get("direction") == "LONG"
            )
            if side == "BUY" and long_count >= MAX_LONGS_IN_DOWN_MARKET:
                return 0, RejectionReason.DIRECTIONAL_LONG

        if nifty_change > NIFTY_HARD_DIRECTIONAL_THRESHOLD:
            short_count = sum(
                1 for p in self.live_positions.values()
                if p.get("direction") == "SHORT"
            )
            if side == "SELL" and short_count >= MAX_SHORTS_IN_UP_MARKET:
                return 0, RejectionReason.DIRECTIONAL_SHORT

        # ── 7. Capital sizing
        order_value = price * qty
        deployed    = self._total_deployed()

        # Per-stock limit check
        per_limit = self.per_stock_limit
        if order_value > per_limit:
            qty = math.ceil(per_limit / price)

        # Clamp between MIN_ORDER_VALUE and MAX_ORDER_VALUE
        qty = max(math.ceil(MIN_ORDER_VALUE / price), min(qty, int(self.max_order_value / price)))
        qty = max(1, qty)

        # Open protection halving AFTER clamp so MIN_ORDER_VALUE
        # does not override the intentional size reduction (UT-010)
        if self._is_open_protection_window(nifty_change):  # UT-010
            qty = max(1, int(qty * OPEN_POSITION_SIZE_PCT))

        order_value = price * qty

        # Global limit check
        if deployed + order_value > self.global_limit:
            headroom = self.global_limit - deployed
            if headroom <= 0:
                return 0, RejectionReason.GLOBAL_LIMIT
            qty        = max(1, int(headroom / price))
            order_value = price * qty

        if qty <= 0:
            return 0, RejectionReason.GLOBAL_LIMIT

        return qty, RejectionReason.APPROVED

    # ─────────────────────────────────────────────────────────────
    # KILL SWITCH
    # ─────────────────────────────────────────────────────────────

    def check_kill_switch(self) -> bool:
        """
        Evaluate kill switch condition.
        Returns True if kill switch fires (or was already fired).
        Caller must cancel all open orders and halt engine.
        """
        if self.kill_switch_fired:
            return True
        if self.current_equity <= self.equity_floor:
            self.kill_switch_fired = True
            return True
        return False

    def update_equity(self, realised_pnl: float):
        """Called on every trade close to update equity tracker."""
        self.session_pnl    += realised_pnl
        self.current_equity  = STARTING_EQUITY + self.session_pnl

    # ─────────────────────────────────────────────────────────────
    # POSITION MANAGEMENT
    # ─────────────────────────────────────────────────────────────

    def record_fill(self, ticker: str, side: str, price: float,
                    qty: int, sector: str, sl_price: float,
                    signal_id: str = "", strategy_name: str = ""):
        """
        Called when an order fills. Records position in memory and DB.
        Record the entry timestamp for velocity cap tracking.
        """
        direction = "LONG" if side == "BUY" else "SHORT"
        self.live_positions[ticker] = {
            "ticker":        ticker,
            "direction":     direction,   # LONG/SHORT — matches vault schema and recovery rows
            "entry_price":   price,
            "qty":           qty,
            "sector":        sector,
            "sl_price":      sl_price,
            "entry_time":    datetime.now().isoformat(),
            "signal_id":     signal_id,
            "strategy_name": strategy_name,
        }
        upsert_position(self.live_positions[ticker])
        self.record_entry_timestamp()

    def record_close(self, ticker: str, exit_price: float,
                     exit_reason: str) -> float:
        """
        Called when a position closes. Returns realised PnL.
        Removes from memory and DB.
        """
        pos = self.live_positions.pop(ticker, None)
        if not pos:
            return 0.0

        qty = pos["qty"]
        if pos["direction"] == "LONG":
            pnl = (exit_price - pos["entry_price"]) * qty
        else:
            pnl = (pos["entry_price"] - exit_price) * qty

        self.update_equity(pnl)
        delete_position(ticker)
        return pnl

    # ─────────────────────────────────────────────────────────────
    # VELOCITY CAP
    # ─────────────────────────────────────────────────────────────

    def record_entry_timestamp(self):
        """Record that a new entry occurred right now."""
        self._entry_timestamps.append(time.time())

    def record_sl_hit(self, ticker: str):
        """
        Record that this ticker just hit its stop-loss.
        Blocks re-entry on the same ticker for SL_COOLDOWN_MIN minutes.
        Called by broker._book_closed_position() when exit_reason == 'SL_HIT'.
        """
        self._sl_cooldown[ticker] = time.time()
        import logging
        logging.getLogger("risk_gate").info(
            "[risk] SL cooldown started: %s — blocked for %d min", ticker, SL_COOLDOWN_MIN
        )

    def check_velocity_cap(self) -> bool:
        """
        Returns True if we have hit VELOCITY_CAP_MAX_ENTRIES entries
        in the last VELOCITY_CAP_WINDOW_SEC seconds.
        Prunes old timestamps first.
        """
        now = time.time()
        cutoff = now - VELOCITY_CAP_WINDOW_SEC
        # Prune expired entries
        while self._entry_timestamps and self._entry_timestamps[0] < cutoff:
            self._entry_timestamps.popleft()
        return len(self._entry_timestamps) >= VELOCITY_CAP_MAX_ENTRIES

    # ─────────────────────────────────────────────────────────────
    # LIVE PARAMETER UPDATES (dashboard-callable)
    # ─────────────────────────────────────────────────────────────

    def update_live_params(self, global_limit: float = None,
                           per_stock_limit: float = None,
                           equity_floor: float = None,
                           max_order_value: float = None):
        """Update capital parameters at runtime. Takes effect next cycle."""
        if global_limit    is not None: self.global_limit    = global_limit
        if per_stock_limit is not None: self.per_stock_limit = per_stock_limit
        if equity_floor    is not None: self.equity_floor    = equity_floor
        if max_order_value is not None: self.max_order_value = max_order_value

    # ─────────────────────────────────────────────────────────────
    # STATE SNAPSHOT (for dashboard)
    # ─────────────────────────────────────────────────────────────

    def get_state(self) -> dict:
        return {
            "kill_switch_fired": self.kill_switch_fired,
            "current_equity":    self.current_equity,
            "session_pnl":       self.session_pnl,
            "deployed_capital":  self._total_deployed(),
            "open_positions":    len(self.live_positions),
            "global_limit":      self.global_limit,
            "per_stock_limit":   self.per_stock_limit,
            "equity_floor":      self.equity_floor,
        }

    # ─────────────────────────────────────────────────────────────
    # PRIVATE HELPERS
    # ─────────────────────────────────────────────────────────────

    def _total_deployed(self) -> float:
        return sum(
            p["entry_price"] * p["qty"]
            for p in self.live_positions.values()
        )

    def _is_open_protection_window(self, nifty_change: float = 0.0) -> bool:
        now_str = datetime.now().strftime("%H:%M")
        end = GAP_DAY_PROTECTION_END if (
            ENABLE_GAP_DAY_EXTENSION and
            abs(nifty_change) > GAP_DAY_NIFTY_THRESHOLD
        ) else OPEN_PROTECTION_END
        return self._time_in_window(now_str, SESSION_START, end)

    @staticmethod
    def _time_in_window(now_str: str, start: str, end: str) -> bool:
        try:
            t   = dtime(*map(int, now_str.split(":")))
            s   = dtime(*map(int, start.split(":")))
            e   = dtime(*map(int, end.split(":")))
            return s <= t < e
        except ValueError:
            return False

    def _recover_positions(self):
        """Re-load open positions from DB on startup."""
        try:
            rows = load_open_positions()
            for row in rows:
                ticker = row.get("ticker")
                if ticker:
                    self.live_positions[ticker] = row
            if self.live_positions:
                print(f"[RISK] Recovered {len(self.live_positions)} open positions from DB")
        except Exception as e:
            print(f"[RISK] Position recovery failed: {e} — starting with empty book")