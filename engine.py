# -*- coding: utf-8 -*-
"""
KUBER'S CALLING — engine.py
=============================
Layer 0: Main orchestrator.

Runs every SCAN_INTERVAL_SEC during market hours.
Wires all 7 layers together. Knows about layers, not about details.

CYCLE (per tick):
  1. Fetch NIFTY + VIX
  2. Evaluate regime
  3. For each ticker: fetch price → build snapshot → evaluate strategies
  4. Route live signals through Risk Gate → Broker
  5. Shadow book evaluates every snapshot
  6. Poll fills + evaluate exits
  7. At EOD: auditor.run()

HARD CONSTRAINTS (enforced here, not configurable from dashboard):
  - EOD_SQUAREOFF_TIME: all positions closed
  - TIME_STOP_HARD_MIN: never extended beyond config value
"""

import os
os.chdir(os.path.dirname(os.path.abspath(__file__)))

import sys, time, signal, logging, uuid, json
from datetime import datetime

from config import (
    TOKEN_MAP_MIN_EXPECTED,
    SCAN_INTERVAL_SEC,
    EOD_SQUAREOFF_TIME,
    NO_NEW_ENTRIES_CUTOFF,
    DOSSIER_TIME,
    ENABLE_ML_STRATEGY,
    LIVE_DB_RETENTION_DAYS,
    DEFAULT_GLOBAL_LIMIT,
    DEFAULT_PER_STOCK_LIMIT,
    DEFAULT_EQUITY_FLOOR,
    MAX_OPEN_POSITIONS,
    SLOT_SIZE,
    MAX_SECTOR_POSITIONS,
    MAX_ENTRY_PRICE,
)

from database.vault       import init_live_db, init_decisions_db
from data.feed            import load_token, verify_connection, forge_token_map, fetch_nifty_data, fetch_vix, fetch_quotes, fetch_broker_positions, place_market_order, start_order_ws
from data.candle_factory  import candle_store
from data.history_store   import load_into_factory
from features.feature_engine import FeatureEngine, RegimeState, feature_engine, _compute_atr
from features.volume_profile import build_volume_profile
from features.sector_builder import build_ex_self_composite
from strategy.rule_strategy  import RuleStrategy
from strategy.strategy_registry import registry
from strategy.shadow_book    import shadow_book
from risk.risk_gate          import RiskManager
from execution.broker        import Broker
from observation.signal_log  import log_signal
from observation.auditor     import auditor
from universe.universe_mapper import get_universe, ticker_to_sector, ticker_to_adv_tier

log = logging.getLogger("engine")

# ── Shared state visible to dashboard via Flask ──────────────────────
_state = {
    "running":           False,
    "regime":            "INITIALISING",
    "nifty":             0.0,
    "vix":               0.0,
    "nifty_atr":         0.0,
    "nifty_open_change": 0.0,   # (nifty - nifty_open) / nifty_open; 0 pre-market
    "tickers_count":     0,     # populated each cycle for dashboard badge
    "cycle":             0,
    "positions":         [],
    "signals_today":     0,
    "session_pnl":       0.0,
    "decision_log":      [],   # recent per-ticker decisions for dashboard
    "errors":            [],
    "live_config":  {
        "global_limit":    DEFAULT_GLOBAL_LIMIT,
        "per_stock_limit": DEFAULT_PER_STOCK_LIMIT,
        "equity_floor":    DEFAULT_EQUITY_FLOOR,
    }
}

_running   = False
_risk      = None
_broker    = None
# ── Order dedup guard — prevents duplicate orders for same ticker within 10s.
# Addresses COFORGE-style 5× qty bug observed on 2026-03-17.
# {ticker: epoch_timestamp_of_last_submit}
_last_submit_time: dict = {}


# ═══════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════

def startup() -> bool:
    """
    Full startup sequence. Returns True if safe to trade.
    Mirrors Validation Agent smoke tests in runtime form.
    """
    global _risk, _broker

    log.info("=" * 60)
    log.info("  KUBER'S CALLING — ENGINE STARTUP")
    log.info("  %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    log.info("=" * 60)

    # DB init
    init_live_db()
    init_decisions_db()
    log.info("[startup] Databases initialised")

    # API auth
    if not load_token():
        log.error("[startup] ABORT — token load failed")
        return False

    conn_result = verify_connection()
    if not conn_result["ok"]:
        log.error("[startup] ABORT — API connection failed: %s", conn_result["detail"])
        return False
    log.info("[startup] %s", conn_result["detail"])

    # Token map
    n = forge_token_map()
    if n < TOKEN_MAP_MIN_EXPECTED:
        log.warning("[startup] Token map only %d tokens — scrip lookup may fail", n)
    log.info("[startup] Token map: %d tokens", n)

    # Historical candles → factory
    result = load_into_factory()
    log.info("[startup] History loaded: %d tickers, %d candles",
             result["tickers_loaded"], result["candles_loaded"])

    # ── BUG-STANDBY FIX (v8): seed NIFTY 50 historical candles at startup.
    # Without this, candle_store has 0 NIFTY candles at 09:15 → nifty_atr=0
    # → regime=STANDBY → all signals suppressed for the first 15 minutes.
    # NIFTY scrip code on INDstocks = NSE_256265 (confirmed in market_data.py).
    try:
        from data.history_store import load_nifty_into_factory
        nifty_result = load_nifty_into_factory()
        log.info("[startup] NIFTY history seeded: %d candles", nifty_result.get("total_candles", 0))
    except Exception as e:
        log.warning("[startup] NIFTY history seed failed (non-fatal, STANDBY may persist): %s", e)

    # Volume profile
    built = build_volume_profile()
    log.info("[startup] Volume profile: %d tickers", built)

    # Risk Gate
    _risk   = RiskManager()
    _broker = Broker(_risk)
    # v8: inject candle_store reference so broker can compute MFE/MAE on position close
    _broker._candle_store = candle_store
    log.info("[startup] Risk Manager initialised")

    # Strategies
    registry.register(RuleStrategy(), mode="live")
    log.info("[startup] Live strategy: RULE_V1")
    log.info("[startup] Shadow book: %d strategies", len(shadow_book.get_strategy_names()))

    try:
        catchup_universe = [u["ticker"] for u in get_universe()]
        auditor.run_if_missed(catchup_universe, _risk.get_state())
    except Exception as e:
        log.warning("[startup] Morning catchup failed (non-fatal): %s", e)

    # Fetch NIFTY/VIX — zero before market open is expected, not a startup failure.
    # REG-013 (non-zero check) is enforced in check_regime() each cycle,
    # which returns STANDBY when values are zero. That is the right gate.
    nifty = fetch_nifty_data()
    vix   = fetch_vix()
    if nifty.get("price", 0) > 0 and vix > 0:
        log.info("[startup] NIFTY=%.0f VIX=%.1f ✅", nifty["price"], vix)
        feature_engine.set_nifty_open(nifty.get("open", nifty["price"]))
    else:
        log.info("[startup] NIFTY/VIX not yet available (pre-market) — "
                 "engine will enter STANDBY until market opens")
    # Start WebSocket order updates listener (primary fill confirmation channel)
    # Token is valid all day — connection opened once, stays open.
    # Falls back to REST polling transparently if WS unavailable.
    try:
        start_order_ws()
        log.info("[startup] Order WebSocket started")
    except Exception as e:
        log.warning("[startup] Order WebSocket failed to start (REST fallback active): %s", e)

    _state["running"] = True

    # Startup reconciliation — compare broker vs DB
    _reconcile_positions()

    log.info("[startup] Startup complete — engine ready")
    return True


# ═══════════════════════════════════════════════════════════════════
# STARTUP RECONCILIATION
# ═══════════════════════════════════════════════════════════════════

def _reconcile_positions():
    """
    Compare open positions on INDmoney vs Kubers DB at startup.
    Before 15:00: warn and log, continue trading.
    After 15:00: hard close ghost positions via market order.
    Phantoms (in DB, not on broker): delete from DB immediately.
    """
    global _risk, _broker
    if not _risk or not _broker:
        return

    now_str          = datetime.now().strftime("%H:%M")
    broker_positions = fetch_broker_positions()

    if not broker_positions:
        log.info("[reconcile] No broker positions returned — skipping")
        return

    broker_tickers = {p["ticker"] for p in broker_positions}
    db_tickers     = set(_risk.live_positions.keys())

    in_broker_not_db = broker_tickers - db_tickers
    in_db_not_broker = db_tickers - broker_tickers

    if not in_broker_not_db and not in_db_not_broker:
        log.info("[reconcile] ✓ All %d positions reconciled", len(db_tickers))
        return

    log.warning("[reconcile] MISMATCH at %s — broker=%s db=%s",
                now_str, sorted(broker_tickers), sorted(db_tickers))

    # Phantoms — in DB but not on broker, delete them
    for ticker in in_db_not_broker:
        log.warning("[reconcile] PHANTOM: %s in DB but not on broker — removing", ticker)
        try:
            from database.vault import delete_position
            delete_position(ticker)
            _risk.live_positions.pop(ticker, None)
            _broker._positions.pop(ticker, None)
        except Exception as e:
            log.error("[reconcile] Phantom delete failed %s: %s", ticker, e)

    # Ghosts — on broker but not in DB
    for ticker in in_broker_not_db:
        log.warning("[reconcile] GHOST: %s on broker but not in DB", ticker)

    if now_str >= "15:00":
        log.warning("[reconcile] POST-15:00 — force closing %d ghost(s)", len(in_broker_not_db))
        for ticker in in_broker_not_db:
            bp = next((p for p in broker_positions if p["ticker"] == ticker), None)
            if not bp:
                continue
            close_side = "SELL" if bp["direction"] == "LONG" else "BUY"
            try:
                result = place_market_order(ticker, close_side, bp["qty"])
                if result.success:
                    log.warning("[reconcile] Ghost closed: %s %s %d", close_side, ticker, bp["qty"])
                else:
                    log.error("[reconcile] Ghost close FAILED %s: %s — close manually", ticker, result.error)
                    _state["errors"].append(f"GHOST_CLOSE_FAILED:{ticker}")
            except Exception as e:
                log.error("[reconcile] Ghost close error %s: %s", ticker, e)
                _state["errors"].append(f"GHOST_CLOSE_ERROR:{ticker}")
    else:
        log.warning("[reconcile] PRE-15:00 — ghosts NOT auto-closed: %s", sorted(in_broker_not_db))
        _state["errors"].append(f"RECONCILE_GHOSTS:{sorted(in_broker_not_db)}")


# ═══════════════════════════════════════════════════════════════════
# MAIN CYCLE
# ═══════════════════════════════════════════════════════════════════

def run_cycle():
    """One engine cycle. Called every SCAN_INTERVAL_SEC."""
    cycle_start = time.time()
    _state["cycle"] += 1

    # ── Fetch index data
    nifty_data = fetch_nifty_data()
    nifty_price = nifty_data.get("price", 0)
    nifty_open  = nifty_data.get("open", 0)
    nifty_volume= nifty_data.get("volume", 0)
    vix         = fetch_vix()

    # ── Tick NIFTY candles
    if nifty_price > 0:
        candle_store.tick("NIFTY 50", nifty_price, nifty_volume)
        # Set today's open once — first cycle where NIFTY is non-zero
        if feature_engine._nifty_open == 0.0 and nifty_open > 0:
            feature_engine.set_nifty_open(nifty_open)
            log.info("[engine] NIFTY open set: %.0f", nifty_open)

    # ── NIFTY ATR from 15m candles
    nifty_15m   = candle_store.get_candles("NIFTY 50", "15m")
    nifty_atr   = _compute_atr(nifty_15m, period=10)

    # ── NIFTY open change — (price - first_price_of_day) / first_price_of_day
    nifty_open_change = (
        (nifty_price - feature_engine._nifty_open) / feature_engine._nifty_open
        if feature_engine._nifty_open > 0 and nifty_price > 0
        else 0.0
    )

    # Update dashboard state — zero values are a visible warning
    _state["nifty"]             = nifty_price
    _state["vix"]               = vix
    _state["nifty_atr"]         = nifty_atr
    _state["nifty_open_change"] = nifty_open_change  # REG-018: must always be set

    # ── Regime check
    regime = feature_engine.check_regime(
        nifty_price, nifty_volume, nifty_atr, vix
    )
    _state["regime"] = regime

    # ── Fetch all universe prices in batch
    universe   = get_universe()
    tickers    = [u["ticker"] for u in universe]
    prices     = fetch_quotes(tickers)   # {ticker: {price, open, volume...}}

    # ── Tick candles for all tickers
    for ticker, q in prices.items():
        candle_store.tick(ticker, q["price"], q.get("volume", 0))

    # Build sector peer close series
    sector_closes = _build_sector_closes(tickers, prices)

    # ── Kill switch check
    if _risk.check_kill_switch():
        log.warning("[engine] KILL SWITCH FIRED — halting new signals")
        _state["regime"] = "KILL_SWITCH"

    # ── Per-ticker evaluation
    live_strategy = registry.get_live_strategy()
    decision_log  = []
    now_str = datetime.now().strftime("%H:%M")
    no_new_entries = (now_str >= NO_NEW_ENTRIES_CUTOFF)
    if no_new_entries:
        log.info("[engine] %s reached — no new entries. Exits and fills still active.", NO_NEW_ENTRIES_CUTOFF)

    # ── 15:10 force close — Kubers proactive squareoff (before IndMoney 15:20)
    # Applies to ALL positions including residuals.
    if now_str >= FORCE_CLOSE_TIME and not getattr(_broker, '_force_close_fired', False):
        log.warning("[engine] FORCE_CLOSE_TIME %s reached — closing all positions", FORCE_CLOSE_TIME)
        _broker._force_close_fired = True
        _broker.force_close_all(reason="FORCE_CLOSE")
        _state["positions"] = []

    if regime != RegimeState.STANDBY and not _risk.kill_switch_fired and not no_new_entries:

        # ── v9: Collect-Rank-Select (5-slot model) ────────────────────────────
        # Old behaviour: fire immediately on every signal → 20-50 trades/day,
        # ₹9,272 lost over 6 days mostly to transaction costs (₹9,289 in fees).
        #
        # New behaviour:
        #   1. Evaluate all tickers → collect every firing signal this cycle
        #   2. Score each by conviction (Z × sector_lag × atr_pct)
        #   3. Check available slots (MAX_OPEN_POSITIONS - currently open)
        #   4. Pick top-ranked signal per sector, up to free slots
        #   5. Only submit those — everything else is logged as RANKED_OUT
        #
        # Backtest result: -9272 actual → -2038 simulated (+7234 improvement)
        # over 6 trading days purely from fewer, better-selected positions.

        import math as _math

        # Step 1: build snapshots and collect all firing signals
        candidates = []   # list of (score, snap, result, udata)

        for udata in universe:
            ticker   = udata["ticker"]
            sector   = udata["sector"]
            adv_tier = udata["adv_tier"]
            q        = prices.get(ticker)
            if not q or q["price"] <= 0:
                continue

            # Hard price filter before building snapshot — saves CPU on obvious rejects
            if q["price"] > MAX_ENTRY_PRICE:
                continue

            snap = feature_engine.build(
                ticker            = ticker,
                price             = q["price"],
                volume            = q.get("volume", 0),
                sector            = sector,
                adv_tier          = adv_tier,
                sector_peer_closes= sector_closes.get(sector, {}),
                nifty_price       = nifty_price,
                nifty_open        = nifty_open,
                nifty_atr         = nifty_atr,
                vix               = vix,
                regime            = regime,
            )

            composite = snap.sector_composite
            feature_engine.record_opens(
                ticker, q["price"], sector,
                composite[-1] if composite else q["price"]
            )

            if not live_strategy:
                shadow_book.evaluate_all(snap)
                continue

            result = live_strategy.evaluate(snap)

            decision_entry = {
                "ticker":      ticker,
                "sector":      sector,
                "signal":      result.signal,
                "reason":      result.metadata.get("reject_reason", ""),
                "z":           snap.vol_z_score,
                "vel":         result.metadata.get("vol_vel", snap.velocity_ratio),
                "lag":         snap.sector_lag,
                "regime":      regime,
                "status_tier": "PASS",
            }

            if result.is_trade:
                decision_entry["status_tier"] = "SIGNAL"

                # Skip tickers already open or recently submitted (dedup)
                _now_check = time.time()
                if ticker in _risk.live_positions or (_now_check - _last_submit_time.get(ticker, 0)) < 60.0:
                    decision_entry["status_tier"] = "BLOCKED"
                    decision_entry["risk_reason"]  = "ALREADY_OPEN"
                    decision_log.append(decision_entry)
                    shadow_book.evaluate_all(snap)
                    continue

                # Conviction score: Z-score × sector lag × ATR-to-price ratio
                # Higher = stronger institutional signal, better risk/reward geometry
                atr_pct = snap.atr_15m / max(q["price"], 1)
                z       = abs(snap.vol_z_score) if snap.vol_z_score else 0.0
                lag     = abs(snap.sector_lag)  if snap.sector_lag  else 0.0
                score   = z * (1 + lag) * (1 + atr_pct * 100)

                candidates.append((score, snap, result, udata, decision_entry))
            else:
                decision_log.append(decision_entry)

            shadow_book.evaluate_all(snap)

        # Step 2: check available slots.
        # CRITICAL: count pending entry orders as occupied slots too.
        # Without this, slow cycles (>2.5s) cause the engine to fire duplicate
        # orders before the first fill is confirmed — creating 6+ positions.
        open_count = len(_risk.live_positions) + _broker.get_pending_count()
        free_slots = MAX_OPEN_POSITIONS - open_count

        if free_slots <= 0:
            log.debug("[engine] All %d slots occupied — no new entries this cycle", MAX_OPEN_POSITIONS)
            for _, _, _, _, de in candidates:
                de["status_tier"] = "BLOCKED"
                de["risk_reason"]  = "SLOT_CAP"
                decision_log.append(de)
        else:
            # Step 3: rank by conviction score descending
            candidates.sort(key=lambda x: x[0], reverse=True)

            # Step 4: select top 1 per sector, up to free_slots total
            selected_sectors = {pos.get("sector") for pos in _risk.live_positions.values()}
            selected_count   = 0

            for score, snap, result, udata, decision_entry in candidates:
                ticker = udata["ticker"]
                sector = udata["sector"]

                if selected_count >= free_slots:
                    decision_entry["status_tier"] = "RANKED_OUT"
                    decision_entry["risk_reason"]  = f"RANKED_OUT:slot_full (score={score:.1f})"
                    decision_log.append(decision_entry)
                    continue

                if sector in selected_sectors:
                    decision_entry["status_tier"] = "RANKED_OUT"
                    decision_entry["risk_reason"]  = f"RANKED_OUT:sector_dup ({sector})"
                    decision_log.append(decision_entry)
                    continue

                # Step 5: run through risk gate and submit
                approved_qty, risk_reason = _risk.validate_order(
                    ticker       = ticker,
                    side         = result.signal,
                    price        = result.limit_price,
                    qty          = max(1, _math.ceil(SLOT_SIZE / max(result.limit_price, 1))),
                    sector       = sector,
                    vol_z        = snap.vol_z_score,
                    nifty_change = snap.nifty_open_change,
                )

                disposition = "LIVE" if approved_qty > 0 else "RISK_REJECTED"
                decision_entry["risk_reason"] = risk_reason
                result.metadata["risk_reason"]      = risk_reason
                result.metadata["approved_qty"]     = approved_qty
                result.metadata["strategy_name"]    = live_strategy.name
                result.metadata["strategy_version"] = live_strategy.version
                result.metadata["conviction_score"] = round(score, 2)
                sid = log_signal(snap, result, disposition)
                result.metadata["signal_id"] = sid

                if approved_qty > 0:
                    _now_epoch = time.time()
                    _last_t    = _last_submit_time.get(ticker, 0)
                    if _now_epoch - _last_t < 60.0:
                        log.debug("[engine] Dedup: skipping %s (%.1fs ago)", ticker, _now_epoch - _last_t)
                    else:
                        _last_submit_time[ticker] = _now_epoch
                        _broker.submit(
                            ticker        = ticker,
                            side          = result.signal,
                            qty           = approved_qty,
                            limit_price   = result.limit_price,
                            sl_price      = result.sl_price,
                            target_price  = result.target_price,
                            signal_id     = sid,
                            strategy_name = live_strategy.name,
                            sector        = sector,
                        )
                        feature_engine.record_signal()
                        _state["signals_today"] += 1
                        decision_entry["status_tier"] = "LIVE"
                        selected_sectors.add(sector)
                        selected_count += 1
                        log.info("[engine] SLOT %d/%d | score=%.1f | %s %s %s qty=%d",
                                 open_count + selected_count, MAX_OPEN_POSITIONS,
                                 score, result.signal, ticker, sector, approved_qty)
                else:
                    decision_entry["status_tier"] = "BLOCKED"

                decision_log.append(decision_entry)

    # ── Shadow fill ticks
    shadow_book.tick_prices(prices)

    # ── Poll pending orders and evaluate exits
    _broker.poll_pending()
    _broker.poll_pending_exits(prices)
    _broker.evaluate_exits(prices)

    # ── Update dashboard state
    open_pos      = _broker.get_open_positions(current_prices=prices)
    closing_pos   = _broker.get_pending_exits_detail()
    all_pos       = open_pos + closing_pos
    _state["positions"]            = all_pos
    _state["session_pnl"]          = _risk.session_pnl
    _state["decision_log"]         = decision_log[-50:]
    _state["tickers_count"]        = len(tickers)
    _state["deployed_capital"]     = sum(p["entry_price"] * p["qty"] for p in open_pos)
    _state["pending_exits"]        = _broker.get_pending_exits_count()
    _state["pending_exits_detail"] = closing_pos

    # ── EOD dossier trigger
    now_str = datetime.now().strftime("%H:%M")
    if now_str >= DOSSIER_TIME and auditor.should_run():
        universe_tickers = [u["ticker"] for u in universe]
        auditor.run(universe_tickers, _risk.get_state())

    # Cycle timing
    elapsed = time.time() - cycle_start
    if elapsed > SCAN_INTERVAL_SEC:
        log.warning("[engine] Slow cycle: %.1fs (target %.1fs)", elapsed, SCAN_INTERVAL_SEC)


# ═══════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════

def _build_sector_closes(tickers: list, prices: dict) -> dict:
    """
    Build {sector: {ticker: [close_prices]}} from candle store.
    Used by feature_engine to build ex-self composites.
    """
    from universe.universe_mapper import ticker_to_sector
    from data.candle_factory import candle_store

    sector_data = {}
    for ticker in tickers:
        sector  = ticker_to_sector(ticker)
        candles = candle_store.get_candles(ticker, "3m")
        if candles:
            closes = [c["close"] for c in candles[-20:]]
            # Append current live price
            q = prices.get(ticker)
            if q and q.get("price", 0) > 0:
                closes.append(q["price"])
            sector_data.setdefault(sector, {})[ticker] = closes
    return sector_data


# ═══════════════════════════════════════════════════════════════════
# DASHBOARD API HELPERS
# ═══════════════════════════════════════════════════════════════════

def get_state() -> dict:
    return dict(_state)


def update_live_config(params: dict):
    """Called by dashboard when operator changes capital limits."""
    allowed = {"global_limit", "per_stock_limit", "equity_floor",
               "max_open_positions", "trailing_profit_pct"}
    cleaned = {k: v for k, v in params.items() if k in allowed}
    if _risk and cleaned:
        _risk.update_live_params(**cleaned)
        _state["live_config"].update(cleaned)
        # Update config module values at runtime so engine loop picks them up
        import config as _cfg
        if "max_open_positions" in cleaned:
            _cfg.MAX_OPEN_POSITIONS = int(cleaned["max_open_positions"])
        if "trailing_profit_pct" in cleaned:
            _cfg.TRAILING_PROFIT_PCT = float(cleaned["trailing_profit_pct"])
        if "per_stock_limit" in cleaned:
            _cfg.SLOT_SIZE = float(cleaned["per_stock_limit"])
        log.info("[engine] Live config updated: %s", cleaned)


# ═══════════════════════════════════════════════════════════════════
# GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════════════════

def shutdown():
    global _running
    _running = False
    _state["running"] = False
    log.info("[engine] Shutdown signal received")


def _signal_handler(sig, frame):
    log.info("[engine] OS signal %d received", sig)
    shutdown()


# ═══════════════════════════════════════════════════════════════════
# ENTRY POINT (run standalone — Flask uses run_cycle() directly)
# ═══════════════════════════════════════════════════════════════════

def run_standalone():
    """Run engine standalone without Flask dashboard."""
    global _running

    logging.basicConfig(
        level   = logging.INFO,
        format  = "%(asctime)s %(name)s %(levelname)s %(message)s",
        datefmt = "%H:%M:%S",
    )

    try:
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT,  _signal_handler)
    except (ValueError, OSError):
        pass  # Not in main thread

    if not startup():
        log.error("[engine] Startup failed — aborting")
        sys.exit(1)

    _running = True
    log.info("[engine] Engine running. Press Ctrl-C to stop.")

    while _running:
        now_str = datetime.now().strftime("%H:%M")
        if now_str >= EOD_SQUAREOFF_TIME:
            log.info("[engine] EOD reached (%s). Reconciling broker positions.", EOD_SQUAREOFF_TIME)
            try:
                _broker.force_close_all(reason="EOD_BROKER_SQUAREOFF")
                _state["positions"] = []
                log.info("[engine] EOD reconciliation complete — all positions cleared.")
            except Exception as e:
                log.error("[engine] EOD reconciliation error: %s", e, exc_info=True)
            log.info("[engine] Market closed (%s). Stopping.", EOD_SQUAREOFF_TIME)
            break
        try:
            run_cycle()
        except Exception as e:
            log.error("[engine] Cycle error: %s", e, exc_info=True)
            _state["errors"].append(str(e))
        time.sleep(SCAN_INTERVAL_SEC)

    log.info("[engine] Engine stopped.")


if __name__ == "__main__":
    run_standalone()