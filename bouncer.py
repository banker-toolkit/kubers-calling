"""
THE BOUNCER — Risk Management
Capital limits, kill switch, sector concentration,
correlation risk, and market direction filters.
All rules enforced here. None bypassed.
"""
import json, os, time
from datetime import datetime
from config import (
    STARTING_EQUITY, DEFAULT_EQUITY_FLOOR, DEFAULT_GLOBAL_LIMIT,
    DEFAULT_PER_STOCK_LIMIT, MAX_SECTOR_POSITIONS,
    NIFTY_DIRECTION_THRESHOLD_1, NIFTY_DIRECTION_THRESHOLD_2,
    MAX_LONGS_IN_DOWN_MARKET, MAX_SHORTS_IN_UP_MARKET,
    OPEN_PROTECTION_MINUTES, OPEN_Z_SCORE_MULTIPLIER, OPEN_POSITION_SIZE_PCT,
    OPEN_NO_SHORTS, VOL_Z_SCORE_TRIGGER
)
from database import get_sector_position_count, get_open_positions

CONFIG_FILE = "live_config.json"

class RiskManager:
    def __init__(self):
        self.deployed         = 0.0
        self.realized_pnl     = 0.0
        self.peak_equity      = STARTING_EQUITY
        self.kill_switch_fired = False
        self.session_start_time = datetime.now()
        self._reload_config()

    def _reload_config(self):
        """Reads live_config.json every cycle — dashboard changes take effect immediately."""
        defaults = {
            "global_limit":   DEFAULT_GLOBAL_LIMIT,
            "per_stock_limit": DEFAULT_PER_STOCK_LIMIT,
            "equity_floor":   DEFAULT_EQUITY_FLOOR,
        }
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE) as f:
                    cfg = json.load(f)
                self.global_limit   = float(cfg.get("global_limit",   defaults["global_limit"]))
                self.per_stock_limit = float(cfg.get("per_stock_limit", defaults["per_stock_limit"]))
                self.equity_floor   = float(cfg.get("equity_floor",   defaults["equity_floor"]))
            else:
                self.global_limit   = defaults["global_limit"]
                self.per_stock_limit = defaults["per_stock_limit"]
                self.equity_floor   = defaults["equity_floor"]
        except Exception:
            self.global_limit   = defaults["global_limit"]
            self.per_stock_limit = defaults["per_stock_limit"]
            self.equity_floor   = defaults["equity_floor"]

    @property
    def current_equity(self) -> float:
        return STARTING_EQUITY + self.realized_pnl

    def update_peak_equity(self):
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity

    def check_kill_switch(self) -> bool:
        """Returns True if kill switch should fire."""
        self._reload_config()
        self.update_peak_equity()
        if not self.kill_switch_fired:
            if self.current_equity < self.equity_floor:
                self.kill_switch_fired = True
                return True
        return self.kill_switch_fired

    def _is_open_protection_window(self) -> bool:
        now = datetime.now()
        market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
        minutes_since_open = (now - market_open).total_seconds() / 60
        return 0 <= minutes_since_open <= OPEN_PROTECTION_MINUTES

    def _get_effective_z_threshold(self) -> float:
        if self._is_open_protection_window():
            return VOL_Z_SCORE_TRIGGER * OPEN_Z_SCORE_MULTIPLIER
        return VOL_Z_SCORE_TRIGGER

    def _count_direction_positions(self, direction: str) -> int:
        positions = get_open_positions("LIVE")
        return sum(1 for p in positions if p["direction"] == direction)

    def validate_order(self, ticker: str, signal: str, price: float,
                       qty_requested: int, sector: str,
                       vol_z_score: float = 0.0,
                       nifty_open_change: float = 0.0) -> tuple:
        """
        Full pre-trade validation.
        Returns (approved_qty: int, message: str)
        approved_qty = 0 means REJECTED.
        """
        self._reload_config()

        # ── Kill switch check
        if self.kill_switch_fired:
            return 0, "KILL SWITCH ACTIVE — live trading halted"

        # ── Open protection window
        if self._is_open_protection_window():
            if OPEN_NO_SHORTS and signal == "SHORT":
                return 0, "OPEN PROTECTION: No shorts in first 15 min"
            # Reduce position size
            qty_requested = max(1, int(qty_requested * OPEN_POSITION_SIZE_PCT))

        # ── Global capital limit
        potential_cost = qty_requested * price
        if self.deployed + potential_cost > self.global_limit:
            remaining = self.global_limit - self.deployed
            if remaining < price:
                return 0, f"GLOBAL LIMIT HIT: deployed ₹{self.deployed:.0f} / ₹{self.global_limit:.0f}"
            qty_requested = max(1, int(remaining / price))
            potential_cost = qty_requested * price

        # ── Per-stock limit
        if potential_cost > self.per_stock_limit:
            qty_requested = max(1, int(self.per_stock_limit / price))
            potential_cost = qty_requested * price

        if qty_requested < 1:
            return 0, "Qty rounds to zero after limits"

        # ── Sector concentration
        sector_count = get_sector_position_count(sector, "LIVE")
        if sector_count >= MAX_SECTOR_POSITIONS:
            return 0, f"SECTOR LIMIT: {sector} at {sector_count}/{MAX_SECTOR_POSITIONS}"

        # ── Correlation / market direction risk
        if signal == "BUY":
            # In a declining market, cap total longs
            if nifty_open_change <= -NIFTY_DIRECTION_THRESHOLD_2:
                return 0, f"DIRECTION BLOCK: NIFTY down {nifty_open_change*100:.1f}% — no new longs"
            if nifty_open_change <= -NIFTY_DIRECTION_THRESHOLD_1:
                long_count = self._count_direction_positions("BUY")
                if long_count >= MAX_LONGS_IN_DOWN_MARKET:
                    return 0, f"CORRELATION LIMIT: {long_count} longs in declining market"
        elif signal == "SHORT":
            if nifty_open_change >= NIFTY_DIRECTION_THRESHOLD_2:
                return 0, f"DIRECTION BLOCK: NIFTY up {nifty_open_change*100:.1f}% — no new shorts"
            if nifty_open_change >= NIFTY_DIRECTION_THRESHOLD_1:
                short_count = self._count_direction_positions("SHORT")
                if short_count >= MAX_SHORTS_IN_UP_MARKET:
                    return 0, f"CORRELATION LIMIT: {short_count} shorts in rising market"

        # ── Equity floor proximity warning
        if self.current_equity - potential_cost < self.equity_floor * 1.05:
            qty_requested = max(1, int(qty_requested * 0.5))
            potential_cost = qty_requested * price

        # ── Reserve capital
        self.deployed += potential_cost
        msg = (f"APPROVED {signal} {ticker} | Qty:{qty_requested} | "
               f"Cost:₹{potential_cost:.2f} | Deployed:₹{self.deployed:.2f}")
        return qty_requested, msg

    def release_capital(self, qty: int, price: float):
        """Called on position close or rejected fill."""
        released = qty * price
        self.deployed = max(0.0, self.deployed - released)

    def record_pnl(self, net_pnl: float):
        self.realized_pnl += net_pnl
        self.update_peak_equity()

    def reset_daily(self):
        """Called at start of each session."""
        self.deployed = 0.0
        self.kill_switch_fired = False
        self.peak_equity = self.current_equity
        self.session_start_time = datetime.now()
