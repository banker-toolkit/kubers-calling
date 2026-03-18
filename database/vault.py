"""
KUBER'S CALLING — database/vault.py
======================================
SQLite schema and all write operations.
This is the only module that writes to any database.
All other modules call functions here — never open sqlite3 directly.

Three databases:
  Tier 1 — kubers_live.db    : rolling 60-day live trading data
  Tier 2 — kubers_archive.db : permanent historical data (ML playground)
  Tier 3 — kubers_decisions.db: owner deployment decision log
"""

import sqlite3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_LIVE_PATH, DB_ARCHIVE_PATH, DB_DECISIONS_PATH

# ── Ensure database directory exists ────────────────────────────────
os.makedirs(os.path.dirname(DB_LIVE_PATH), exist_ok=True)


# ═══════════════════════════════════════════════════════════════════
# SCHEMA CREATION
# ═══════════════════════════════════════════════════════════════════

_LIVE_SCHEMA = """
-- Historical OHLCV candles (30-day profile window)
CREATE TABLE IF NOT EXISTS historical_candles (
    ticker      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,   -- '3m' or '15m'
    time        INTEGER NOT NULL,   -- Unix timestamp
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    PRIMARY KEY (ticker, timeframe, time)
);
CREATE INDEX IF NOT EXISTS idx_hc_ticker_tf ON historical_candles(ticker, timeframe);

-- Every signal fired (live or shadow) — primary ML training dataset
CREATE TABLE IF NOT EXISTS signal_log (
    signal_id       TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    sector          TEXT,
    adv_tier        TEXT,
    disposition     TEXT NOT NULL,  -- LIVE / SHADOW / RISK_REJECTED / EXPIRED_UNFILLED

    -- Scout features
    vol_z_score     REAL,
    velocity_ratio  REAL,
    atr_15m         REAL,
    candle_count_3m INTEGER,
    candle_count_15m INTEGER,

    -- Spy features
    sector_lag_pct  REAL,
    sector_slope    REAL,
    candle_close_pct REAL,
    gap_to_target   REAL,
    news_filter_fired INTEGER DEFAULT 0,

    -- Market context
    regime          TEXT,
    nifty_open_change REAL,
    nifty_atr       REAL,
    vix             REAL,
    time_bucket     TEXT,           -- 'HH:MM' bucket
    day_of_week     INTEGER,        -- 0=Mon
    is_open_protection INTEGER DEFAULT 0,
    is_gap_day      INTEGER DEFAULT 0,
    signal_density  INTEGER,

    -- Order details
    direction       TEXT,           -- LONG / SHORT
    limit_price     REAL,
    confidence      REAL DEFAULT 1.0,
    sl_price        REAL,

    -- Full decision audit trail
    gate_trace      TEXT,           -- JSON: [{gate, passed, value, detail}]
    entry_reason    TEXT,           -- Human-readable why this trade was initiated
    risk_reason     TEXT,           -- Risk gate decision detail
    cost_breakdown  TEXT,           -- JSON: itemised transaction costs

    -- Outcome (filled by trade_log on close)
    outcome_pnl     REAL,
    exit_reason     TEXT,
    hold_minutes    REAL,
    mfe_5m          REAL,
    mfe_10m         REAL,
    mfe_20m         REAL,
    mae_5m          REAL,
    mae_10m         REAL,
    slippage_pct    REAL,

    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sl_ticker   ON signal_log(ticker);
CREATE INDEX IF NOT EXISTS idx_sl_strategy ON signal_log(strategy_name);
CREATE INDEX IF NOT EXISTS idx_sl_ts       ON signal_log(timestamp);

-- Live trade outcomes
CREATE TABLE IF NOT EXISTS trade_log (
    trade_id        TEXT PRIMARY KEY,
    signal_id       TEXT REFERENCES signal_log(signal_id),
    ticker          TEXT NOT NULL,
    direction       TEXT NOT NULL,
    entry_price     REAL,
    exit_price      REAL,
    qty             INTEGER,
    entry_time      TEXT,
    exit_time       TEXT,
    hold_minutes    REAL,
    exit_reason     TEXT,
    gross_pnl       REAL,
    slippage_rs     REAL,
    cost_brokerage  REAL,           -- INDmoney brokerage (both legs)
    cost_stt        REAL,           -- Securities Transaction Tax
    cost_exchange   REAL,           -- NSE exchange charges
    cost_sebi       REAL,           -- SEBI levy
    cost_stamp      REAL,           -- Stamp duty
    cost_gst        REAL,           -- GST on brokerage+exchange+SEBI
    cost_total      REAL,           -- Total statutory + brokerage cost
    net_pnl         REAL,           -- gross_pnl - slippage - cost_total
    entry_narrative TEXT,           -- Why entered: full Scout+Spy chain summary
    exit_narrative  TEXT,           -- Why exited: which rule triggered and values
    mfe_5m          REAL,
    mfe_10m         REAL,
    mfe_20m         REAL,
    mae_5m          REAL,
    mae_10m         REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tl_ticker ON trade_log(ticker);
CREATE INDEX IF NOT EXISTS idx_tl_ts     ON trade_log(entry_time);

-- Shadow book outcomes (with limit-order physics simulation)
CREATE TABLE IF NOT EXISTS shadow_log (
    shadow_id           TEXT PRIMARY KEY,
    strategy_name       TEXT NOT NULL,
    strategy_version    TEXT NOT NULL,
    signal_id           TEXT REFERENCES signal_log(signal_id),
    ticker              TEXT NOT NULL,
    direction           TEXT,
    simulated_entry     REAL,
    simulated_exit      REAL,
    simulated_pnl       REAL,
    fill_simulated      INTEGER DEFAULT 0,  -- 1 if price ticked through limit
    fill_latency_candles INTEGER,           -- candles until fill (NULL if unfilled)
    exit_reason         TEXT,
    hold_minutes        REAL,
    slippage_pct        REAL,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_shd_strategy ON shadow_log(strategy_name);

-- Current open positions (recovered on engine restart)
CREATE TABLE IF NOT EXISTS positions (
    ticker          TEXT PRIMARY KEY,
    direction       TEXT NOT NULL,      -- LONG / SHORT
    entry_price     REAL NOT NULL,
    qty             INTEGER NOT NULL,
    sl_price        REAL,
    target_price    REAL,
    entry_time      TEXT,
    order_id        TEXT,
    sector          TEXT,
    signal_id       TEXT,
    strategy_name   TEXT
);

-- Daily dossier summaries
CREATE TABLE IF NOT EXISTS daily_dossier (
    date                TEXT PRIMARY KEY,
    live_pnl            REAL,
    shadow_pnl          REAL,
    live_trade_count    INTEGER,
    shadow_trade_count  INTEGER,
    best_shadow_strategy TEXT,
    best_shadow_pnl     REAL,
    regime_breakdown    TEXT,   -- JSON
    time_breakdown      TEXT,   -- JSON
    sector_breakdown    TEXT,   -- JSON
    notes               TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""

_DECISIONS_SCHEMA = """
-- Every ML model ever built and its lifecycle status
CREATE TABLE IF NOT EXISTS model_registry (
    model_id        TEXT PRIMARY KEY,
    model_name      TEXT NOT NULL,
    model_type      TEXT NOT NULL,  -- 'GBM' / 'RF' / 'NN' / 'LR' / 'ENSEMBLE'
    model_file      TEXT,           -- path to serialised model
    trained_at      TEXT,
    data_start      TEXT,
    data_end        TEXT,
    trade_count     INTEGER,
    validation_score REAL,          -- Gini coefficient from walk-forward
    win_rate        REAL,
    sharpe          REAL,
    status          TEXT DEFAULT 'CANDIDATE',  -- CANDIDATE/SHADOW/LIVE/RETIRED
    pushed_at       TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);

-- Every deployment decision the owner makes
CREATE TABLE IF NOT EXISTS deployment_decisions (
    decision_id         TEXT PRIMARY KEY,
    decision_date       TEXT NOT NULL,
    models_reviewed     TEXT,       -- JSON list of model_ids reviewed
    model_chosen        TEXT,       -- model_id chosen (NULL if none deployed)
    models_rejected     TEXT,       -- JSON: {model_id: reason}
    owner_reasoning     TEXT NOT NULL,  -- free text — minimum 20 chars enforced in UI
    live_strategy_before TEXT,
    live_strategy_after TEXT,
    performance_30d     REAL,       -- live P&L in 30 days after deployment
    performance_60d     REAL,       -- live P&L in 60 days after deployment
    created_at          TEXT DEFAULT (datetime('now'))
);

-- Notes from each ML workbench session
CREATE TABLE IF NOT EXISTS session_notes (
    session_id          TEXT PRIMARY KEY,
    session_date        TEXT NOT NULL,
    data_range_start    TEXT,
    data_range_end      TEXT,
    total_trades_analysed INTEGER,
    key_findings        TEXT,       -- free text
    action_taken        TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
"""


# ═══════════════════════════════════════════════════════════════════
# CONNECTION HELPERS
# ═══════════════════════════════════════════════════════════════════

def _connect(path):
    """Open a SQLite connection with WAL mode for safe concurrent access."""
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    # PRAGMA foreign_keys intentionally OFF
    # shadow_log.signal_id uses None for un-linked shadow signals;
    # enabling FK enforcement here crashes every cycle that fires a shadow strategy.
    # conn.execute("PRAGMA foreign_keys=ON")  # REG-018: must NOT be enabled
    conn.row_factory = sqlite3.Row
    return conn


def init_live_db():
    """Create all tables in the live database. Safe to call on every startup."""
    conn = _connect(DB_LIVE_PATH)
    conn.executescript(_LIVE_SCHEMA)
    conn.commit()
    conn.close()


def init_decisions_db():
    """Create all tables in the decisions database."""
    os.makedirs(os.path.dirname(DB_DECISIONS_PATH), exist_ok=True)
    conn = _connect(DB_DECISIONS_PATH)
    conn.executescript(_DECISIONS_SCHEMA)
    conn.commit()
    conn.close()


def get_live_conn():
    """Return an open connection to the live DB. Caller must close."""
    return _connect(DB_LIVE_PATH)


def get_decisions_conn():
    """Return an open connection to the decisions DB. Caller must close."""
    return _connect(DB_DECISIONS_PATH)


def get_archive_conn():
    """Return an open connection to the archive DB. Caller must close."""
    os.makedirs(os.path.dirname(DB_ARCHIVE_PATH), exist_ok=True)
    return _connect(DB_ARCHIVE_PATH)


# ═══════════════════════════════════════════════════════════════════
# WRITE OPERATIONS — signal_log
# ═══════════════════════════════════════════════════════════════════

def write_signal(signal: dict):
    """
    Insert one signal record into signal_log.
    signal dict must include at minimum: signal_id, strategy_name,
    strategy_version, timestamp, ticker, disposition.
    All other fields optional — NULL stored if absent.
    """
    cols = list(signal.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO signal_log ({','.join(cols)}) VALUES ({placeholders})"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(signal.values()))
        conn.commit()
    finally:
        conn.close()


def update_signal_outcome(signal_id: str, outcome: dict):
    """
    Update outcome fields on an existing signal_log record.
    Called when a trade closes.
    outcome keys: outcome_pnl, exit_reason, hold_minutes,
                  mfe_5m, mfe_10m, mfe_20m, mae_5m, mae_10m, slippage_pct
    """
    sets = ", ".join(f"{k}=?" for k in outcome.keys())
    sql  = f"UPDATE signal_log SET {sets} WHERE signal_id=?"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(outcome.values()) + [signal_id])
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# WRITE OPERATIONS — trade_log
# ═══════════════════════════════════════════════════════════════════

def write_trade(trade: dict):
    """Insert one completed trade into trade_log."""
    cols = list(trade.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO trade_log ({','.join(cols)}) VALUES ({placeholders})"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(trade.values()))
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# WRITE OPERATIONS — shadow_log
# ═══════════════════════════════════════════════════════════════════

def write_shadow(shadow: dict):
    """Insert one shadow trade record into shadow_log."""
    cols = list(shadow.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO shadow_log ({','.join(cols)}) VALUES ({placeholders})"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(shadow.values()))
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# WRITE OPERATIONS — positions
# ═══════════════════════════════════════════════════════════════════

def upsert_position(position: dict):
    """Insert or update a live position record."""
    cols = list(position.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO positions ({','.join(cols)}) VALUES ({placeholders})"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(position.values()))
        conn.commit()
    finally:
        conn.close()


def delete_position(ticker: str):
    """Remove a position when it is closed."""
    conn = get_live_conn()
    try:
        conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        conn.commit()
    finally:
        conn.close()


def load_open_positions() -> list:
    """Return all open positions — called at engine startup for recovery."""
    conn = get_live_conn()
    try:
        rows = conn.execute("SELECT * FROM positions").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# WRITE OPERATIONS — historical_candles
# ═══════════════════════════════════════════════════════════════════

def write_candles_bulk(candles: list, ticker: str, timeframe: str):
    """
    Bulk-insert OHLCV candles. Silently ignores duplicates (INSERT OR IGNORE).
    candles: list of dicts with keys: time, open, high, low, close, volume
    """
    if not candles:
        return 0
    sql = """INSERT OR IGNORE INTO historical_candles
             (ticker, timeframe, time, open, high, low, close, volume)
             VALUES (?,?,?,?,?,?,?,?)"""
    rows = [
        (ticker, timeframe, c["time"], c["open"],
         c["high"], c["low"], c["close"], c["volume"])
        for c in candles
    ]
    conn = get_live_conn()
    try:
        conn.executemany(sql, rows)
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def delete_candles_before(cutoff_ts: int):
    """
    Remove candles older than cutoff_ts from historical_candles.
    Called by auditor on rollover day. Returns rows deleted.
    """
    conn = get_live_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM historical_candles WHERE time < ?", (cutoff_ts,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# ARCHIVE ROLLOVER
# ═══════════════════════════════════════════════════════════════════

def archive_signal_block(cutoff_ts_str: str) -> int:
    """
    Copy signal_log + trade_log records older than cutoff into archive DB.
    Then delete them from live DB.
    Called by auditor when LIVE_DB_RETENTION_DAYS exceeded.
    Returns number of signals archived.
    """
    live = get_live_conn()
    arch = get_archive_conn()

    # Ensure archive has same schema
    arch.executescript(_LIVE_SCHEMA)
    arch.commit()

    try:
        rows = live.execute(
            "SELECT * FROM signal_log WHERE timestamp < ?", (cutoff_ts_str,)
        ).fetchall()

        if not rows:
            return 0

        cols = [desc[0] for desc in live.execute(
            "SELECT * FROM signal_log LIMIT 0"
        ).description]
        placeholders = ",".join(["?"] * len(cols))
        insert_sql = f"INSERT OR IGNORE INTO signal_log ({','.join(cols)}) VALUES ({placeholders})"

        arch.executemany(insert_sql, [list(r) for r in rows])
        arch.commit()

        # Also archive corresponding trade_log rows
        signal_ids = tuple(r["signal_id"] for r in rows)
        if signal_ids:
            trade_rows = live.execute(
                f"SELECT * FROM trade_log WHERE signal_id IN ({','.join(['?']*len(signal_ids))})",
                signal_ids
            ).fetchall()
            if trade_rows:
                t_cols = [desc[0] for desc in live.execute(
                    "SELECT * FROM trade_log LIMIT 0"
                ).description]
                t_sql = f"INSERT OR IGNORE INTO trade_log ({','.join(t_cols)}) VALUES ({','.join(['?']*len(t_cols))})"
                arch.executemany(t_sql, [list(r) for r in trade_rows])
                arch.commit()

        # Delete from live
        live.execute("DELETE FROM signal_log WHERE timestamp < ?", (cutoff_ts_str,))
        live.commit()

        return len(rows)

    finally:
        live.close()
        arch.close()


# ═══════════════════════════════════════════════════════════════════
# DECISIONS DB — model registry
# ═══════════════════════════════════════════════════════════════════

def register_model(model: dict):
    """Insert a new model candidate into the decisions DB."""
    init_decisions_db()
    cols = list(model.keys())
    sql = f"INSERT OR REPLACE INTO model_registry ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})"
    conn = get_decisions_conn()
    try:
        conn.execute(sql, list(model.values()))
        conn.commit()
    finally:
        conn.close()


def record_deployment_decision(decision: dict):
    """
    Record a push decision made by the owner via ML Workbench.
    Enforces minimum reasoning length (20 chars) at DB level.
    """
    reasoning = decision.get("owner_reasoning", "")
    if len(reasoning.strip()) < 20:
        raise ValueError(
            "owner_reasoning must be at least 20 characters. "
            "Empty pushes are not permitted."
        )
    init_decisions_db()
    cols = list(decision.keys())
    sql = f"INSERT OR REPLACE INTO deployment_decisions ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})"
    conn = get_decisions_conn()
    try:
        conn.execute(sql, list(decision.values()))
        conn.commit()
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# STARTUP
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("[vault] Initialising all databases...")
    init_live_db()
    init_decisions_db()
    print(f"[vault] Live DB:      {DB_LIVE_PATH}")
    print(f"[vault] Archive DB:   {DB_ARCHIVE_PATH}")
    print(f"[vault] Decisions DB: {DB_DECISIONS_PATH}")
    print("[vault] All tables created. ✅")
