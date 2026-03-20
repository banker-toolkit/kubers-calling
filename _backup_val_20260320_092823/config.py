# -*- coding: utf-8 -*-
"""
KUBER'S CALLING — config.py
============================
Single source of truth for every parameter in the system.

v8 changes:
  - BROKERAGE_PER_ORDER_RS corrected to 5.0 (IndMoney flat rate per leg)
  - GST now calculated on brokerage only (per IndMoney spec)
  - NO_NEW_ENTRIES_CUTOFF moved to 14:20
  - FORCE_CLOSE_TIME = 15:10 (Kubers proactive squareoff before IndMoney 15:20)
  - WS_ORDER_UPDATES_URL added
  - ENTRY_CANCEL_AFTER_FIRST_FILL added
  - RECONCILIATION_INTERVAL_SEC added
"""

import os
_BASE = os.path.dirname(os.path.abspath(__file__))

# ===================================================================
# GROUP 1 — PATHS AND CREDENTIALS
# ===================================================================
CREDS_FILE           = os.path.join(_BASE, "investright_creds.json")
TOKEN_KEY            = "jwt_token"
DB_LIVE_PATH         = os.path.join(_BASE, "database", "kubers_live.db")
DB_ARCHIVE_PATH      = os.path.join(_BASE, "database", "kubers_archive.db")
DB_DECISIONS_PATH    = os.path.join(_BASE, "database", "kubers_decisions.db")
MODEL_STORE_PATH     = os.path.join(_BASE, "model_registry", "models")
DB_DIR               = os.path.join(_BASE, "database")
DB_NAME              = "kubers_live.db"
DB_PATH              = DB_LIVE_PATH

# ===================================================================
# GROUP 2 — API
# ===================================================================
INDMONEY_BASE_URL      = "https://api.indstocks.com"
ZERODHA_INSTRUMENTS    = "https://api.kite.trade/instruments"
TOKEN_MAP_MIN_EXPECTED = 1000
SCRIP_NIFTY50          = "NIDX_40000001"
SCRIP_INDIA_VIX        = "NIDX_40000107"

# WebSocket — real-time order updates (primary fill confirmation channel)
WS_ORDER_UPDATES_URL   = "wss://ws-order-updates.indstocks.com"

# ===================================================================
# GROUP 3 — DATA AND TIMING
# ===================================================================
SCAN_INTERVAL_SEC        = 2.5
CANDLE_3M_SEC            = 180
CANDLE_15M_SEC           = 900
LIVE_DB_RETENTION_DAYS   = 60
HISTORY_DAYS_FOR_PROFILE = 30
DOSSIER_TIME             = "16:30"
SESSION_START            = "09:15"

# ===================================================================
# GROUP 3B — SESSION WIND-DOWN WINDOW (Principle 1.13)
# Two cutoffs enforce a defined wind-down, not a single hard stop.
# 14:20 — no new entry signals accepted (toggle-controllable)
# 15:10 — all open positions force-closed by Kubers (before IndMoney 15:20)
# ===================================================================
NO_NEW_ENTRIES_CUTOFF  = "14:20"   # v8: moved from 14:30
FORCE_CLOSE_TIME       = "15:10"   # v8: Kubers proactive squareoff

# Reconciliation heartbeat — position count check vs IndMoney
# (Principle 1.11 makes this rarely needed, but covers manual trades / GTT)
RECONCILIATION_INTERVAL_SEC = 300  # every 5 minutes

# ===================================================================
# GROUP 4 — UNIVERSE FILTERS
# ===================================================================
MIN_ADV_CRORE        = 50.0
MIN_ADV_CRORE_FLOOR  = 10.0
MIN_DAILY_VOLUME     = 500000
MIN_MARKET_CAP_CRORE = 1000.0

# ===================================================================
# GROUP 5 — SCOUT FILTER
# ===================================================================
VOL_Z_SCORE_TRIGGER  = 2.0
SCOUT_K_MULTIPLIER   = 1.0
MIN_CANDLES_3M       = 5
MIN_CANDLES_15M      = 5
MAX_Z_SCORE          = 999.0
HIGH_CONVICTION_Z    = 3.0
VOL_Z_SCORE_PERIOD   = 20
SCOUT_ATR_PERIOD     = 10
MIN_ATR_PCT          = 0.003
HIGH_VOL_EVENT_Z     = HIGH_CONVICTION_Z

# ===================================================================
# GROUP 6 — SPY DIRECTION CHECK
# ===================================================================
LAG_THRESHOLD_PCT       = 0.2
CANDLE_TOP_PCT          = 0.75
CANDLE_BOTTOM_PCT       = 0.25
SECTOR_SLOPE_PERIOD     = 10
MIN_SECTOR_SLOPE_LONG   = 0.0
MAX_SECTOR_SLOPE_SHORT  = 0.0
GAP_VIABILITY_ATR_MULT  = 0.5
TARGET_LAG_MULT         = 1.5

# ===================================================================
# GROUP 7 — NEWS FILTER
# ===================================================================
NEWS_FILTER_ABSOLUTE_DROP    = 1.5
NEWS_FILTER_SECTOR_MOVE_MIN  = 0.3

# ===================================================================
# GROUP 8 — MARKET REGIME
# ===================================================================
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
MIN_VIX                      = VIX_FLOOR_ABSOLUTE
MAX_VIX                      = VIX_CEILING_ABSOLUTE
MIN_NIFTY_ATR_15M            = NIFTY_ATR_FLOOR_ABSOLUTE
DYNAMIC_VIX_LOW_MULT         = DYNAMIC_VIX_MULT
DYNAMIC_ATR_LOW_MULT         = DYNAMIC_ATR_MULT
NIFTY_DIRECTION_THRESHOLD_1  = NIFTY_DIRECTIONAL_THRESHOLD
NIFTY_DIRECTION_THRESHOLD_2  = NIFTY_HARD_DIRECTIONAL_THRESHOLD

# ===================================================================
# GROUP 9 — OPEN PROTECTION AND GAP DAYS
# ===================================================================
OPEN_PROTECTION_END      = "09:30"
OPEN_Z_MULTIPLIER        = 1.5
OPEN_POSITION_SIZE_PCT   = 0.5
GAP_DAY_NIFTY_THRESHOLD  = 0.01
GAP_DAY_PROTECTION_END   = "10:00"
OPEN_NO_SHORTS           = True
OPEN_PROTECTION_MINUTES  = 15
OPEN_Z_SCORE_MULTIPLIER  = OPEN_Z_MULTIPLIER

# ===================================================================
# GROUP 10 — RISK AND CAPITAL
# ===================================================================
DEFAULT_GLOBAL_LIMIT     = 100000
DEFAULT_PER_STOCK_LIMIT  = 25000
DEFAULT_EQUITY_FLOOR     = 95000
STARTING_EQUITY          = 100000
MAX_SECTOR_POSITIONS     = 1
MAX_OPEN_POSITIONS       = 5
SLOT_SIZE                = 20000
MAX_LONGS_IN_DOWN_MARKET = 2
MAX_SHORTS_IN_UP_MARKET  = 2
MIN_ORDER_VALUE          = 15000
MAX_ENTRY_PRICE          = 8000
MAX_ORDER_VALUE          = 45000
SL_COOLDOWN_MIN          = 30

# Entry order: cancel unfilled residual after first fill confirmation
# Prevents IndMoney drip-filling hours later creating ghost positions
ENTRY_CANCEL_AFTER_FIRST_FILL = True

# ===================================================================
# GROUP 11 — VELOCITY CAP
# ===================================================================
ENABLE_VELOCITY_CAP      = False
VELOCITY_CAP_MAX_ENTRIES = 3
VELOCITY_CAP_WINDOW_SEC  = 300

# ===================================================================
# GROUP 12 — EXECUTION
# ===================================================================
LIMIT_ORDER_OFFSET_PCT   = 0.001
ORDER_TTL_CANDLES        = 1
SL_ATR_MULTIPLIER        = 1.0
ENABLE_PARTIAL_FILL_CHECK= True
EXIT_ORDER_TTL_CANDLES   = 3
EXIT_MARKET_ESCALATE     = True
ATR_STOP_MULTIPLIER      = SL_ATR_MULTIPLIER

# ===================================================================
# GROUP 13 — EXITS
# ===================================================================
TIME_STOP_CHECK_MIN              = 20
TIME_STOP_PROGRESS_THRESHOLD     = 0.30
TIME_STOP_EXTENSION_THRESHOLD    = 0.50
TIME_STOP_EXTENSION_MIN          = 10
TIME_STOP_HARD_MIN               = 30
EOD_SQUAREOFF_TIME               = "15:15"
CONTRARIAN_THRESHOLD_PCT         = 0.003
CONTRARIAN_ATR_MULT              = 0.5
TIME_STOP_MIN_PROGRESS           = TIME_STOP_PROGRESS_THRESHOLD
TIME_STOP_EXTENSION_PCT          = TIME_STOP_EXTENSION_THRESHOLD

# Trailing profit stop (TARGET_PLUS)
TRAILING_PROFIT_PCT   = 0.10
ENABLE_TRAILING_PROFIT = True

# ===================================================================
# GROUP 14 — SHADOW BOOK SLIPPAGE SIMULATION
# ===================================================================
SLIPPAGE_FLOOR_NORMAL  = 0.0003
SLIPPAGE_MID_NORMAL    = 0.0008
SLIPPAGE_LARGE_NORMAL  = 0.0015
SLIPPAGE_FLOOR_EVENT   = 0.0008
SLIPPAGE_MID_EVENT     = 0.0015
SLIPPAGE_LARGE_EVENT   = 0.0025
OUTCOME_HORIZONS       = [5, 10, 20, 30]

# ===================================================================
# GROUP 15 — FEATURE FLAGS
# ===================================================================
ENABLE_MIDDAY_BLACKOUT   = False
MIDDAY_BLACKOUT_START    = "11:30"
MIDDAY_BLACKOUT_END      = "13:15"
ENABLE_GAP_DAY_EXTENSION = True
ENABLE_ML_STRATEGY       = False

# ===================================================================
# GROUP 16 — AUDITOR AND ANALYTICS
# ===================================================================
TTEST_BLACKOUT_DAYS  = 60
TTEST_BASELINE_MIN   = 200
TTEST_RECENT_WINDOW  = 30
ML_DEPLOY_MIN_DAYS   = 60

# ===================================================================
# GROUP 17 — TRANSACTION COSTS (IndMoney Intraday, NSE)
# ===================================================================
# v8 CORRECTION: IndMoney charges a FLAT ₹5 per order for API users.
# Previous code used min(₹20, 0.05%) which was wrong.
# Confirmed from IndMoney margin API response: brokerage=5 per order.
#
# Cost per round trip on any position size:
#   Brokerage  : ₹5 entry + ₹5 exit           = ₹10.00
#   GST 18%    : on brokerage only (₹10 × 18%) =  ₹1.80
#   STT        : sell-side turnover × 0.025%   (varies by price/qty)
#   Exchange   : both-side turnover × 0.00297% (varies)
#   SEBI       : both-side turnover × 0.0001%  (varies)
#   Stamp duty : buy-side turnover × 0.003%    (varies)
#
# Pre-v8 code used GST on (brokerage + exchange + SEBI) — incorrect per spec.
# IndMoney spec shows GST only on brokerage.

BROKERAGE_PER_ORDER_RS   = 5.0        # v8: ₹5 flat per order/leg (was 20.0)
# BROKERAGE_MAX_PCT removed — no longer percentage-based
STT_SELL_PCT             = 0.00025    # 0.025% sell side only
EXCHANGE_CHARGE_PCT      = 0.0000297  # 0.00297% NSE both sides
SEBI_LEVY_PCT            = 0.000001   # both sides
STAMP_DUTY_PCT           = 0.00003    # 0.003% buy side only
GST_PCT                  = 0.18       # 18% on brokerage ONLY (per IndMoney spec)
DP_CHARGES_RS            = 0.0        # intraday: no demat debit


def compute_trade_cost(entry_price: float, exit_price: float,
                       qty: int, side_entry: str = "BUY") -> dict:
    """
    Compute full statutory + brokerage cost for one intraday round-trip.

    v8 corrections:
      - Brokerage: flat ₹5 per order leg (not min(₹20, 0.05%))
      - GST: 18% on brokerage only (not brokerage+exchange+SEBI)

    Returns dict with itemised breakdown and total.
    """
    entry_turn = entry_price * qty
    exit_turn  = exit_price  * qty

    # Brokerage: ₹5 flat per leg — confirmed IndMoney API rate
    brokerage = round(BROKERAGE_PER_ORDER_RS * 2, 4)   # entry + exit legs

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

    # GST 18% on brokerage ONLY (per IndMoney spec — not on exchange/SEBI)
    gst       = round(brokerage * GST_PCT, 4)

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
