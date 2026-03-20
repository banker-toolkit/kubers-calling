"""
KUBER'S CALLING — database/vault.py
======================================
SQLite schema and all write operations.

v8 changes:
  - migrate_live_db(): adds new columns to existing tables without losing data
  - positions: exit_order_id, residual_id, position_type columns
  - trade_log: exit_order_id, residual_id, position_type columns
  - signal_log: order_id column
  - Three-ID lineage: order_id (ID1), exit_order_id (ID2), residual_id (ID3)
  - init_live_db() calls migrate_live_db() automatically
"""

import sqlite3
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_LIVE_PATH, DB_ARCHIVE_PATH, DB_DECISIONS_PATH

os.makedirs(os.path.dirname(DB_LIVE_PATH), exist_ok=True)


# ===================================================================
# SCHEMA CREATION
# ===================================================================

_LIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS historical_candles (
    ticker      TEXT    NOT NULL,
    timeframe   TEXT    NOT NULL,
    time        INTEGER NOT NULL,
    open        REAL    NOT NULL,
    high        REAL    NOT NULL,
    low         REAL    NOT NULL,
    close       REAL    NOT NULL,
    volume      REAL    NOT NULL,
    PRIMARY KEY (ticker, timeframe, time)
);
CREATE INDEX IF NOT EXISTS idx_hc_ticker_tf ON historical_candles(ticker, timeframe);

CREATE TABLE IF NOT EXISTS signal_log (
    signal_id           TEXT PRIMARY KEY,
    strategy_name       TEXT NOT NULL,
    strategy_version    TEXT NOT NULL,
    timestamp           TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    sector              TEXT,
    adv_tier            TEXT,
    disposition         TEXT NOT NULL,
    vol_z_score         REAL,
    velocity_ratio      REAL,
    atr_15m             REAL,
    candle_count_3m     INTEGER,
    candle_count_15m    INTEGER,
    sector_lag_pct      REAL,
    sector_slope        REAL,
    candle_close_pct    REAL,
    gap_to_target       REAL,
    news_filter_fired   INTEGER DEFAULT 0,
    regime              TEXT,
    nifty_open_change   REAL,
    nifty_atr           REAL,
    vix                 REAL,
    time_bucket         TEXT,
    day_of_week         INTEGER,
    is_open_protection  INTEGER DEFAULT 0,
    is_gap_day          INTEGER DEFAULT 0,
    signal_density      INTEGER,
    direction           TEXT,
    limit_price         REAL,
    confidence          REAL DEFAULT 1.0,
    sl_price            REAL,
    gate_trace          TEXT,
    entry_reason        TEXT,
    risk_reason         TEXT,
    cost_breakdown      TEXT,
    outcome_pnl         REAL,
    exit_reason         TEXT,
    hold_minutes        REAL,
    mfe_5m              REAL,
    mfe_10m             REAL,
    mfe_20m             REAL,
    mae_5m              REAL,
    mae_10m             REAL,
    slippage_pct        REAL,
    order_id            TEXT,
    created_at          TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sl_ticker   ON signal_log(ticker);
CREATE INDEX IF NOT EXISTS idx_sl_strategy ON signal_log(strategy_name);
CREATE INDEX IF NOT EXISTS idx_sl_ts       ON signal_log(timestamp);

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
    cost_brokerage  REAL,
    cost_stt        REAL,
    cost_exchange   REAL,
    cost_sebi       REAL,
    cost_stamp      REAL,
    cost_gst        REAL,
    cost_total      REAL,
    net_pnl         REAL,
    entry_narrative TEXT,
    exit_narrative  TEXT,
    order_id        TEXT,
    exit_order_id   TEXT,
    residual_id     TEXT,
    position_type   TEXT DEFAULT 'NORMAL',
    mfe_5m          REAL,
    mfe_10m         REAL,
    mfe_20m         REAL,
    mae_5m          REAL,
    mae_10m         REAL,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tl_ticker    ON trade_log(ticker);
CREATE INDEX IF NOT EXISTS idx_tl_ts        ON trade_log(entry_time);
CREATE INDEX IF NOT EXISTS idx_tl_order_id  ON trade_log(order_id);

CREATE TABLE IF NOT EXISTS shadow_log (
    shadow_id               TEXT PRIMARY KEY,
    strategy_name           TEXT NOT NULL,
    strategy_version        TEXT NOT NULL,
    signal_id               TEXT REFERENCES signal_log(signal_id),
    ticker                  TEXT NOT NULL,
    direction               TEXT,
    simulated_entry         REAL,
    simulated_exit          REAL,
    simulated_pnl           REAL,
    fill_simulated          INTEGER DEFAULT 0,
    fill_latency_candles    INTEGER,
    exit_reason             TEXT,
    hold_minutes            REAL,
    slippage_pct            REAL,
    created_at              TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_shd_strategy ON shadow_log(strategy_name);

CREATE TABLE IF NOT EXISTS positions (
    ticker          TEXT PRIMARY KEY,
    direction       TEXT NOT NULL,
    entry_price     REAL NOT NULL,
    qty             INTEGER NOT NULL,
    sl_price        REAL,
    target_price    REAL,
    entry_time      TEXT,
    order_id        TEXT,
    sector          TEXT,
    signal_id       TEXT,
    strategy_name   TEXT,
    exit_order_id   TEXT,
    residual_id     TEXT,
    position_type   TEXT DEFAULT 'NORMAL'
);

CREATE TABLE IF NOT EXISTS daily_dossier (
    date                    TEXT PRIMARY KEY,
    live_pnl                REAL,
    shadow_pnl              REAL,
    live_trade_count        INTEGER,
    shadow_trade_count      INTEGER,
    best_shadow_strategy    TEXT,
    best_shadow_pnl         REAL,
    regime_breakdown        TEXT,
    time_breakdown          TEXT,
    sector_breakdown        TEXT,
    notes                   TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);
"""

_DECISIONS_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_registry (
    model_id         TEXT PRIMARY KEY,
    model_name       TEXT NOT NULL,
    model_type       TEXT NOT NULL,
    model_file       TEXT,
    trained_at       TEXT,
    data_start       TEXT,
    data_end         TEXT,
    trade_count      INTEGER,
    validation_score REAL,
    win_rate         REAL,
    sharpe           REAL,
    status           TEXT DEFAULT 'CANDIDATE',
    pushed_at        TEXT,
    notes            TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deployment_decisions (
    decision_id          TEXT PRIMARY KEY,
    decision_date        TEXT NOT NULL,
    models_reviewed      TEXT,
    model_chosen         TEXT,
    models_rejected      TEXT,
    owner_reasoning      TEXT NOT NULL,
    live_strategy_before TEXT,
    live_strategy_after  TEXT,
    performance_30d      REAL,
    performance_60d      REAL,
    created_at           TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_notes (
    session_id              TEXT PRIMARY KEY,
    session_date            TEXT NOT NULL,
    data_range_start        TEXT,
    data_range_end          TEXT,
    total_trades_analysed   INTEGER,
    key_findings            TEXT,
    action_taken            TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
);
"""


# ===================================================================
# CONNECTION HELPERS
# ===================================================================

def _connect(path):
    conn = sqlite3.connect(path, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn



def init_live_db():
    conn = _connect(DB_LIVE_PATH)
    conn.executescript(_LIVE_SCHEMA)
    conn.commit()
    conn.close()
    migrate_live_db()   # v8: apply column migrations on every startup



def migrate_live_db():
    """
    Safe schema migration — adds new columns to existing DB without destroying data.
    Called at startup after init_live_db(). Idempotent — safe to call every run.
    SQLite does not support DROP COLUMN so old columns are always preserved.
    """
    conn = _connect(DB_LIVE_PATH)
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(positions)").fetchall()}
        migrations = [
            ("exit_order_id", "TEXT"),
            ("residual_id",   "TEXT"),
            ("position_type", "TEXT DEFAULT 'LIVE'"),
        ]
        for col, col_type in migrations:
            if col not in existing:
                conn.execute(f"ALTER TABLE positions ADD COLUMN {col} {col_type}")
                conn.commit()
    except Exception as e:
        import logging; logging.getLogger("vault").warning("migrate_live_db: %s", e)
    finally:
        conn.close()


def migrate_live_db():
    """
    Safe schema migration — adds ANY missing columns to existing tables.
    Uses ALTER TABLE ADD COLUMN which is non-destructive (never drops data).
    Called from init_live_db() so it runs on EVERY import of vault,
    including during validate.py test runs — not just engine startup.
    Idempotent: safe to call multiple times.
    """
    migrations = [
        # positions table — columns added over v4-v8 lifecycle
        ("positions", "order_id",        "TEXT"),
        ("positions", "signal_id",       "TEXT"),
        ("positions", "strategy_name",   "TEXT"),
        ("positions", "sector",          "TEXT"),
        ("positions", "target_price",    "REAL"),
        ("positions", "sl_price",        "REAL"),
        ("positions", "exit_order_id",   "TEXT"),
        ("positions", "residual_id",     "TEXT"),
        ("positions", "position_type",   "TEXT DEFAULT 'LIVE'"),
        # trade_log — v8 MFE/MAE columns
        ("trade_log", "mfe_5m",  "REAL"),
        ("trade_log", "mfe_10m", "REAL"),
        ("trade_log", "mfe_20m", "REAL"),
        ("trade_log", "mae_5m",  "REAL"),
        ("trade_log", "mae_10m", "REAL"),
        # signal_log — v7/v8 additions
        ("signal_log", "atr_15m",    "REAL"),
        ("signal_log", "risk_reason","TEXT"),
        ("signal_log", "gate_trace", "TEXT"),
    ]
    try:
        conn = _connect(DB_LIVE_PATH)
        for table, col, col_type in migrations:
            try:
                existing = {r[1] for r in
                            conn.execute(f"PRAGMA table_info({table})").fetchall()}
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE {table} ADD COLUMN {col} {col_type}"
                    )
                    conn.commit()
            except Exception:
                pass   # Table may not exist yet — init_live_db will create it
        conn.close()
    except Exception as e:
        import logging
        logging.getLogger("vault").debug("migrate_live_db: %s", e)


def init_decisions_db():
    os.makedirs(os.path.dirname(DB_DECISIONS_PATH), exist_ok=True)
    conn = _connect(DB_DECISIONS_PATH)
    conn.executescript(_DECISIONS_SCHEMA)
    conn.commit()
    conn.close()


def get_live_conn():
    return _connect(DB_LIVE_PATH)


def get_decisions_conn():
    return _connect(DB_DECISIONS_PATH)


def get_archive_conn():
    os.makedirs(os.path.dirname(DB_ARCHIVE_PATH), exist_ok=True)
    return _connect(DB_ARCHIVE_PATH)


# ===================================================================
# WRITE OPERATIONS
# ===================================================================

def write_signal(signal: dict):
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
    sets = ", ".join(f"{k}=?" for k in outcome.keys())
    sql  = f"UPDATE signal_log SET {sets} WHERE signal_id=?"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(outcome.values()) + [signal_id])
        conn.commit()
    finally:
        conn.close()


def write_trade(trade: dict):
    cols = list(trade.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO trade_log ({','.join(cols)}) VALUES ({placeholders})"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(trade.values()))
        conn.commit()
    finally:
        conn.close()


def write_shadow(shadow: dict):
    cols = list(shadow.keys())
    placeholders = ",".join(["?"] * len(cols))
    sql = f"INSERT OR REPLACE INTO shadow_log ({','.join(cols)}) VALUES ({placeholders})"
    conn = get_live_conn()
    try:
        conn.execute(sql, list(shadow.values()))
        conn.commit()
    finally:
        conn.close()


def upsert_position(position: dict):
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
    conn = get_live_conn()
    try:
        conn.execute("DELETE FROM positions WHERE ticker=?", (ticker,))
        conn.commit()
    finally:
        conn.close()


def load_open_positions() -> list:
    conn = get_live_conn()
    try:
        rows = conn.execute("SELECT * FROM positions").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def write_candles_bulk(candles: list, ticker: str, timeframe: str):
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
    conn = get_live_conn()
    try:
        cursor = conn.execute(
            "DELETE FROM historical_candles WHERE time < ?", (cutoff_ts,)
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def archive_signal_block(cutoff_ts_str: str) -> int:
    live = get_live_conn()
    arch = get_archive_conn()
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
        live.execute("DELETE FROM signal_log WHERE timestamp < ?", (cutoff_ts_str,))
        live.commit()
        return len(rows)
    finally:
        live.close()
        arch.close()


def register_model(model: dict):
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
    reasoning = decision.get("owner_reasoning", "")
    if len(reasoning.strip()) < 20:
        raise ValueError("owner_reasoning must be at least 20 characters.")
    init_decisions_db()
    cols = list(decision.keys())
    sql = f"INSERT OR REPLACE INTO deployment_decisions ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})"
    conn = get_decisions_conn()
    try:
        conn.execute(sql, list(decision.values()))
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    print("[vault] Initialising all databases...")
    init_live_db()
    init_decisions_db()
    print(f"[vault] Live DB:      {DB_LIVE_PATH}")
    print(f"[vault] Migrations applied.")
    print("[vault] Done. ✅")
