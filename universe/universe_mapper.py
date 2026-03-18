"""
KUBER'S CALLING — universe/universe_mapper.py
==============================================
NSE 500 universe with sector classification and ADV tiers.

Returns the full trading universe as a list of dicts.
Sectors are used for ex-self composite and concentration limits.
ADV tiers are used for slippage simulation and position sizing.
"""

import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── NSE 500 trading universe with sector tags ────────────────────────
# Format: (ticker, sector, adv_tier) — 'HIGH' | 'MID' | 'LOW'
_UNIVERSE = [
    # FINANCIALS
    ("HDFCBANK",   "FINANCIALS", "HIGH"), ("ICICIBANK",   "FINANCIALS", "HIGH"),
    ("KOTAKBANK",  "FINANCIALS", "HIGH"), ("AXISBANK",    "FINANCIALS", "HIGH"),
    ("SBIN",       "FINANCIALS", "HIGH"), ("BAJFINANCE",  "FINANCIALS", "HIGH"),
    ("BAJAJFINSV", "FINANCIALS", "MID"),  ("INDUSINDBK",  "FINANCIALS", "MID"),
    ("HDFCAMC",    "FINANCIALS", "MID"),  ("CHOLAFIN",    "FINANCIALS", "MID"),
    ("MUTHOOTFIN", "FINANCIALS", "MID"),  ("SHRIRAMFIN",  "FINANCIALS", "MID"),
    ("PFC",        "FINANCIALS", "MID"),  ("RECLTD",      "FINANCIALS", "MID"),
    ("BANDHANBNK", "FINANCIALS", "MID"),  ("AUBANK",      "FINANCIALS", "MID"),
    ("FEDERALBNK", "FINANCIALS", "MID"),  ("IDFCFIRSTB",  "FINANCIALS", "LOW"),
    ("RBLBANK",    "FINANCIALS", "LOW"),  ("YESBANK",     "FINANCIALS", "LOW"),

    # IT
    ("TCS",        "IT", "HIGH"), ("INFY",        "IT", "HIGH"),
    ("HCLTECH",    "IT", "HIGH"), ("WIPRO",        "IT", "HIGH"),
    ("TECHM",      "IT", "HIGH"), ("LTI",          "IT", "MID"),
    ("MPHASIS",    "IT", "MID"),  ("PERSISTENT",   "IT", "MID"),
    ("COFORGE",    "IT", "MID"),  ("LTTS",         "IT", "MID"),
    ("HEXAWARE",   "IT", "MID"),  ("KPITTECH",     "IT", "MID"),

    # ENERGY
    ("RELIANCE",   "ENERGY", "HIGH"), ("ONGC",    "ENERGY", "HIGH"),
    ("BPCL",       "ENERGY", "HIGH"), ("IOC",     "ENERGY", "HIGH"),
    ("HINDPETRO",  "ENERGY", "MID"),  ("GAIL",    "ENERGY", "MID"),
    ("PETRONET",   "ENERGY", "MID"),  ("IGL",     "ENERGY", "MID"),
    ("MGL",        "ENERGY", "MID"),  ("ATGL",    "ENERGY", "LOW"),

    # AUTO
    ("MARUTI",     "AUTO", "HIGH"), ("M&M",       "AUTO", "HIGH"),
    ("TATAMOTORS", "AUTO", "HIGH"), ("BAJAJ-AUTO", "AUTO", "HIGH"),
    ("HEROMOTOCO", "AUTO", "HIGH"), ("EICHERMOT",  "AUTO", "MID"),
    ("TVSMOTOR",   "AUTO", "MID"),  ("ASHOKLEY",   "AUTO", "MID"),
    ("MOTHERSON",  "AUTO", "MID"),  ("BALKRISIND", "AUTO", "MID"),
    ("BHARATFORG", "AUTO", "MID"),  ("ENDURANCE",  "AUTO", "LOW"),

    # PHARMA
    ("SUNPHARMA",  "PHARMA", "HIGH"), ("DRREDDY",    "PHARMA", "HIGH"),
    ("CIPLA",      "PHARMA", "HIGH"), ("DIVISLAB",   "PHARMA", "HIGH"),
    ("APOLLOHOSP", "PHARMA", "HIGH"), ("BIOCON",     "PHARMA", "MID"),
    ("AUROPHARMA", "PHARMA", "MID"),  ("TORNTPHARM", "PHARMA", "MID"),
    ("ALKEM",      "PHARMA", "MID"),  ("LUPIN",      "PHARMA", "MID"),
    ("IPCALAB",    "PHARMA", "MID"),  ("ABBOTINDIA", "PHARMA", "LOW"),

    # METALS
    ("TATASTEEL",  "METALS", "HIGH"), ("HINDALCO",  "METALS", "HIGH"),
    ("JSWSTEEL",   "METALS", "HIGH"), ("VEDL",      "METALS", "HIGH"),
    ("NMDC",       "METALS", "MID"),  ("NATIONALUM","METALS", "MID"),
    ("SAIL",       "METALS", "MID"),  ("HINDZINC",  "METALS", "MID"),
    ("COALINDIA",  "METALS", "HIGH"), ("JSWENERGY",  "METALS", "MID"),

    # FMCG
    ("HINDUNILVR", "FMCG", "HIGH"), ("ITC",       "FMCG", "HIGH"),
    ("NESTLEIND",  "FMCG", "HIGH"), ("BRITANNIA", "FMCG", "MID"),
    ("DABUR",      "FMCG", "MID"),  ("MARICO",    "FMCG", "MID"),
    ("GODREJCP",   "FMCG", "MID"),  ("COLPAL",    "FMCG", "MID"),
    ("EMAMILTD",   "FMCG", "MID"),  ("VBLLTD",    "FMCG", "MID"),
    ("TATACONSUM", "FMCG", "MID"),  ("MCDOWELL-N","FMCG", "MID"),

    # INFRA / CEMENT
    ("ULTRACEMCO", "INFRA", "HIGH"), ("GRASIM",    "INFRA", "HIGH"),
    ("SHREECEM",   "INFRA", "MID"),  ("AMBUJACEM", "INFRA", "MID"),
    ("ACC",        "INFRA", "MID"),  ("LT",        "INFRA", "HIGH"),
    ("ADANIPORTS", "INFRA", "HIGH"), ("IRB",       "INFRA", "LOW"),
    ("KNR",        "INFRA", "LOW"),  ("JKCEMENT",  "INFRA", "MID"),
    ("DALMIACEME", "INFRA", "MID"),  ("RAMCOCEM",  "INFRA", "LOW"),

    # TELECOM
    ("BHARTIARTL", "TELECOM", "HIGH"), ("INDUSTOWER", "TELECOM", "MID"),
    ("IDEA",       "TELECOM", "MID"),

    # CONSUMER DISCRETIONARY
    ("ASIANPAINT", "CONSUMER", "HIGH"), ("TITAN",    "CONSUMER", "HIGH"),
    ("HAVELLS",    "CONSUMER", "MID"),  ("VOLTAS",   "CONSUMER", "MID"),
    ("WHIRLPOOL",  "CONSUMER", "MID"),  ("CROMPTON",  "CONSUMER", "MID"),
    ("VGUARD",     "CONSUMER", "LOW"),  ("DIXON",    "CONSUMER", "MID"),
    ("AMBER",      "CONSUMER", "LOW"),  ("RAJESHEXPO","CONSUMER", "LOW"),
    ("CERA",       "CONSUMER", "LOW"),  ("SYMPHONY",  "CONSUMER", "LOW"),

    # REALTY
    ("DLF",        "REALTY", "HIGH"), ("GODREJPROP", "REALTY", "MID"),
    ("OBEROIRLTY", "REALTY", "MID"),  ("PRESTIGE",   "REALTY", "MID"),
    ("PHOENIXLTD", "REALTY", "MID"),  ("BRIGADE",    "REALTY", "LOW"),
    ("SOBHA",      "REALTY", "LOW"),  ("MAHLIFE",    "REALTY", "LOW"),

    # CHEMICALS
    ("PIDILITIND",  "CHEMICALS", "HIGH"), ("AAPL",    "CHEMICALS", "MID"),
    ("DEEPAKNTR",   "CHEMICALS", "MID"),  ("TATACHEM", "CHEMICALS", "MID"),
    ("GSFC",        "CHEMICALS", "LOW"),  ("GNFC",    "CHEMICALS", "LOW"),
    ("ATUL",        "CHEMICALS", "MID"),  ("NOCIL",   "CHEMICALS", "LOW"),

    # CAPITAL GOODS
    ("SIEMENS",    "CAPGOODS", "HIGH"), ("ABB",       "CAPGOODS", "HIGH"),
    ("BHEL",       "CAPGOODS", "MID"),  ("THERMAX",   "CAPGOODS", "MID"),
    ("CUMMINSIND", "CAPGOODS", "MID"),  ("AIAENG",    "CAPGOODS", "MID"),
    ("GRINDWELL",  "CAPGOODS", "LOW"),  ("TIMKEN",    "CAPGOODS", "LOW"),
    ("ELGIEQUIP",  "CAPGOODS", "LOW"),  ("KENNAMET",  "CAPGOODS", "LOW"),

    # AVIATION / HOSPITALITY
    ("INDIGO",     "AVIATION", "HIGH"), ("SPICEJET",  "AVIATION", "LOW"),
    ("IRCTC",      "AVIATION", "MID"),  ("MHRIL",     "AVIATION", "LOW"),
    ("MAHINDCIE",  "AVIATION", "LOW"),

    # INSURANCE
    ("SBILIFE",    "INSURANCE", "HIGH"), ("HDFCLIFE",  "INSURANCE", "HIGH"),
    ("ICICIPRULI", "INSURANCE", "HIGH"), ("GICRE",     "INSURANCE", "MID"),
    ("NIACL",      "INSURANCE", "MID"),  ("STARHEALTH", "INSURANCE", "MID"),
]


def get_universe() -> list:
    """Return full universe as list of dicts."""
    return [
        {"ticker": t, "sector": s, "adv_tier": a}
        for t, s, a in _UNIVERSE
    ]


def get_tickers() -> list:
    """Return just ticker symbols."""
    return [t for t, _, _ in _UNIVERSE]


def get_sectors() -> list:
    """Return sorted list of unique sectors."""
    return sorted(set(s for _, s, _ in _UNIVERSE))


def get_sector_peers(sector: str) -> list:
    """Return all tickers in a sector."""
    return [t for t, s, _ in _UNIVERSE if s == sector]


def ticker_to_sector(ticker: str) -> str:
    """Return sector for a ticker. Returns 'UNKNOWN' if not found."""
    return next((s for t, s, _ in _UNIVERSE if t == ticker), "UNKNOWN")


def ticker_to_adv_tier(ticker: str) -> str:
    """Return ADV tier for ticker. Returns 'MID' if not found."""
    return next((a for t, _, a in _UNIVERSE if t == ticker), "MID")
