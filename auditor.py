"""
KUBER'S CALLING — observation/auditor.py
==========================================
Layer 6: Daily dossier and DB maintenance.

Triggered at DOSSIER_TIME (16:30) by the engine.
Responsible for:
  1. Writing daily dossier summary to DB
  2. Triggering candle history update via update_today()
  3. Triggering archive rollover when retention exceeded
  4. Logging regime and time-bucket P&L breakdown

RULE: update_today() is ALWAYS called here. Every day.
"""

import os, sys, sqlite3, json, logging
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_LIVE_PATH, LIVE_DB_RETENTION_DAYS, DOSSIER_TIME
from data.history_store import update_today, archive_oldest_block

log = logging.getLogger("auditor")


class Auditor:
    """
    Post-session auditor. Runs once per day at close.
    Wired into engine.py's shutdown sequence.
    """

    def __init__(self):
        self._last_dossier_date = None

    def should_run(self) -> bool:
        today = datetime.now().date().isoformat()
        now   = datetime.now().strftime("%H:%M")
        if self._last_dossier_date == today:
            return False
        return now >= DOSSIER_TIME

    def run_if_missed(self, universe: list, risk_state: dict):
        """
        Morning catchup — called once at engine startup (pre-market).

        The engine loop exits at EOD_SQUAREOFF_TIME (15:20). The dossier
        normally fires at DOSSIER_TIME (16:30) inside run_cycle() — but the
        loop already exited, so it never runs. This method detects that gap
        and runs the dossier at next-morning startup instead.

        Logic:
          - Work out the most recent trading day (yesterday, or Friday if today
            is Monday — skips the weekend).
          - Check daily_dossier table for that date.
          - If the row is absent, call run() now.
          - If the row exists, log and return — nothing to do.
        """
        today     = datetime.now()
        weekday   = today.weekday()   # 0=Mon … 6=Sun

        # Most recent trading day
        if weekday == 0:             # Monday → look back to Friday
            days_back = 3
        elif weekday == 6:           # Sunday → look back to Friday (engine shouldn't be running, but be safe)
            days_back = 2
        else:
            days_back = 1
        target_date = (today - timedelta(days=days_back)).date().isoformat()

        log.info("[auditor] run_if_missed: checking dossier for %s", target_date)

        try:
            conn = sqlite3.connect(DB_LIVE_PATH)
            row  = conn.execute(
                "SELECT date FROM daily_dossier WHERE date = ?", (target_date,)
            ).fetchone()
            conn.close()
        except sqlite3.Error as e:
            log.warning("[auditor] run_if_missed: DB check failed: %s", e)
            return

        if row:
            log.info("[auditor] Dossier for %s already exists — nothing to do", target_date)
            return

        log.info("[auditor] Dossier missing for %s — running morning catchup", target_date)

        try:
            self.run(universe, risk_state, target_date=target_date)
        except Exception as e:
            log.error("[auditor] run_if_missed: catchup failed: %s", e)
        log.info("[auditor] Morning catchup complete for %s", target_date)

    def run(self, universe: list, risk_state: dict, target_date: str = None):
        """
        Run end-of-day tasks.
        universe:    list of tickers in today's trading universe
        risk_state:  dict from RiskManager.get_state()
        target_date: ISO date string to write the dossier for.
                     Defaults to today. Pass yesterday's date for morning catchup.
        """
        today = target_date or datetime.now().date().isoformat()
        log.info("[auditor] Running end-of-day for %s", today)

        # ── 1. Update candle history (REG-006: always called here)
        log.info("[auditor] update_today() — fetching today's candles")
        candles_added = update_today(universe)
        log.info("[auditor] update_today() complete: %d rows added", candles_added)

        # ── 2. Compute daily P&L summaries
        summary = self._compute_daily_summary(today, risk_state)

        # ── 3. Write dossier
        self._write_dossier(today, summary)

        # ── 4. Archive rollover check
        oldest = self._get_oldest_signal_date()
        if oldest:
            cutoff = datetime.now() - timedelta(days=LIVE_DB_RETENTION_DAYS)
            if oldest < cutoff:
                archived = archive_oldest_block()
                log.info("[auditor] Archived %d records (retention exceeded)", archived)

        self._last_dossier_date = datetime.now().date().isoformat()  # lock on actual today, not target_date
        log.info("[auditor] End-of-day complete. Live PnL: %.0f",
                 summary.get("live_pnl", 0))
        return summary

    def _compute_daily_summary(self, date: str, risk_state: dict) -> dict:
        try:
            conn  = sqlite3.connect(DB_LIVE_PATH)
            today = f"{date}T"

            live_trades = conn.execute("""
                SELECT COUNT(*), SUM(net_pnl) FROM trade_log
                WHERE entry_time LIKE ?
            """, (today + "%",)).fetchone()

            shadow_trades = conn.execute("""
                SELECT COUNT(*), SUM(simulated_pnl) FROM shadow_log
                WHERE created_at LIKE ? AND fill_simulated=1
            """, (today + "%",)).fetchone()

            # Best shadow strategy today
            best = conn.execute("""
                SELECT strategy_name, SUM(simulated_pnl) as pnl
                FROM shadow_log
                WHERE created_at LIKE ? AND fill_simulated=1
                GROUP BY strategy_name
                ORDER BY pnl DESC LIMIT 1
            """, (today + "%",)).fetchone()

            # Time bucket breakdown
            time_rows = conn.execute("""
                SELECT sl.time_bucket, SUM(tl.net_pnl)
                FROM trade_log tl
                JOIN signal_log sl ON tl.signal_id = sl.signal_id
                WHERE tl.entry_time LIKE ?
                GROUP BY sl.time_bucket
            """, (today + "%",)).fetchall()

            conn.close()

            return {
                "live_pnl":            live_trades[1] or 0.0,
                "live_trade_count":    live_trades[0] or 0,
                "shadow_pnl":          shadow_trades[1] or 0.0,
                "shadow_trade_count":  shadow_trades[0] or 0,
                "best_shadow_strategy":best[0] if best else None,
                "best_shadow_pnl":     best[1] if best else 0.0,
                "time_breakdown":      json.dumps(dict(time_rows)),
                "equity":              risk_state.get("current_equity", 0),
            }
        except sqlite3.Error as e:
            log.error("[auditor] summary compute failed: %s", e)
            return {}

    def _write_dossier(self, date: str, summary: dict):
        try:
            conn = sqlite3.connect(DB_LIVE_PATH)
            conn.execute("""
                INSERT OR REPLACE INTO daily_dossier
                (date, live_pnl, shadow_pnl, live_trade_count,
                 shadow_trade_count, best_shadow_strategy, best_shadow_pnl,
                 time_breakdown, notes)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                date,
                summary.get("live_pnl", 0),
                summary.get("shadow_pnl", 0),
                summary.get("live_trade_count", 0),
                summary.get("shadow_trade_count", 0),
                summary.get("best_shadow_strategy"),
                summary.get("best_shadow_pnl", 0),
                summary.get("time_breakdown", "{}"),
                None,
            ))
            conn.commit()
            conn.close()
        except sqlite3.Error as e:
            log.error("[auditor] dossier write failed: %s", e)

    def _get_oldest_signal_date(self):
        try:
            conn = sqlite3.connect(DB_LIVE_PATH)
            row  = conn.execute(
                "SELECT MIN(timestamp) FROM signal_log"
            ).fetchone()
            conn.close()
            if row and row[0]:
                return datetime.fromisoformat(row[0])
        except (sqlite3.Error, ValueError):
            pass
        return None


# ── Module-level singleton
auditor = Auditor()