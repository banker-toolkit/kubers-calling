"""
dump_march17.py
===============
Dumps trade_log + signal_log for 2026-03-17 from kubers_live.db
into two CSV files for offline analysis.

Output files (same folder as this script):
  trade_log_2026-03-17.csv
  signal_log_2026-03-17.csv

Usage:
  python dump_march17.py
"""

import sqlite3
import csv
import os
import sys
from datetime import datetime

# ── locate the DB ───────────────────────────────────────────────────────────
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(SCRIPT_DIR, "database", "kubers_live.db")
TARGET_DATE = "2026-03-17"

if not os.path.exists(DB_PATH):
    print(f"[ERROR] DB not found at: {DB_PATH}")
    print("        Make sure you run this from the Kubers project root.")
    sys.exit(1)


def dump_table(conn, query, params, outpath, label):
    cur = conn.cursor()
    cur.execute(query, params)
    rows    = cur.fetchall()
    headers = [d[0] for d in cur.description]

    with open(outpath, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        writer.writerows(rows)

    print(f"[OK] {label}: {len(rows)} rows  →  {outpath}")
    return rows, headers


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    out_trades  = os.path.join(SCRIPT_DIR, f"trade_log_{TARGET_DATE}.csv")
    out_signals = os.path.join(SCRIPT_DIR, f"signal_log_{TARGET_DATE}.csv")

    # ── trade_log ─────────────────────────────────────────────────────────
    dump_table(
        conn,
        """
        SELECT
            trade_id, signal_id, ticker, direction,
            entry_price, exit_price, qty,
            entry_time, exit_time, hold_minutes,
            exit_reason,
            gross_pnl, slippage_rs,
            cost_brokerage, cost_stt, cost_exchange,
            cost_sebi, cost_stamp, cost_gst, cost_total,
            net_pnl,
            entry_narrative, exit_narrative,
            mfe_5m, mfe_10m, mfe_20m,
            mae_5m, mae_10m,
            created_at
        FROM trade_log
        WHERE DATE(entry_time) = ?
        ORDER BY entry_time
        """,
        (TARGET_DATE,),
        out_trades,
        "trade_log"
    )

    # ── signal_log ────────────────────────────────────────────────────────
    dump_table(
        conn,
        """
        SELECT *
        FROM signal_log
        WHERE DATE(timestamp) = ?
        ORDER BY timestamp
        """,
        (TARGET_DATE,),
        out_signals,
        "signal_log"
    )

    # ── quick sanity summary ───────────────────────────────────────────────
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*)                                          AS total_trades,
            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END)    AS winners,
            ROUND(SUM(gross_pnl), 2)                         AS gross_pnl,
            ROUND(SUM(cost_total), 2)                        AS total_costs,
            ROUND(SUM(net_pnl), 2)                           AS net_pnl
        FROM trade_log
        WHERE DATE(entry_time) = ?
    """, (TARGET_DATE,))
    t = cur.fetchone()

    cur.execute("""
        SELECT disposition, COUNT(*) AS cnt
        FROM signal_log
        WHERE DATE(timestamp) = ?
        GROUP BY disposition
    """, (TARGET_DATE,))
    sigs = cur.fetchall()

    cur.execute("""
        SELECT exit_reason, COUNT(*) AS cnt, ROUND(SUM(net_pnl), 2) AS pnl
        FROM trade_log
        WHERE DATE(entry_time) = ?
        GROUP BY exit_reason
        ORDER BY pnl
    """, (TARGET_DATE,))
    exits = cur.fetchall()

    # ── NOCIL specifically ─────────────────────────────────────────────────
    cur.execute("""
        SELECT ticker, entry_time, exit_time, direction,
               entry_price, exit_price, qty,
               exit_reason, gross_pnl, cost_total, net_pnl
        FROM trade_log
        WHERE DATE(entry_time) = ? AND ticker = 'NOCIL'
        ORDER BY entry_time
    """, (TARGET_DATE,))
    nocil = cur.fetchall()

    conn.close()

    print()
    print("=" * 55)
    print(f"  KUBERS SUMMARY — {TARGET_DATE}")
    print("=" * 55)
    if t and t[0]:
        wr = round(t[1] / t[0] * 100, 1) if t[0] else 0
        print(f"  Trades      : {t[0]}  (winners: {t[1]}, win rate: {wr}%)")
        print(f"  Gross P&L   : ₹{t[2]}")
        print(f"  Total costs : ₹{t[3]}")
        print(f"  Net P&L     : ₹{t[4]}")
    else:
        print("  No trades found for this date.")

    print()
    print("  Signals:")
    for s in sigs:
        print(f"    {s[0]:<20} {s[1]}")

    print()
    print("  Exit reasons:")
    for e in exits:
        print(f"    {e[0]:<25} {e[1]:>3} trades   ₹{e[2]}")

    if nocil:
        print()
        print("  NOCIL trades (SL cooldown check):")
        for n in nocil:
            print(f"    {n[1][:16]}  {n[3]:<5}  {n[7]:<22}  net ₹{n[10]}")
    else:
        print()
        print("  NOCIL: no trades on this date.")

    print()
    print("  Done.")


if __name__ == "__main__":
    main()