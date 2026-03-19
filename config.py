# -*- coding: utf-8 -*-
"""
KUBER'S CALLING — config.py
============================
Single source of truth for every parameter in the system.

RULES (enforced by Validation Agent):
  1. Every threshold, constant, flag, and path lives here.
  2. No numeric literals anywhere else except loop indices (0, 1, 2, -1).
  3. Every value is a starting hypothesis, not a permanent truth.
  4. No logic here — no functions, classes, or conditionals.
"""

import os
_BASE = os.path.dirname(os.path.abspath(__file__))

# ═══════════════════════════════════════════════════════════════════
# GROUP 1 — PATHS AND CREDENTIALS
# ═══════════════════════════════════════════════════════════════════
CREDS_FILE           = os.path.join(_BASE, "investright_creds.json")
TOKEN_KEY            = "jwt_token"
DB_LIVE_PATH         = os.path.join(_BASE, "database", "kubers_live.db")
DB_ARCHIVE_PATH      = os.path.join(_BASE, "database", "kubers_archive.db")
DB_DECISIONS_PATH    = os.path.join(_BASE, "database", "kubers_decisions.db")
MODEL_STORE_PATH     = os.path.join(_BASE, "model_registry", "models")
# Backward-compatible aliases
DB_DIR               = os.path.join(_BASE, "database")
DB_NAME              = "kubers_live.db"
DB_PATH              = DB_LIVE_PATH

# ═══════════════════════════════════════════════════════════════════
# GROUP 2 — API
# ═══════════════════════════════════════════════════════════════════
INDMONEY_BASE_URL    = "https://api.indstocks.com"
ZERODHA_INSTRUMENTS  = "https://api.kite.trade/instruments"
TOKEN_MAP_MIN_EXPECTED = 1000      # Minimum tokens expected from Zerodha dump
SCRIP_NIFTY50        = "NIDX_40000001"
SCRIP_INDIA_VIX      = "NIDX_40000107"

# ═══════════════════════════════════════════════════════════════════
# GROUP 3 — DATA AND TIMING
# ═══════════════════════════════════════════════════════════════════
SCAN_INTERVAL_SEC        = 2.5
CANDLE_3M_SEC            = 180
CANDLE_15M_SEC           = 900
LIVE_DB_RETENTION_DAYS   = 60
HISTORY_DAYS_FOR_PROFILE = 30
DOSSIER_TIME             = "16:30"
SESSION_START            = "09:15"   # Market open — used as lower bound for open protection window

# ═══════════════════════════════════════════════════════════════════
# GROUP 4 — UNIVERSE FILTERS
# ═══════════════════════════════════════════════════════════════════
MIN_ADV_CRORE        = 50.0
MIN_ADV_CRORE_FLOOR  = 10.0
MIN_DAILY_VOLUME     = 500000
MIN_MARKET_CAP_CRORE = 1000.0

# ═══════════════════════════════════════════════════════════════════
# GROUP 5 — SCOUT FILTER
# ═══════════════════════════════════════════════════════════════════
VOL_Z_SCORE_TRIGGER  = 2.0
SCOUT_K_MULTIPLIER   = 1.0
MIN_CANDLES_3M       = 5
MIN_CANDLES_15M      = 5
MAX_Z_SCORE          = 999.0  # v8: cap removed — real Z flows through. Shadow book calibration requires true values.
HIGH_CONVICTION_Z    = 3.0
VOL_Z_SCORE_PERIOD   = 20
SCOUT_ATR_PERIOD     = 10
# Minimum ATR/price ratio for a viable trade.
# Below this threshold the stock has insufficient range to cover transaction costs.
# 37 of 38 flash closes on Day 1 were tickers with ATR <= ₹0.8 (ratio ~0.05%).
# At 0.3%, a full ATR move covers costs on a ₹15K position with margin.
MIN_ATR_PCT          = 0.003   # 0.3% — atr_15m / price must exceed this
# Backward-compatible alias
HIGH_VOL_EVENT_Z     = HIGH_CONVICTION_Z

# ═══════════════════════════════════════════════════════════════════
# GROUP 6 — SPY DIRECTION CHECK
# ═══════════════════════════════════════════════════════════════════
LAG_THRESHOLD_PCT       = 0.2
CANDLE_TOP_PCT          = 0.75
CANDLE_BOTTOM_PCT       = 0.25
SECTOR_SLOPE_PERIOD     = 10
MIN_SECTOR_SLOPE_LONG   = 0.0
MAX_SECTOR_SLOPE_SHORT  = 0.0
GAP_VIABILITY_ATR_MULT  = 0.5
# v8.2: Target multiplier — how many times the sector lag to use as target.
# 1.0 = exit exactly when institutional gap closes (current behaviour).
# 2.0 = hold for twice the lag move — captures hyena momentum after institution.
# Evidence: TARGET exits avg ₹58 gross in 9-17m. Hyenas typically push another
# 50-100% of the original move in the following 5-10m before reverting.
# Start at 1.5 — not greedy, captures roughly half the follow-through.
TARGET_LAG_MULT         = 1.5

# ═══════════════════════════════════════════════════════════════════
# GROUP 7 — NEWS FILTER
# ═══════════════════════════════════════════════════════════════════
NEWS_FILTER_ABSOLUTE_DROP    = 1.5
NEWS_FILTER_SECTOR_MOVE_MIN  = 0.3

# ═══════════════════════════════════════════════════════════════════
# GROUP 8 — MARKET REGIME
# ═══════════════════════════════════════════════════════════════════
VIX_FLOOR_ABSOLUTE           = 11.0
VIX_CEILING_ABSOLUTE         = 28.0
NIFTY_ATR_FLOOR_ABSOLUTE     = 15.0
DYNAMIC_REGIME_WINDOW        = 20
DYNAMIC_VIX_MULT             = 0.65
DYNAMIC_ATR_MULT             = 0.60
NIFTY_VOLUME_MULT            = 0.60
NIFTY_DIRECTIONAL_THRESHOLD  = 0.008
NIFTY_HARD_DIRECTIONAL_THRESHOLD = 0.015
REGIME_VOL_PERIOD            = 20
# Backward-compatible aliases
MIN_VIX                      = VIX_FLOOR_ABSOLUTE
MAX_VIX                      = VIX_CEILING_ABSOLUTE
MIN_NIFTY_ATR_15M            = NIFTY_ATR_FLOOR_ABSOLUTE
DYNAMIC_VIX_LOW_MULT         = DYNAMIC_VIX_MULT
DYNAMIC_ATR_LOW_MULT         = DYNAMIC_ATR_MULT
NIFTY_DIRECTION_THRESHOLD_1  = NIFTY_DIRECTIONAL_THRESHOLD
NIFTY_DIRECTION_THRESHOLD_2  = NIFTY_HARD_DIRECTIONAL_THRESHOLD

# ═══════════════════════════════════════════════════════════════════
# GROUP 9 — OPEN PROTECTION AND GAP DAYS
# ═══════════════════════════════════════════════════════════════════
OPEN_PROTECTION_END      = "09:30"
OPEN_Z_MULTIPLIER        = 1.5
OPEN_POSITION_SIZE_PCT   = 0.5
GAP_DAY_NIFTY_THRESHOLD  = 0.01
GAP_DAY_PROTECTION_END   = "10:00"
# Backward-compatible aliases
OPEN_NO_SHORTS           = True
OPEN_PROTECTION_MINUTES  = 15
OPEN_Z_SCORE_MULTIPLIER  = OPEN_Z_MULTIPLIER

# ═══════════════════════════════════════════════════════════════════
# GROUP 10 — RISK AND CAPITAL
# ═══════════════════════════════════════════════════════════════════
DEFAULT_GLOBAL_LIMIT     = 100000
DEFAULT_EQUITY_FLOOR     = 95000
STARTING_EQUITY          = 100000
MAX_LONGS_IN_DOWN_MARKET = 2
MAX_SHORTS_IN_UP_MARKET  = 2

# ── 5-Slot position model (v9) ───────────────────────────────────────
# Replace old per-stock-limit + sector-cap with a fixed slot system:
# exactly MAX_OPEN_POSITIONS slots, each sized SLOT_SIZE.
# New entry only considered when a slot is free.
# Within open slots: max 1 position per sector (MAX_SECTOR_POSITIONS).
# Backtest Mar 11-18: actual -9272 -> simulated -2038 (+7234 improvement).
MAX_OPEN_POSITIONS   = 5       # total concurrent positions allowed
SLOT_SIZE            = 20000   # capital per slot (100K / 5)
MAX_SECTOR_POSITIONS = 1       # max 1 per sector within the 5 slots
DEFAULT_PER_STOCK_LIMIT = SLOT_SIZE   # backward-compat alias

# ── Trailing profit protection (v9) ─────────────────────────────────
# Once a position hits its target (TARGET+), instead of closing immediately:
#   1. Move SL to entry price (breakeven — can't lose now)
#   2. Track peak profit seen
#   3. Exit only if profit drops more than TRAILING_PROFIT_PCT below peak
# Example: target hit at +197, peak reaches +280, exit at +252 (10% below peak)
# This lets strong institutional moves run while locking in gains.
TRAILING_PROFIT_PCT    = 0.10   # 10% erosion from peak profit triggers exit
ENABLE_TRAILING_PROFIT = True   # set False to revert to immediate target exit
# Minimum notional order value (qty × price). Orders below this are rejected.
# At ₹15K, round-trip cost ~₹22 = 0.15% — achievable on a 0.3% move.
# Below ₹15K costs consume too large a fraction of potential profit.
MIN_ORDER_VALUE          = 15000
# Maximum stock price for entry. Stocks above this threshold have too few
# shares per ₹15K position to exit reliably — thin order books cause
# FORCE_BOOKED exits. Evidence: SHREECEM ₹23,955 (1 share) TIME_STOP_CHECK_FORCE_BOOKED.
# ULTRACEMCO ₹11,277 (2 shares) same risk profile. ₹8,000 keeps JKCEMENT (3 shares,
# better liquidity) while blocking the truly illiquid high-price stocks.
MAX_ENTRY_PRICE          = 8000   # no entries on stocks priced above this
MAX_ORDER_VALUE = 45000   # add this line
# SL cooldown — block re-entry on the same ticker for this many minutes after SL hit.
# Each SL hit confirms institutional activity is complete or reversed on that ticker.
# Re-entering is fighting the tape. Day 1: NOCIL hit SL 6× losing ₹135 on same ticker.
SL_COOLDOWN_MIN          = 30

# ═══════════════════════════════════════════════════════════════════
# GROUP 11 — VELOCITY CAP
# ═══════════════════════════════════════════════════════════════════
ENABLE_VELOCITY_CAP      = False
VELOCITY_CAP_MAX_ENTRIES = 3
VELOCITY_CAP_WINDOW_SEC  = 300

# ═══════════════════════════════════════════════════════════════════
# GROUP 12 — EXECUTION
# ═══════════════════════════════════════════════════════════════════
LIMIT_ORDER_OFFSET_PCT   = 0.001
ORDER_TTL_CANDLES        = 1
SL_ATR_MULTIPLIER        = 1.0
ENABLE_PARTIAL_FILL_CHECK= True
# Exit order polling — how long to attempt limit exit before escalating to market
EXIT_ORDER_TTL_CANDLES   = 3      # 3 candles (9 min) at limit before escalating
EXIT_MARKET_ESCALATE     = True   # if True, re-issue as market order after TTL
# Backward-compatible alias
ATR_STOP_MULTIPLIER      = SL_ATR_MULTIPLIER

# ═══════════════════════════════════════════════════════════════════
# GROUP 13 — EXITS
# TIME_STOP_HARD_MIN and EOD_SQUAREOFF_TIME are NOT dashboard-tunable.
# They are thesis-discipline constants.
# ═══════════════════════════════════════════════════════════════════
TIME_STOP_CHECK_MIN              = 20
TIME_STOP_PROGRESS_THRESHOLD     = 0.30    # legacy — no longer used for closure, kept for logging
TIME_STOP_EXTENSION_THRESHOLD    = 0.50
TIME_STOP_EXTENSION_MIN          = 10
TIME_STOP_HARD_MIN               = 30
EOD_SQUAREOFF_TIME               = "15:15"
NO_NEW_ENTRIES_CUTOFF            = "14:30"  # v8: moved from 15:10. Mar-12 and Mar-17 data show late entries are net negative. Existing positions run to their natural exit.
# Directional conviction exit — after TIME_STOP_CHECK_MIN, only close if price
# has moved more than this fraction AGAINST the trade. Below threshold = neutral = hold.
# v8.1: ATR-based contrarian exit replaces flat percentage threshold.
# A percentage threshold is broken across a universe spanning ₹157 (IGL) to ₹23,955 (SHREECEM):
#   0.15% on IGL   = ₹0.24 — literal tick noise, triggers constantly.
#   0.15% on SHREECEM = ₹35.93 — almost never triggers, position drifts for 30m.
# ATR-based: close if adverse move > CONTRARIAN_ATR_MULT × ATR(15m).
#   IGL (ATR=₹0.91):   closes if adverse > ₹0.46  — half a natural noise range.
#   SHREECEM (ATR=₹125): closes if adverse > ₹62.50 — same logic, correct scale.
# CONTRARIAN_THRESHOLD_PCT retained for backward-compat with validate.py REG tests.
# The live path in broker.py uses pos.atr_threshold (set from atr_15m at fill time).
CONTRARIAN_THRESHOLD_PCT         = 0.003   # kept for validate.py REG-checks only — not used in live path
CONTRARIAN_ATR_MULT              = 0.5     # close if adverse move > 0.5 × ATR(15m)
# Backward-compatible aliases
TIME_STOP_MIN_PROGRESS           = TIME_STOP_PROGRESS_THRESHOLD
TIME_STOP_EXTENSION_PCT          = TIME_STOP_EXTENSION_THRESHOLD

# ═══════════════════════════════════════════════════════════════════
# GROUP 14 — SHADOW BOOK SLIPPAGE SIMULATION
# ═══════════════════════════════════════════════════════════════════
SLIPPAGE_FLOOR_NORMAL  = 0.0003
SLIPPAGE_MID_NORMAL    = 0.0008
SLIPPAGE_LARGE_NORMAL  = 0.0015
SLIPPAGE_FLOOR_EVENT   = 0.0008
SLIPPAGE_MID_EVENT     = 0.0015
SLIPPAGE_LARGE_EVENT   = 0.0025
OUTCOME_HORIZONS       = [5, 10, 20, 30]

# ═══════════════════════════════════════════════════════════════════
# GROUP 15 — FEATURE FLAGS
# Enable only after reviewing shadow data for the relevant feature.
# ═══════════════════════════════════════════════════════════════════
ENABLE_MIDDAY_BLACKOUT   = False
MIDDAY_BLACKOUT_START    = "11:30"
MIDDAY_BLACKOUT_END      = "13:15"
ENABLE_GAP_DAY_EXTENSION = True
ENABLE_ML_STRATEGY       = False

# ═══════════════════════════════════════════════════════════════════
# GROUP 16 — AUDITOR AND ANALYTICS
# ═══════════════════════════════════════════════════════════════════
TTEST_BLACKOUT_DAYS  = 60
TTEST_BASELINE_MIN   = 200
TTEST_RECENT_WINDOW  = 30
ML_DEPLOY_MIN_DAYS   = 60

# ═══════════════════════════════════════════════════════════════════
# GROUP 17 — TRANSACTION COSTS (INDmoney Intraday, NSE)
# ═══════════════════════════════════════════════════════════════════
# Confirmed schedule — INDmoney intraday equity, NSE 2024.
# DP charges = ₹0 (does not apply to intraday — no demat debit).
# All costs deducted from gross P&L on every closed trade.
#
# Round-trip cost breakdown on ₹5,000 position (e.g. 3 shares @ ₹1,667):
#   Brokerage  : 0.05% × 2 legs        =  ₹5.00  (₹20 flat only kicks in above ₹40,000/order)
#   STT        : ₹5,000 × 0.025%       =  ₹1.25  (exit side only)
#   Exchange   : ₹10,000 × 0.00297%    =  ₹0.30  (both sides)
#   SEBI       : ₹10,000 × 0.0001%     =  ₹0.01
#   Stamp Duty : ₹5,000 × 0.003%       =  ₹0.15  (entry side only)
#   GST 18%    : on (5 + 0.30 + 0.01)  =  ₹0.96
#   DP Charges : ₹0 (intraday, n/a)
#   TOTAL                               ≈  ₹7.66 round-trip
#
# NOTE: ₹20 flat brokerage only applies when order notional > ₹40,000.
#       At DEFAULT_PER_STOCK_LIMIT = ₹5,000, brokerage is always 0.05% = ₹2.50/leg.
#
# Net P&L = Gross P&L − total_cost
# Minimum gross move needed to break even ≈ ₹7.66 on a ₹5,000 position = 0.153%

BROKERAGE_PER_ORDER_RS   = 20.0       # ₹20 flat or 0.05% whichever LOWER, per executed order
BROKERAGE_MAX_PCT        = 0.0005     # 0.05% — cap, applies to orders > ₹40,000 notional
STT_SELL_PCT             = 0.00025    # 0.025% on sell-side turnover only (intraday)
EXCHANGE_CHARGE_PCT      = 0.0000297  # 0.00297% NSE exchange txn charge, both sides
SEBI_LEVY_PCT            = 0.000001   # ₹10 per crore (0.0001%), both sides
STAMP_DUTY_PCT           = 0.00003    # 0.003% on buy-side turnover only
GST_PCT                  = 0.18       # 18% on (brokerage + exchange + SEBI)
DP_CHARGES_RS            = 0.0        # ₹0 — intraday, no demat debit occurs


def compute_trade_cost(entry_price: float, exit_price: float,
                       qty: int, side_entry: str = "BUY") -> dict:
    """
    Compute full statutory + brokerage cost for one intraday round-trip.

    Returns dict with itemised breakdown AND total:
      {
        'brokerage': float,   # entry + exit legs combined
        'stt': float,         # sell side only
        'exchange': float,    # both sides
        'sebi': float,        # both sides
        'stamp_duty': float,  # buy side only
        'gst': float,         # on brokerage + exchange + sebi
        'dp': float,          # always 0.0 for intraday
        'total': float,       # sum of all above
      }

    entry_price: avg fill price on entry order
    exit_price:  avg fill price on exit order
    qty:         number of shares (use actual filled qty)
    side_entry:  'BUY' for long trade, 'SELL' for short trade
    """
    entry_turn = entry_price * qty
    exit_turn  = exit_price  * qty

    # Brokerage: ₹20 flat or 0.05% — whichever is LOWER — per order, × 2
    b_entry   = min(BROKERAGE_PER_ORDER_RS, entry_turn * BROKERAGE_MAX_PCT)
    b_exit    = min(BROKERAGE_PER_ORDER_RS, exit_turn  * BROKERAGE_MAX_PCT)
    brokerage = round(b_entry + b_exit, 4)

    # STT — sell side only
    sell_turn = exit_turn if side_entry == "BUY" else entry_turn
    stt       = round(sell_turn * STT_SELL_PCT, 4)

    # Exchange charges — both sides
    exchange  = round((entry_turn + exit_turn) * EXCHANGE_CHARGE_PCT, 4)

    # SEBI levy — both sides
    sebi      = round((entry_turn + exit_turn) * SEBI_LEVY_PCT, 4)

    # Stamp duty — buy side only
    buy_turn  = entry_turn if side_entry == "BUY" else exit_turn
    stamp     = round(buy_turn * STAMP_DUTY_PCT, 4)

    # GST 18% on brokerage + exchange + SEBI
    gst       = round((brokerage + exchange + sebi) * GST_PCT, 4)

    # DP charges — zero for intraday
    dp        = DP_CHARGES_RS

    total     = round(brokerage + stt + exchange + sebi + stamp + gst + dp, 2)

    return {
        'brokerage':  brokerage,
        'stt':        stt,
        'exchange':   exchange,
        'sebi':       sebi,
        'stamp_duty': stamp,
        'gst':        gst,
        'dp':         dp,
        'total':      total,
    }


def compute_trade_cost_total(entry_price: float, exit_price: float,
                              qty: int, side_entry: str = "BUY") -> float:
    """Convenience wrapper — returns only the total cost float."""
    return compute_trade_cost(entry_price, exit_price, qty, side_entry)['total']