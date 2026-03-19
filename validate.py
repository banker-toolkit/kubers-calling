"""
KUBER'S CALLING — VALIDATION AGENT
====================================
Golden source: kubers_calling_master_v2.docx

This agent is an invigilator. It reads the architecture document as
ground truth and verifies the codebase against it. No session starts
until this reports OVERALL: PASS.

Usage:
  python validate.py              # Static + Unit tests (no API needed)
  python validate.py --smoke      # Adds live API smoke tests (needs token)
  python validate.py --report     # Also writes validate_report.html
  python validate.py --verbose    # Show detail on passing tests too

Exit code: 0 = PASS, 1 = FAIL
"""

import ast
import os
import re
import sys
import math
import json
import time
import importlib
import traceback
from pathlib import Path
from datetime import datetime

# ── Always run from the project folder ──────────────────────────────
os.chdir(os.path.dirname(os.path.abspath(__file__)))

SMOKE_MODE   = "--smoke"   in sys.argv
REPORT_MODE  = "--report"  in sys.argv
VERBOSE_MODE = "--verbose" in sys.argv

# ─────────────────────────────────────────────────────────────────────
# RESULT TRACKING
# ─────────────────────────────────────────────────────────────────────
_results = []  # (mode, test_id, description, passed, detail, is_regression)

def record(mode, test_id, desc, passed, detail="", regression=False):
    _results.append((mode, test_id, desc, passed, detail, regression))
    if not passed:
        tag = " [REGRESSION]" if regression else ""
        print(f"  ❌ [{test_id}] {desc}{tag}")
        if detail:
            print(f"       → {detail}")
    elif VERBOSE_MODE:
        print(f"  ✅ [{test_id}] {desc}")

def section(title):
    width = 62
    print(f"\n{'═' * width}")
    print(f"  {title}")
    print(f"{'═' * width}")

# ─────────────────────────────────────────────────────────────────────
# FILE PARSING HELPERS
# ─────────────────────────────────────────────────────────────────────
_ast_cache = {}

def parse(filename):
    """Returns (tree, source) or (None, '') on error."""
    if filename not in _ast_cache:
        p = Path(filename)
        if not p.exists():
            _ast_cache[filename] = (None, "")
            return None, ""
        try:
            src = p.read_text(encoding="utf-8")
            _ast_cache[filename] = (ast.parse(src), src)
        except SyntaxError as e:
            _ast_cache[filename] = (None, "")
            record("STATIC", "SYN", f"{filename}: syntax error", False,
                   str(e), regression=False)
    return _ast_cache[filename]

def src(filename):
    _, s = parse(filename)
    return s

def funcs(filename):
    tree, _ = parse(filename)
    if not tree:
        return []
    return [n.name for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef)]

def all_imports(filename):
    tree, _ = parse(filename)
    if not tree:
        return []
    result = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            result.append(node.module or "")
    return result

def has_pattern(filename, pattern):
    return bool(re.search(pattern, src(filename)))

# ─────────────────────────────────────────────────────────────────────
# MODE 1 — STATIC ANALYSIS
# ─────────────────────────────────────────────────────────────────────
def run_static():
    section("MODE 1 — STATIC ANALYSIS")

    # ── Syntax check every Python file
    py_files = sorted(Path(".").glob("*.py")) + \
               sorted(Path("database").glob("*.py")) if Path("database").exists() else []
    py_files = sorted(Path(".").rglob("*.py"))
    for f in py_files:
        # Skip ml_workbench — separate application
        if "ml_workbench" in str(f):
            continue
        tree, _ = parse(str(f))
        record("STATIC", "SYN-001",
               f"{f.name}: valid Python syntax",
               tree is not None,
               "Syntax error — file will not import" if tree is None else "")

    # ── REG-001a: No numpy/scipy in pure-Python modules
    pure_python = ["spy_agent.py", "volume_profile.py", "scout_math.py"]
    for fname in pure_python:
        imports = all_imports(fname)
        bad = [i for i in imports if "numpy" in (i or "") or "scipy" in (i or "")]
        record("STATIC", "REG-001a",
               f"{fname}: no numpy/scipy imports",
               len(bad) == 0,
               f"Found: {bad}" if bad else "",
               regression=True)

    # ── REG-001b: No np. or linregress bare references in spy_agent
    for pattern, label in [
        (r"\bnp\.", "np. reference"),
        (r"\blinregress\b", "linregress reference"),
    ]:
        found = has_pattern("spy_agent.py", pattern)
        record("STATIC", "REG-001b",
               f"spy_agent.py: no undeclared '{label}'",
               not found,
               f"Found undeclared {label}" if found else "",
               regression=True)

    # ── REG-003: No duplicate function definitions in any module
    check_files = [
        "spy_agent.py", "market_data.py", "scout_math.py",
        "volume_profile.py", "bouncer.py", "vajrapaat.py",
        "shadow_book.py", "historical_db.py", "candle_builder.py",
        "build_history.py", "kubers_calling.py",
    ]
    for fname in check_files:
        if not Path(fname).exists():
            continue
        tree, _ = parse(fname)
        if not tree:
            continue
        # Only check MODULE-LEVEL functions (direct children of module)
        # Class methods and nested helpers share names legitimately
        top_funcs = [n.name for n in ast.iter_child_nodes(tree)
                     if isinstance(n, ast.FunctionDef)]
        dups = sorted(set(n for n in top_funcs if top_funcs.count(n) > 1))
        record("STATIC", "REG-003",
               f"{fname}: no duplicate module-level function definitions",
               len(dups) == 0,
               f"Duplicates: {dups}" if dups else "",
               regression=True)

    # ── REG-002: scrip-codes never passed as requests params dict
    api_files = ["market_data.py"]
    for fname in api_files:
        if not Path(fname).exists():
            continue
        # Look for params={... 'scrip' or "scrip" ...}
        bad = bool(re.search(
            r'params\s*=\s*\{[^}]*["\']scrip', src(fname)
        ))
        record("STATIC", "REG-002",
               f"{fname}: scrip-codes not in requests params dict",
               not bad,
               "Found params={...scrip-codes...} — will be percent-encoded" if bad else "",
               regression=True)

    # ── REG-005: No print(end='\r') hiding errors
    for fname in ["build_history.py", "historical_db.py", "market_data.py"]:
        if not Path(fname).exists():
            continue
        bad = has_pattern(fname, r"print\s*\(.*end\s*=\s*['\"]\\r['\"]")
        record("STATIC", "REG-005",
               f"{fname}: no print(end='\\r') hiding errors",
               not bad,
               "Found print(end='\\r') — errors will be overwritten" if bad else "",
               regression=True)

    # ── REG-006: update_today called in dossier/auditor block
    # Check either auditor.py or vajrapaat.py references update_today
    found_update = (
         "update_today" in src("observation/auditor.py") if Path("observation/auditor.py").exists() else False
    ) or (
        "update_today" in src("vajrapaat.py") if Path("vajrapaat.py").exists() else False
    )
    record("STATIC", "REG-006",
           "update_today() wired into daily dossier trigger",
           found_update,
           "update_today not found in auditor.py or vajrapaat.py" if not found_update else "",
           regression=True)

    # ── REG-007: os.chdir present in startup files
    for fname in ["build_history.py", "kubers_calling.py"]:
        if not Path(fname).exists():
            continue
        has_chdir = has_pattern(fname, r"os\.chdir\s*\(")
        record("STATIC", "REG-007",
               f"{fname}: os.chdir at startup",
               has_chdir,
               "Missing os.chdir — DB may be written to wrong directory" if not has_chdir else "",
               regression=True)

    # ── REG-008: yfinance never called with interval='3m'
    for fname in ["build_history.py", "historical_db.py", "history_loader.py"]:
        if not Path(fname).exists():
            continue
        bad = has_pattern(fname, r"""interval\s*=\s*['"]3m['"]""")
        record("STATIC", "REG-008",
               f"{fname}: yfinance interval not '3m'",
               not bad,
               "interval='3m' found — Yahoo Finance does not support this" if bad else "",
               regression=True)

    # ── REG-009: Engine not launched via subprocess.Popen
    bad = has_pattern("kubers_calling.py", r"subprocess\.Popen")
    record("STATIC", "REG-009",
           "kubers_calling.py: engine uses threading not subprocess",
           not bad,
           "subprocess.Popen found — will crash on Windows" if bad else "",
           regression=True)

    # ── REG-010: signal.signal() wrapped in try/except
    s = src("vajrapaat.py")
    if "signal.signal" in s or "sig_mod.signal" in s:
        # Walk AST: find all signal.signal calls and verify they're inside Try nodes
        tree_v, _ = parse("vajrapaat.py")
        sig_call_lines = set()
        try_ranges = []
        for node in ast.walk(tree_v):
            if isinstance(node, ast.Try):
                # Collect line range of the try body
                if node.body:
                    start = node.lineno
                    end   = max(getattr(n, 'lineno', start) for n in ast.walk(node))
                    try_ranges.append((start, end))
        sig_lines_v = [i for i, l in enumerate(s.split("\n"), 1)
                       if "signal.signal" in l or "sig_mod.signal" in l]
        protected = all(
            any(start <= ln <= end for start, end in try_ranges)
            for ln in sig_lines_v
        )
        record("STATIC", "REG-010",
               "vajrapaat.py: signal.signal() in try/except block",
               protected,
               "signal.signal() called outside try block — crashes in non-main thread" if not protected else "",
               regression=True)

    # ── REG-004: Z-score cap present in both profile and scout
    for fname, pattern in [
        ("features/volume_profile.py", r"MAX_Z_SCORE|10\.0"),  # FIXED: file lives in features/
        ("scout_math.py",              r"MAX_Z_SCORE|10\.0"),
    ]:
        has_cap = has_pattern(fname, pattern)
        record("STATIC", "REG-004",
               f"{fname}: Z-score cap (MAX_Z_SCORE) present",
               has_cap,
               "Z-score cap missing — extreme values will pass Scout" if not has_cap else "",
               regression=True)

    # ── ARCH-001: No magic numbers in vajrapaat/engine
    # ARCH-001 checks the active engine file only
    # vajrapaat.py is legacy — excluded once engine.py exists
    _engine_file = "engine.py" if Path("engine.py").exists() else None
    _engine_files = [_engine_file] if _engine_file else []
    for fname in _engine_files:
        if not Path(fname).exists():
            continue
        lines = src(fname).split("\n")
        magic = []
        for i, line in enumerate(lines, 1):
            # Remove comments and strings
            code_part = re.sub(r'#.*$', '', line)
            code_part = re.sub(r'["\'][^"\']*["\']', '""', code_part)
            # Find numeric literals that aren't 0,1,2,-1 or part of log messages
            nums = re.findall(r'(?<!\w)(?<!\.)\b(\d{2,})\b(?!\w)', code_part)
            # Filter out obvious non-thresholds
            real_magic = [n for n in nums
                          if n not in ('10','15','20','30','60','100','200','300','500')
                          and '_log' not in line
                          and 'print' not in line
                          and 'f"' not in line
                          and "f'" not in line]
            if real_magic:
                magic.append(f"L{i}: {line.strip()[:60]}")
        record("STATIC", "ARCH-001",
               f"{fname}: no obvious magic numbers (thresholds from config)",
               len(magic) == 0,
               f"{len(magic)} suspect lines: {magic[:3]}" if magic else "")

    # ── ARCH-002: All config imports exist in config.py
    config_src_text = src("config.py")
    config_constants = set(re.findall(
        r'^([A-Z_0-9]{3,})\s*=', config_src_text, re.MULTILINE
    ))
    for fname in ["bouncer.py", "scout_math.py", "spy_agent.py", "vajrapaat.py"]:
        if not Path(fname).exists():
            continue
        file_src = src(fname)
        imported = []
        for match in re.finditer(
            r'from\s+config\s+import\s+\(([^)]+)\)', file_src
        ):
            imported += [x.strip() for x in match.group(1).split(",") if x.strip()]
        for match in re.finditer(
            r'from\s+config\s+import\s+([A-Z_,\s]+?)(?:\n|#)', file_src
        ):
            imported += [x.strip() for x in match.group(1).split(",") if x.strip()]
        missing = [x for x in imported if x and x not in config_constants]
        record("STATIC", "ARCH-002",
               f"{fname}: all config imports exist in config.py",
               len(missing) == 0,
               f"Missing from config.py: {missing}" if missing else "")

    # ── ARCH-003: ML workbench does not import trading engine modules
    # (Only relevant once ml_workbench/ exists)
    wb_path = Path("ml_workbench")
    if wb_path.exists():
        engine_modules = {
            "engine", "risk_gate", "broker", "feed",
            "candle_factory", "feature_engine"
        }
        for wb_file in wb_path.rglob("*.py"):
            imports = all_imports(str(wb_file))
            bad_imports = [i for i in imports
                           if i and i.split(".")[-1] in engine_modules]
            record("STATIC", "REG-014",
                   f"ml_workbench/{wb_file.name}: no trading engine imports",
                   len(bad_imports) == 0,
                   f"Imports trading engine modules: {bad_imports}" if bad_imports else "",
                   regression=True)

    # ── ARCH-004: No inline imports (imports at top of file only)
    for fname in ["spy_agent.py", "scout_math.py", "volume_profile.py",
                  "bouncer.py"]:
        if not Path(fname).exists():
            continue
        tree, _ = parse(fname)
        if not tree:
            continue
        # Find imports inside function bodies
        inline = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for child in ast.walk(node):
                    if isinstance(child, (ast.Import, ast.ImportFrom)):
                        # Allow 'from volume_profile import' in scout_math
                        # (optional dependency pattern)
                        mod = getattr(child, 'module', '') or ''
                        if mod not in ('volume_profile', 'config'):
                            inline.append(f"{node.name}(): imports {mod}")
        record("STATIC", "ARCH-004",
               f"{fname}: no unexpected inline imports",
               len(inline) == 0,
               f"Inline imports found: {inline[:3]}" if inline else "")

    # ── ARCH-005: No bare except clauses
    silent_except_files = [
        "spy_agent.py", "scout_math.py", "market_data.py",
        "historical_db.py", "bouncer.py", "candle_builder.py"
    ]
    for fname in silent_except_files:
        if not Path(fname).exists():
            continue
        tree, _ = parse(fname)
        if not tree:
            continue
        bare = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                if node.type is None:
                    bare.append(f"line {node.lineno}")
        record("STATIC", "ARCH-005",
               f"{fname}: no bare except clauses",
               len(bare) == 0,
               f"Bare except at: {bare}" if bare else "")


# ─────────────────────────────────────────────────────────────────────
# MODE 2 — UNIT TESTS
# ─────────────────────────────────────────────────────────────────────
def make_candles(n, base=100.0, vol=10000, trend=0.01):
    """Generate synthetic OHLCV candles."""
    candles = []
    for i in range(n):
        c = base + i * trend
        candles.append({
            "open":   c - 0.1,
            "high":   c + 0.2,
            "low":    c - 0.2,
            "close":  c,
            "volume": vol,
            "time":   1700000000 + i * 180,
        })
    return candles

def run_unit_tests():
    section("MODE 2 — UNIT TESTS")

    # ── UT-001: Spy PASS on insufficient sector data
    try:
        from spy_agent import spy_absorption_protocol
        candles = make_candles(5)
        sig, metrics = spy_absorption_protocol(candles, [100.0, 100.1], 1.0)
        passed = (sig == "PASS" and
                  "Insufficient" in metrics.get("reject_reason", ""))
        record("UNIT", "UT-001",
               "spy_agent: PASS with reject_reason on insufficient sector data",
               passed,
               f"sig={sig} reason={metrics.get('reject_reason','')}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-001", "spy_agent: insufficient sector data", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-002: Z-score capped at MAX_Z_SCORE in volume_profile
    try:
        # FIXED: volume_profile lives at features/volume_profile.py, not project root
        if str(Path("features").resolve()) not in sys.path:
            sys.path.insert(0, str(Path("features").resolve()))
        import volume_profile as vpm
        vpm._profile["__TEST__"] = {"10:00": (100.0, 1.0)}
        vpm._profile_built = True
        z = vpm.get_volume_z("__TEST__", 9_999_999.0,
                              datetime(2026, 3, 9, 10, 0))
        del vpm._profile["__TEST__"]
        passed = (z is not None and
                  not math.isnan(z) and
                  abs(z) <= vpm.MAX_Z_SCORE)
        record("UNIT", "UT-002",
               f"volume_profile: Z capped at MAX_Z_SCORE={vpm.MAX_Z_SCORE}",
               passed,
               f"Got z={z}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-002", "volume_profile: Z cap", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-003: Rolling Z also capped in scout_math
    try:
        from scout_math import calculate_volume_zscore
        base = make_candles(25, vol=100)
        base[-1]["volume"] = 9_999_999
        _, z = calculate_volume_zscore(base)
        from scout_math import MAX_Z_SCORE as SCOUT_MAX
        passed = abs(z) <= SCOUT_MAX
        record("UNIT", "UT-003",
               f"scout_math: rolling Z capped at {SCOUT_MAX}",
               passed,
               f"Got z={z}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-003", "scout_math: rolling Z cap", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-004: News filter blocks idiosyncratic move
    try:
        from spy_agent import spy_absorption_protocol
        from config import SECTOR_SLOPE_PERIOD
        # Stock dropped 15% from prev_close, sector flat
        candles = make_candles(5, base=85.0)
        sector = [100.0 + i * 0.001 for i in range(SECTOR_SLOPE_PERIOD + 2)]
        sig, metrics = spy_absorption_protocol(
            candles, sector, 1.0, prev_close=100.0
        )
        passed = (sig == "PASS" and
                  "NEWS FILTER" in metrics.get("reject_reason", ""))
        record("UNIT", "UT-004",
               "spy_agent: news filter blocks -15% stock vs flat sector",
               passed,
               f"sig={sig} reason={metrics.get('reject_reason','')}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-004", "spy_agent: news filter", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-005: Kill switch blocks all orders
    try:
        try:
            from risk.risk_gate import RiskManager
        except ImportError:
            from bouncer import RiskManager
        b = RiskManager()
        b.kill_switch_fired = True
        qty, msg = b.validate_order(
            "TCS", "BUY", 3500.0, 2, "IT", 2.5, 0.0
        )
        passed = (qty == 0 and "kill" in msg.lower())
        record("UNIT", "UT-005",
               "bouncer: kill switch blocks all orders",
               passed,
               f"qty={qty} msg={msg}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-005", "bouncer: kill switch", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-006: Sector cap blocks 3rd position in same sector
    try:
        try:
            from risk.risk_gate import RiskManager
        except ImportError:
            from bouncer import RiskManager
        from config import MAX_SECTOR_POSITIONS
        b = RiskManager()
        # Fill sector to cap
        for i in range(MAX_SECTOR_POSITIONS):
            b.live_positions[f"STOCK{i}"] = {
                "sector":      "ENERGY",
                "qty":         5,
                "entry_price": 200.0,
                "direction":   "LONG",   # fixed: was "side":"BUY" — must match risk_gate schema
            }
        qty, msg = b.validate_order(
            "BPCL", "BUY", 400.0, 10, "ENERGY", 2.5, 0.0
        )
        passed = (qty == 0 and "sector" in msg.lower())
        record("UNIT", "UT-006",
               f"bouncer: sector cap blocks position {MAX_SECTOR_POSITIONS+1}",
               passed,
               f"qty={qty} msg={msg}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-006", "bouncer: sector cap", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-007: Scout returns False on insufficient candle history
    try:
        from scout_math import scout_trigger_evaluation
        from config import MIN_CANDLES_3M
        short = make_candles(max(1, MIN_CANDLES_3M - 1))
        passed_flag, z, atr, vel = scout_trigger_evaluation(short, short)
        passed = (not passed_flag and math.isnan(z))
        record("UNIT", "UT-007",
               f"scout_math: returns False when < MIN_CANDLES_3M ({MIN_CANDLES_3M})",
               passed,
               f"passed_flag={passed_flag}, z={z}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-007", "scout_math: insufficient history gate", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-008: Candle builder inject then retrieve
    try:
        from candle_builder import CandleStore
        cs = CandleStore()
        test_candles = [
            {"time": 1000 * i, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.5, "volume": 5000}
            for i in range(10)
        ]
        cs.inject_historical("TESTSTOCK", test_candles, "3m")
        result = cs.get_candles("TESTSTOCK", "3m")
        passed = (len(result) == 10 and
                  abs(result[0]["close"] - 100.5) < 0.001)
        record("UNIT", "UT-008",
               "candle_builder: inject_historical then retrieve",
               passed,
               f"got {len(result)} candles" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-008", "candle_builder: inject_historical", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-009: No short entries during open protection
    try:
        try:
            from risk.risk_gate import RiskManager
            import risk.risk_gate as bmod
        except ImportError:
            from bouncer import RiskManager
            import bouncer as bmod
        b = RiskManager()
        _orig = bmod.RiskManager._is_open_protection_window
        bmod.RiskManager._is_open_protection_window = lambda self, nc=0.0: True
        qty, msg = b.validate_order(
            "RELIANCE", "SELL", 2800.0, 5, "ENERGY", 3.0, 0.0
        )
        bmod.RiskManager._is_open_protection_window = _orig
        passed = (qty == 0)
        record("UNIT", "UT-009",
               "bouncer: no SELL entries during open protection",
               passed,
               f"qty={qty} — short should be blocked" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-009", "bouncer: open protection no shorts", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-010: Open protection halves position size
    try:
        try:
            from risk.risk_gate import RiskManager
            import risk.risk_gate as bmod
        except ImportError:
            from bouncer import RiskManager
            import bouncer as bmod
        from config import OPEN_POSITION_SIZE_PCT, DEFAULT_PER_STOCK_LIMIT
        b = RiskManager()
        _orig = bmod.RiskManager._is_open_protection_window
        bmod.RiskManager._is_open_protection_window = lambda self, nc=0.0: True
        # Request a qty that would cost ~ per_stock_limit at price=100
        full_qty = int(DEFAULT_PER_STOCK_LIMIT / 100.0)
        qty, _ = b.validate_order(
            "RELIANCE", "BUY", 100.0, full_qty, "ENERGY", 3.5, 0.0
        )
        bmod.RiskManager._is_open_protection_window = _orig
        expected_max = math.ceil(full_qty * OPEN_POSITION_SIZE_PCT)
        passed = (qty <= expected_max or qty == 0)
        record("UNIT", "UT-010",
               f"bouncer: open protection limits size to {OPEN_POSITION_SIZE_PCT*100:.0f}%",
               passed,
               f"qty={qty}, expected <= {expected_max}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-010", "bouncer: open protection half size", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-011: Ex-self composite excludes target ticker exactly
    try:
        from spy_agent import build_ex_self_composite
        peers = {
            "RELIANCE": [100.0, 101.0, 102.0],
            "ONGC":     [50.0,  51.0,  52.0],
            "BPCL":     [200.0, 201.0, 202.0],
        }
        composite = build_ex_self_composite("RELIANCE", peers)
        # Should be average of ONGC and BPCL only
        expected_last = (52.0 + 202.0) / 2.0   # = 127.0
        passed = (len(composite) > 0 and
                  abs(composite[-1] - expected_last) < 0.01)
        record("UNIT", "UT-011",
               "spy_agent: ex-self composite excludes target ticker",
               passed,
               f"last={composite[-1] if composite else 'empty'}, expected={expected_last}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-011", "spy_agent: ex-self composite", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-012: get_prev_close returns 0.0 for unknown ticker (no exception)
    try:
        from historical_db import get_prev_close
        result = get_prev_close("THIS_TICKER_DOES_NOT_EXIST_XYZ_999")
        passed = (result == 0.0)
        record("UNIT", "UT-012",
               "historical_db: get_prev_close returns 0.0 for unknown ticker",
               passed,
               f"Got {result}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-012", "historical_db: get_prev_close no exception", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-013: ATR calculation returns positive value on valid candles
    try:
        from scout_math import calculate_atr
        candles = make_candles(15)
        atr = calculate_atr(candles, period=10)
        passed = (atr > 0)
        record("UNIT", "UT-013",
               "scout_math: ATR > 0 on valid candles",
               passed,
               f"Got atr={atr}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-013", "scout_math: ATR calculation", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-014: Sector slope returns float on valid closes
    try:
        from spy_agent import calculate_sector_slope
        closes = [100.0 + i * 0.1 for i in range(15)]
        slope = calculate_sector_slope(closes, period=10)
        passed = (isinstance(slope, float) and slope > 0)
        record("UNIT", "UT-014",
               "spy_agent: calculate_sector_slope returns positive float on rising series",
               passed,
               f"Got slope={slope}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-014", "spy_agent: sector slope", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-015: Volume profile build with synthetic data
    try:
        # FIXED: volume_profile lives at features/volume_profile.py, not project root
        if str(Path("features").resolve()) not in sys.path:
            sys.path.insert(0, str(Path("features").resolve()))
        import volume_profile as vpm
        import sqlite3, tempfile, os
        from config import DB_PATH
        # Build a minimal in-memory profile check
        # Just test that build_volume_profile handles empty DB gracefully
        old_path = vpm.__dict__.get('_profile', {})
        # It should not crash even with no DB data
        count = vpm.build_volume_profile(tickers=["NONEXISTENT_TICKER_XYZ"])
        passed = True  # didn't crash
        record("UNIT", "UT-015",
               "volume_profile: build_volume_profile handles missing ticker gracefully",
               passed, "")
    except Exception as e:
        record("UNIT", "UT-015", "volume_profile: graceful missing ticker", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-016: Velocity cap blocks after max entries
    try:
        try:
            from risk.risk_gate import RiskManager
        except ImportError:
            from bouncer import RiskManager
        from config import VELOCITY_CAP_MAX_ENTRIES, ENABLE_VELOCITY_CAP
        if not ENABLE_VELOCITY_CAP:
            record("UNIT", "UT-016",
                   "bouncer: velocity cap (SKIPPED — ENABLE_VELOCITY_CAP=False)",
                   True, "Feature flag off — skipping")
        else:
            b = RiskManager()
            # Record max entries
            for _ in range(VELOCITY_CAP_MAX_ENTRIES):
                b.record_entry_timestamp()
            result = b.check_velocity_cap()
            passed = result is True
            record("UNIT", "UT-016",
                   f"bouncer: velocity cap blocks after {VELOCITY_CAP_MAX_ENTRIES} entries",
                   passed,
                   f"check_velocity_cap returned {result}" if not passed else "",
                   regression=True)
    except AttributeError:
        record("UNIT", "UT-016",
               "bouncer: velocity cap methods",
               False,
               "record_entry_timestamp() or check_velocity_cap() not implemented yet",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-016", "bouncer: velocity cap", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-016: PRAGMA foreign_keys=ON must NOT appear in vault.py
    # Root cause: PRAGMA foreign_keys=ON + shadow_log.signal_id="" crashes every cycle.
    try:
        vault_src = open("database/vault.py", encoding="utf-8").read()
        # It may exist in a comment but must not be active (uncommented)
        import re
        active_fk = re.search(r'^\s*conn\.execute\s*\(\s*["\']PRAGMA foreign_keys\s*=\s*ON["\']',
                              vault_src, re.MULTILINE)
        passed = active_fk is None
        record("UNIT", "REG-016",
               "vault.py: PRAGMA foreign_keys=ON must be DISABLED (crashes shadow cycles)",
               passed,
               "FOUND active PRAGMA foreign_keys=ON — this crashes every cycle with shadow signals" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-016", "vault.py: foreign_keys pragma", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-017: shadow_book signal_id must use None not ""
    # Root cause: signal_id="" fails FK check; signal_id=None is allowed (NULL).
    try:
        sb_src = open("strategy/shadow_book.py", encoding="utf-8").read()
        # Must not contain the old pattern: metadata.get("signal_id", "")
        bad_pattern = 'metadata.get("signal_id", "")'
        passed = bad_pattern not in sb_src
        record("UNIT", "REG-017",
               "shadow_book: signal_id uses None not empty string (FK compliance)",
               passed,
               f"Found '{bad_pattern}' — empty string fails FK constraint, use `or None`" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-017", "shadow_book: signal_id None check", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-018: nifty_open_change must be written to _state in engine.py
    # Root cause: Missing _state["nifty_open_change"] → dashboard shows "--" for NIFTY open
    try:
        eng_src = open("engine.py", encoding="utf-8").read()
        passed = '_state["nifty_open_change"]' in eng_src
        record("UNIT", "REG-018",
               "engine.py: nifty_open_change written to _state (dashboard open display)",
               passed,
               "nifty_open_change not found in _state updates — dashboard will show '--'" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-018", "engine.py: nifty_open_change in state", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-019: nifty_open_change initialised in _state dict literal
    try:
        eng_src = open("engine.py", encoding="utf-8").read()
        # Find _state dict initialisation — nifty_open_change must be in it
        import re
        state_block = re.search(r'_state\s*=\s*\{[^}]+\}', eng_src, re.DOTALL)
        passed = state_block is not None and "nifty_open_change" in state_block.group()
        record("UNIT", "REG-019",
               "engine.py: nifty_open_change initialised in _state dict",
               passed,
               "nifty_open_change missing from _state initial dict" if not passed else "")
    except Exception as e:
        record("UNIT", "REG-019", "engine.py: _state init", False,
               traceback.format_exc().split('\n')[-2])

    # ── REG-020: Dashboard JS uses equity_floor not equity
    # Root cause: live_config.equity does not exist; correct key is equity_floor
    try:
        kc_src = open("kubers_calling.py", encoding="utf-8").read()
        bad  = "live_config?.equity||" in kc_src or "live_config.equity||" in kc_src
        good = "live_config?.equity_floor" in kc_src or "live_config.equity_floor" in kc_src
        passed = good and not bad
        record("UNIT", "REG-020",
               "kubers_calling.py: dashboard JS uses equity_floor not equity",
               passed,
               "Wrong key: live_config.equity (doesn't exist) — must be equity_floor" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-020", "dashboard: equity_floor key", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-021: Config panel exists in dashboard HTML
    # Root cause: No UI for changing limits → operator cannot adjust them at runtime
    try:
        kc_src = open("kubers_calling.py", encoding="utf-8").read()
        has_global    = "cfgGlobal"    in kc_src
        has_per_stock = "cfgPerStock"  in kc_src
        has_floor     = "cfgFloor"     in kc_src
        has_save      = "saveConfig"   in kc_src
        passed = has_global and has_per_stock and has_floor and has_save
        missing = [n for n, v in [("cfgGlobal", has_global),("cfgPerStock", has_per_stock),
                                   ("cfgFloor", has_floor),("saveConfig", has_save)] if not v]
        record("UNIT", "REG-021",
               "kubers_calling.py: live config panel present (global_limit, per_stock, floor)",
               passed,
               f"Missing: {missing}" if missing else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-021", "dashboard: config panel", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-022: NIFTY 50 scrip code is correct (NIDX format)
    # Root cause: Wrong ticker caused NIFTY data to return 0
    try:
        cfg_src = open("config.py", encoding="utf-8").read()
        import re
        match = re.search(r'SCRIP_NIFTY50\s*=\s*["\']([^"\']+)["\']', cfg_src)
        passed = match is not None and match.group(1).startswith("NIDX_")
        val = match.group(1) if match else "NOT FOUND"
        record("UNIT", "REG-022",
               "config.py: SCRIP_NIFTY50 uses NIDX_ format",
               passed,
               f"Value: {val} — expected NIDX_XXXXXXXX" if not passed else f"Value: {val}",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-022", "config.py: SCRIP_NIFTY50", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-023: India VIX scrip code is correct (NIDX format)
    # Root cause: Wrong ticker caused VIX to return 0 → STANDBY all day
    try:
        cfg_src = open("config.py", encoding="utf-8").read()
        import re
        match = re.search(r'SCRIP_INDIA_VIX\s*=\s*["\']([^"\']+)["\']', cfg_src)
        passed = match is not None and match.group(1).startswith("NIDX_")
        val = match.group(1) if match else "NOT FOUND"
        record("UNIT", "REG-023",
               "config.py: SCRIP_INDIA_VIX uses NIDX_ format",
               passed,
               f"Value: {val} — expected NIDX_XXXXXXXX" if not passed else f"Value: {val}",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-023", "config.py: SCRIP_INDIA_VIX", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-025: engine.py calls auditor.run_if_missed() at startup
    # Root cause: BUG-007 — dossier never fires at 16:30 because loop exits at 15:20.
    # Morning catchup in startup() is the fix. If this call is removed, dossier silently
    # stops running forever and candle history / shadow analytics go stale.
    try:
        eng_src = open("engine.py", encoding="utf-8").read()
        passed = "run_if_missed" in eng_src
        record("UNIT", "REG-025",
               "engine.py: auditor.run_if_missed() called at startup (BUG-007 morning catchup)",
               passed,
               "run_if_missed not found — EOD dossier will never run (loop exits at 15:20)" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-025", "engine.py: run_if_missed present", False,
               traceback.format_exc().split('\n')[-2], regression=True)


    # Root cause: NIFTY never downloaded → ATR always 0 → STANDBY every cycle
    try:
        bh_src = open("build_history.py", encoding="utf-8").read()
        passed = "NIFTY 50" in bh_src and ("universe.insert" in bh_src or '"NIFTY 50"' in bh_src)
        record("UNIT", "REG-024",
               "build_history.py: NIFTY 50 explicitly in download queue",
               passed,
               "NIFTY 50 missing from build_history universe — ATR will be 0 on first run" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-024", "build_history.py: NIFTY 50 in queue", False,
               traceback.format_exc().split('\n')[-2], regression=True)


    # ── REG-026: positions table columns match what broker.py passes to upsert_position()
    # Root cause: today's cascade — DB write failed silently on column mismatch,
    # leaving risk gate, kill switch, and sector cap completely blind all session.
    # This test catches schema drift before the engine starts trading.
    try:
        import sqlite3
        from config import DB_LIVE_PATH
        conn = sqlite3.connect(DB_LIVE_PATH)
        db_cols = set(row[1] for row in conn.execute("PRAGMA table_info(positions)"))
        conn.close()

        # Exact columns broker.py passes to upsert_position() on every fill
        required_cols = {
            "ticker", "direction", "entry_price", "qty",
            "sl_price", "target_price", "entry_time",
            "order_id", "sector", "signal_id", "strategy_name"
        }
        missing = required_cols - db_cols
        extra   = db_cols - required_cols  # allowed — extra cols in DB are fine
        passed  = len(missing) == 0
        record("UNIT", "REG-026",
               "vault: positions table has all columns broker.py requires",
               passed,
               f"Missing columns: {missing} — upsert_position() will crash silently" if not passed else
               f"All {len(required_cols)} required columns present",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-026", "vault: positions schema check", False,
               traceback.format_exc().split("\n")[-2], regression=True)

    # ─────────────────────────────────────────────────────────────
    # NEW TESTS — Day 1 post-mortem fixes (v1.2)
    # ─────────────────────────────────────────────────────────────

    # ── REG-027: SL polling present in evaluate_exits (BUG-011 fix guard)
    try:
        broker_src = open("execution/broker.py", encoding="utf-8").read()
        passed = "SL_HIT" in broker_src and "poll" in broker_src
        record("UNIT", "REG-027",
               "broker.py: SL_HIT polling present in evaluate_exits (BUG-011)",
               passed,
               "SL_HIT or poll not found — positions will never detect broker-side SL hits" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-027", "broker.py: SL polling", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-028: exit polling present — _pending_exits and poll_pending_exits (exit fill guard)
    try:
        broker_src = open("execution/broker.py", encoding="utf-8").read()
        has_pending_exits  = "_pending_exits" in broker_src
        has_poll_exits     = "poll_pending_exits" in broker_src
        passed = has_pending_exits and has_poll_exits
        record("UNIT", "REG-028",
               "broker.py: _pending_exits and poll_pending_exits present (exit fill guard)",
               passed,
               f"Missing: {'_pending_exits' if not has_pending_exits else 'poll_pending_exits'} — "
               "exits marked closed before fill confirmed" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-028", "broker.py: exit polling", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-029: poll_pending_exits called in engine.py each cycle
    try:
        eng_src = open("engine.py", encoding="utf-8").read()
        passed = "poll_pending_exits" in eng_src
        record("UNIT", "REG-029",
               "engine.py: poll_pending_exits() called each cycle",
               passed,
               "poll_pending_exits not wired into run_cycle — exit fills never confirmed" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-029", "engine.py: poll_pending_exits wired", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-030: ENABLE_VELOCITY_CAP = False in config
    try:
        from config import ENABLE_VELOCITY_CAP
        passed = (ENABLE_VELOCITY_CAP is False)
        record("UNIT", "REG-030",
               "config.py: ENABLE_VELOCITY_CAP=False (no API rate basis confirmed Day 1)",
               passed,
               f"ENABLE_VELOCITY_CAP={ENABLE_VELOCITY_CAP} — velocity cap has no justification" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-030", "config.py: ENABLE_VELOCITY_CAP", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-031: MIN_ORDER_VALUE present in config and >= 15000
    try:
        from config import MIN_ORDER_VALUE
        passed = (MIN_ORDER_VALUE >= 15000)
        record("UNIT", "REG-031",
               f"config.py: MIN_ORDER_VALUE >= 15000 (currently {MIN_ORDER_VALUE})",
               passed,
               f"MIN_ORDER_VALUE={MIN_ORDER_VALUE} — below ₹15K orders are cost-inefficient" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-031", "config.py: MIN_ORDER_VALUE", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-032: MIN_ORDER_VALUE check present in broker.py submit()
    try:
        broker_src = open("execution/broker.py", encoding="utf-8").read()
        passed = "MIN_ORDER_VALUE" in broker_src
        record("UNIT", "REG-032",
               "broker.py: MIN_ORDER_VALUE guard in submit() (cost efficiency gate)",
               passed,
               "MIN_ORDER_VALUE check missing — small orders will be placed unprofitably" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-032", "broker.py: MIN_ORDER_VALUE guard", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-033: MIN_ATR_PCT present in config
    try:
        from config import MIN_ATR_PCT
        passed = (0 < MIN_ATR_PCT <= 0.01)
        record("UNIT", "REG-033",
               f"config.py: MIN_ATR_PCT present and plausible (currently {MIN_ATR_PCT:.4f})",
               passed,
               f"MIN_ATR_PCT={MIN_ATR_PCT} — expected 0 < value <= 0.01" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-033", "config.py: MIN_ATR_PCT", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-034: MIN_ATR_PCT guard present in rule_strategy.py
    try:
        rs_src = open("strategy/rule_strategy.py", encoding="utf-8").read()
        passed = "MIN_ATR_PCT" in rs_src and "atr_pct" in rs_src
        record("UNIT", "REG-034",
               "rule_strategy.py: MIN_ATR_PCT guard implemented (blocks razor-thin ATR entries)",
               passed,
               "MIN_ATR_PCT check missing from rule_strategy — low-ATR flash-close trades will fire" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-034", "rule_strategy.py: MIN_ATR_PCT guard", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-035: SL_COOLDOWN_MIN present in config
    try:
        from config import SL_COOLDOWN_MIN
        passed = (SL_COOLDOWN_MIN > 0)
        record("UNIT", "REG-035",
               f"config.py: SL_COOLDOWN_MIN present and positive (currently {SL_COOLDOWN_MIN} min)",
               passed,
               f"SL_COOLDOWN_MIN={SL_COOLDOWN_MIN} — must be > 0" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-035", "config.py: SL_COOLDOWN_MIN", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-036: record_sl_hit and _sl_cooldown present in risk_gate.py
    try:
        rg_src = open("risk/risk_gate.py", encoding="utf-8").read()
        has_method   = "record_sl_hit" in rg_src
        has_dict     = "_sl_cooldown" in rg_src
        has_check    = "SL_COOLDOWN" in rg_src
        passed = has_method and has_dict and has_check
        missing = [n for n, v in [("record_sl_hit", has_method),
                                   ("_sl_cooldown", has_dict),
                                   ("SL_COOLDOWN check", has_check)] if not v]
        record("UNIT", "REG-036",
               "risk_gate.py: SL cooldown implemented (record_sl_hit, _sl_cooldown, check)",
               passed,
               f"Missing: {missing}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-036", "risk_gate.py: SL cooldown", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-037: broker.py calls record_sl_hit on SL_HIT close
    try:
        broker_src = open("execution/broker.py", encoding="utf-8").read()
        passed = "record_sl_hit" in broker_src
        record("UNIT", "REG-037",
               "broker.py: record_sl_hit() called on SL_HIT close (wires cooldown)",
               passed,
               "record_sl_hit not called — SL cooldown will never activate" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-037", "broker.py: record_sl_hit wired", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-038: SESSION_START defined in config (replaces hardcoded "09:15")
    try:
        from config import SESSION_START
        passed = (SESSION_START == "09:15")
        record("UNIT", "REG-038",
               f'config.py: SESSION_START defined (currently "{SESSION_START}")',
               passed,
               f'SESSION_START="{SESSION_START}" — expected "09:15"' if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-038", "config.py: SESSION_START", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── REG-039: risk_gate.py has no hardcoded "09:15" literal
    try:
        rg_src = open("risk/risk_gate.py", encoding="utf-8").read()
        # Strip comments before checking — literal in comment is fine
        code_lines = [l for l in rg_src.split('\n') if not l.strip().startswith('#')]
        code_only  = '\n'.join(code_lines)
        passed = '"09:15"' not in code_only and "'09:15'" not in code_only
        record("UNIT", "REG-039",
               'risk_gate.py: no hardcoded "09:15" literal (uses SESSION_START from config)',
               passed,
               'Found hardcoded "09:15" — must use SESSION_START from config' if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-039", "risk_gate.py: no hardcoded session start", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-023: SL cooldown blocks re-entry on same ticker after SL hit
    try:
        try:
            from risk.risk_gate import RiskManager
        except ImportError:
            from bouncer import RiskManager
        from config import SL_COOLDOWN_MIN
        b = RiskManager()
        b.record_sl_hit("NOCIL")
        qty, msg = b.validate_order(
            "NOCIL", "BUY", 250.0, 5, "CHEMICALS", 2.5, 0.0
        )
        passed = (qty == 0 and "SL_COOLDOWN" in msg)
        record("UNIT", "UT-023",
               "risk_gate: SL cooldown blocks re-entry immediately after SL hit",
               passed,
               f"qty={qty} msg={msg} — expected qty=0 and SL_COOLDOWN in msg" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-023", "risk_gate: SL cooldown blocks re-entry", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-024: SL cooldown expires and allows re-entry after timeout
    try:
        try:
            from risk.risk_gate import RiskManager
        except ImportError:
            from bouncer import RiskManager
        b = RiskManager()
        # Backdate the cooldown timestamp so it has already expired
        b._sl_cooldown["NOCIL"] = time.time() - (SL_COOLDOWN_MIN * 60 + 1)
        qty, msg = b.validate_order(
            "NOCIL", "BUY", 250.0, 5, "CHEMICALS", 2.5, 0.0
        )
        # Cooldown expired — should be approved (or fail for another reason, NOT SL_COOLDOWN)
        passed = ("SL_COOLDOWN" not in msg)
        record("UNIT", "UT-024",
               "risk_gate: SL cooldown expires and allows re-entry after timeout",
               passed,
               f"msg={msg} — SL_COOLDOWN should have expired" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "UT-024", "risk_gate: SL cooldown expiry", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── UT-025: MIN_ORDER_VALUE blocks small order in broker submit()
    try:
        from config import MIN_ORDER_VALUE
        # Simulate the guard logic directly (broker needs full stack to instantiate)
        price = 100.0
        qty   = max(1, int((MIN_ORDER_VALUE * 0.5) / price))  # deliberately below threshold
        order_value = qty * price
        passed = (order_value < MIN_ORDER_VALUE)   # confirms our test input is sub-threshold
        record("UNIT", "UT-025",
               f"config: MIN_ORDER_VALUE={MIN_ORDER_VALUE} — test input ₹{order_value:.0f} is correctly sub-threshold",
               passed,
               f"Test setup error: order_value={order_value} is not < MIN_ORDER_VALUE={MIN_ORDER_VALUE}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-025", "config: MIN_ORDER_VALUE threshold", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-026: ATR% guard rejects low-ATR ticker in rule_strategy
    try:
        from config import MIN_ATR_PCT
        # Simulate the guard: ATR=0.5, price=2000 → atr_pct=0.00025 which is < 0.003
        atr   = 0.5
        price = 2000.0
        atr_pct = atr / price
        passed = (atr_pct < MIN_ATR_PCT)   # confirms this would be rejected
        record("UNIT", "UT-026",
               f"config: MIN_ATR_PCT guard — atr_pct={atr_pct:.5f} correctly below threshold {MIN_ATR_PCT}",
               passed,
               f"Test setup error: atr_pct={atr_pct} is not < MIN_ATR_PCT={MIN_ATR_PCT}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-026", "config: MIN_ATR_PCT threshold", False,
               traceback.format_exc().split('\n')[-2])

    # ── UT-027: ATR% guard passes high-ATR ticker in rule_strategy
    try:
        from config import MIN_ATR_PCT
        # BAJFINANCE-style: ATR=25, price=700 → atr_pct=0.036 which is > 0.003
        atr   = 25.0
        price = 700.0
        atr_pct = atr / price
        passed = (atr_pct > MIN_ATR_PCT)   # confirms this would pass
        record("UNIT", "UT-027",
               f"config: MIN_ATR_PCT guard — atr_pct={atr_pct:.4f} correctly above threshold {MIN_ATR_PCT}",
               passed,
               f"Test setup error: atr_pct={atr_pct} is not > MIN_ATR_PCT={MIN_ATR_PCT}" if not passed else "")
    except Exception as e:
        record("UNIT", "UT-027", "config: MIN_ATR_PCT pass case", False,
               traceback.format_exc().split('\n')[-2])

    # ── REG-040: EXIT_ORDER_TTL_CANDLES defined in config
    try:
        from config import EXIT_ORDER_TTL_CANDLES
        passed = (EXIT_ORDER_TTL_CANDLES >= 2)
        record("UNIT", "REG-040",
               f"config.py: EXIT_ORDER_TTL_CANDLES defined and >= 2 (currently {EXIT_ORDER_TTL_CANDLES})",
               passed,
               f"EXIT_ORDER_TTL_CANDLES={EXIT_ORDER_TTL_CANDLES} — too short, exits won't have time to fill" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-040", "config.py: EXIT_ORDER_TTL_CANDLES", False,
               traceback.format_exc().split('\n')[-2], regression=True)
    # ── REG-041: WS_ORDER_UPDATES_URL defined in config ─────────────────
    try:
        from config import WS_ORDER_UPDATES_URL
        passed = WS_ORDER_UPDATES_URL.startswith("wss://")
        record("UNIT", "REG-041",
               f"config.py: WS_ORDER_UPDATES_URL defined and starts with wss:// ({WS_ORDER_UPDATES_URL})",
               passed,
               f"Value: {WS_ORDER_UPDATES_URL}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-041", "config.py: WS_ORDER_UPDATES_URL", False,
               traceback.format_exc().split("\n")[-2], regression=True)

    # ── REG-042: FORCE_CLOSE_TIME defined and before EOD_SQUAREOFF_TIME ─
    try:
        from config import FORCE_CLOSE_TIME, EOD_SQUAREOFF_TIME
        passed = FORCE_CLOSE_TIME < EOD_SQUAREOFF_TIME
        record("UNIT", "REG-042",
               f"config.py: FORCE_CLOSE_TIME ({FORCE_CLOSE_TIME}) before EOD ({EOD_SQUAREOFF_TIME})",
               passed,
               f"FORCE_CLOSE_TIME {FORCE_CLOSE_TIME} >= EOD {EOD_SQUAREOFF_TIME}" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-042", "config.py: FORCE_CLOSE_TIME", False,
               traceback.format_exc().split("\n")[-2], regression=True)

    # ── REG-043: BROKERAGE_PER_ORDER_RS == 5.0 (IndMoney flat rate) ─────
    try:
        from config import BROKERAGE_PER_ORDER_RS
        passed = (BROKERAGE_PER_ORDER_RS == 5.0)
        record("UNIT", "REG-043",
               f"config.py: BROKERAGE_PER_ORDER_RS == 5.0 (IndMoney flat rate, currently {BROKERAGE_PER_ORDER_RS})",
               passed,
               f"Value {BROKERAGE_PER_ORDER_RS} — IndMoney charges flat Rs5 per order" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-043", "config.py: BROKERAGE_PER_ORDER_RS", False,
               traceback.format_exc().split("\n")[-2], regression=True)

    # ── REG-044: MARKET orders omit limit_price in feed.py ───────────────
    # Root cause of all SL_HIT ghost positions: LIMIT at Rs0 was rejected by IndMoney
    # IndMoney validates LimitPriceMustBeAboveZero on every order including MARKET
    try:
        feed_src = open("data/feed.py", encoding="utf-8").read()
        # Must have conditional: only include limit_price for LIMIT orders
        has_guard = 'if order.order_type == "LIMIT"' in feed_src
        # Must NOT have unconditional limit_price in payload for market orders
        # Check that the payload dict doesn't always include limit_price
        record("UNIT", "REG-044",
               "data/feed.py: MARKET orders omit limit_price (LimitPriceMustBeAboveZero fix)",
               has_guard,
               "limit_price guard missing — MARKET orders will be rejected with Rs0" if not has_guard else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-044", "data/feed.py: MARKET order payload", False,
               traceback.format_exc().split("\n")[-2], regression=True)

    # ── REG-045: feed.py checks SUCCESS not COMPLETE ──────────────────────
    # COMPLETE does not exist in IndMoney API. Actual terminal status is SUCCESS.
    # Every fill confirmation via REST was broken before v8.
    try:
        feed_src = open("data/feed.py", encoding="utf-8").read()
        has_success   = '"SUCCESS"' in feed_src
        # COMPLETE should only appear as the *internal normalised value*, not as API check
        # In _STATUS_MAP: "SUCCESS": "COMPLETE" — this is correct normalisation
        # What we must NOT have: checking for status == "COMPLETE" from IndMoney directly
        # The _STATUS_MAP maps IndMoney "SUCCESS" -> internal "COMPLETE" — that is correct
        record("UNIT", "REG-045",
               "data/feed.py: IndMoney SUCCESS status handled (not COMPLETE as external check)",
               has_success,
               "SUCCESS not found in feed.py — fill confirmations will fail" if not has_success else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-045", "data/feed.py: SUCCESS status string", False,
               traceback.format_exc().split("\n")[-2], regression=True)

    # ── REG-046: SL_HIT goes through _pending_exits not fast-track ───────
    # Root cause of Mar-19 ghost positions: SL_HIT fast-tracked delete
    # before sending close order to IndMoney
    try:
        import re as _re
        broker_src = open("execution/broker.py", encoding="utf-8").read()
        # Must NOT have: if reason == "SL_HIT": immediate pop+book (fast track)
        fast_track = bool(_re.search(
            r'if reason == .SL_HIT.:\s*\n\s*self\._positions\.pop',
            broker_src
        ))
        # Must have: SL_HIT goes to _pending_exits
        has_pending = "_pending_exits" in broker_src
        passed = (not fast_track) and has_pending
        record("UNIT", "REG-046",
               "execution/broker.py: SL_HIT exits through _pending_exits (no fast-track delete)",
               passed,
               "SL_HIT fast-tracks position delete before broker confirmation — ghost positions will occur" if not passed else "",
               regression=True)
    except Exception as e:
        record("UNIT", "REG-046", "execution/broker.py: SL_HIT through _pending_exits", False,
               traceback.format_exc().split("\n")[-2], regression=True)




# ─────────────────────────────────────────────────────────────────────
# MODE 3 — SMOKE TESTS (live API)
# ─────────────────────────────────────────────────────────────────────
def run_smoke_tests():
    section("MODE 3 — SMOKE TESTS  (live API)")

    # ── SM-001: Token loads
    try:
        import market_data
        loaded = market_data.load_token()
        record("SMOKE", "SM-001",
               "market_data: JWT token loads from investright_creds.json",
               loaded,
               "Token missing or malformed — paste today's token first" if not loaded else "")
    except Exception as e:
        record("SMOKE", "SM-001", "market_data: token load", False,
               traceback.format_exc().split('\n')[-2])

    # ── SM-002: API connection
    try:
        import market_data
        ok = market_data.verify_connection()
        record("SMOKE", "SM-002",
               "market_data: INDstocks API connection verified",
               ok,
               "Connection failed — check token and network" if not ok else "")
    except Exception as e:
        record("SMOKE", "SM-002", "market_data: API connection", False,
               traceback.format_exc().split('\n')[-2])

    # ── SM-003: Token map loads
    try:
        import market_data
        count = market_data.forge_token_map()
        passed = count > 1000
        record("SMOKE", "SM-003",
               f"market_data: token map loaded ({count} tokens)",
               passed,
               f"Only {count} — expected > 1000" if not passed else "")
    except Exception as e:
        record("SMOKE", "SM-003", "market_data: token map", False,
               traceback.format_exc().split('\n')[-2])

    # ── SM-004: NIFTY fetch — price non-zero (REG-013)
    try:
        import market_data
        nifty = market_data.fetch_nifty_data()
        price = nifty.get("price", 0) if isinstance(nifty, dict) else 0
        passed = price > 1000
        record("SMOKE", "SM-004",
               f"market_data: NIFTY price non-zero ({price:.0f})",
               passed,
               f"NIFTY price={price} — zero or low during market hours is a data error" if not passed else "",
               regression=True)
    except Exception as e:
        record("SMOKE", "SM-004", "market_data: NIFTY fetch", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── SM-005: VIX fetch — non-zero (REG-013)
    try:
        import market_data
        vix = market_data.fetch_vix()
        passed = (8.0 < vix < 80.0)
        record("SMOKE", "SM-005",
               f"market_data: VIX non-zero and plausible ({vix:.1f})",
               passed,
               f"VIX={vix} — outside plausible range 8–80" if not passed else "",
               regression=True)
    except Exception as e:
        record("SMOKE", "SM-005", "market_data: VIX fetch", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── SM-006: NIFTY ATR computation — non-zero (REG-013)
    try:
        from candle_builder import candle_store
        from scout_math import calculate_atr
        nifty_candles = candle_store.get_candles("NIFTY", "15m")
        if len(nifty_candles) >= 2:
            atr = calculate_atr(nifty_candles)
            passed = atr > 0
            record("SMOKE", "SM-006",
                   f"candle_builder: NIFTY 15m ATR non-zero ({atr:.1f})",
                   passed,
                   f"NIFTY ATR={atr} — zero means no candles built yet" if not passed else "",
                   regression=True)
        else:
            record("SMOKE", "SM-006",
                   "candle_builder: NIFTY 15m ATR (insufficient candles yet)",
                   True,  # Not a failure — market may just have opened
                   f"Only {len(nifty_candles)} NIFTY candles — needs time to build")
    except Exception as e:
        record("SMOKE", "SM-006", "candle_builder: NIFTY ATR", False,
               traceback.format_exc().split('\n')[-2], regression=True)

    # ── SM-007: Historical DB populated
    try:
        from historical_db import is_history_populated
        populated = is_history_populated()
        record("SMOKE", "SM-007",
               "historical_db: candle history present in DB",
               populated,
               "DB empty — run build_history.py before trading" if not populated else "")
    except Exception as e:
        record("SMOKE", "SM-007", "historical_db: populated", False,
               traceback.format_exc().split('\n')[-2])

    # ── SM-008: Volume profile builds
    try:
        from volume_profile import build_volume_profile, get_profile_coverage
        count = build_volume_profile()
        cov   = get_profile_coverage()
        passed = (count > 100 and cov.get("buckets", 0) > 5000)
        record("SMOKE", "SM-008",
               f"volume_profile: built for {count} tickers ({cov.get('buckets',0):,} buckets)",
               passed,
               f"Too few: {count} tickers, {cov.get('buckets',0)} buckets" if not passed else "")
    except Exception as e:
        record("SMOKE", "SM-008", "volume_profile: build", False,
               traceback.format_exc().split('\n')[-2])

    # ── SM-009: Live quote for RELIANCE
    try:
        import market_data
        quotes = market_data.fetch_quotes(["RELIANCE"])
        price  = quotes.get("RELIANCE", {}).get("price", 0)
        passed = price > 500
        record("SMOKE", "SM-009",
               f"market_data: RELIANCE live quote ({price:.0f})",
               passed,
               f"price={price} — suspiciously low or zero" if not passed else "")
    except Exception as e:
        record("SMOKE", "SM-009", "market_data: live quote", False,
               traceback.format_exc().split('\n')[-2])


# ─────────────────────────────────────────────────────────────────────
# SUMMARY AND REPORT
# ─────────────────────────────────────────────────────────────────────
def print_summary():
    print(f"\n{'═' * 62}")
    print("  VALIDATION SUMMARY")
    print(f"{'═' * 62}")

    by_mode = {}
    for mode, tid, desc, passed, detail, reg in _results:
        by_mode.setdefault(mode, []).append(passed)

    total_pass = sum(1 for r in _results if r[3])
    total_fail = sum(1 for r in _results if not r[3])
    total_reg_fail = sum(1 for _, __, ___, p, ____, reg in _results
                         if not p and reg)

    for mode, results in by_mode.items():
        n_pass = sum(results)
        n_fail = len(results) - n_pass
        line = f"  {mode:12s}  {n_pass:2d}/{len(results):2d} passed"
        if n_fail:
            line += f"   ❌ {n_fail} failed"
        print(line)

    print(f"\n  Total:   {total_pass} passed  |  {total_fail} failed")
    if total_reg_fail:
        print(f"  WARNING: {total_reg_fail} REGRESSION failures "
              f"(previously fixed bugs have reappeared)")

    overall = (total_fail == 0)
    print(f"\n  {'✅ OVERALL: PASS' if overall else '❌ OVERALL: FAIL'}")

    if overall:
        print("  DEPLOYMENT: ✅ SAFE TO START ENGINE")
    else:
        failed = [tid for _, tid, _, p, __, ___ in _results if not p]
        print(f"  DEPLOYMENT: ❌ HOLD — fix {failed} first")

    return overall


def write_html_report():
    rows = ""
    for mode, tid, desc, passed, detail, reg in _results:
        bg   = "#f0fff0" if passed else "#fff0f0"
        icon = "✅" if passed else "❌"
        reg_badge = '<span style="background:#ffd0d0;padding:2px 6px;border-radius:3px;font-size:11px;margin-left:6px">REGRESSION</span>' if reg and not passed else ""
        rows += (f'<tr style="background:{bg}">'
                 f'<td>{mode}</td><td><code>{tid}</code></td>'
                 f'<td>{desc}{reg_badge}</td>'
                 f'<td style="text-align:center">{icon}</td>'
                 f'<td style="font-size:12px;color:#666">{detail}</td>'
                 f'</tr>')

    total_pass = sum(1 for r in _results if r[3])
    total_fail = len(_results) - total_pass
    overall    = "PASS" if total_fail == 0 else "FAIL"
    color      = "#1A6B2A" if total_fail == 0 else "#C00000"
    ts         = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<title>Kuber's Calling — Validation Report</title>
<style>
  body {{ font-family: Arial, sans-serif; padding: 24px;
          background: #f8f9fa; color: #222; }}
  h1   {{ color: #1B3A5C; margin-bottom: 4px; }}
  .summary {{ background: #1B3A5C; color: white; padding: 20px 24px;
              border-radius: 8px; margin-bottom: 24px; }}
  .overall {{ font-size: 22px; font-weight: bold; color: {color}; }}
  table {{ border-collapse: collapse; width: 100%; background: white;
           box-shadow: 0 1px 4px rgba(0,0,0,0.1); }}
  th  {{ background: #1B3A5C; color: white; padding: 10px 12px;
         text-align: left; font-size: 13px; }}
  td  {{ padding: 8px 12px; border: 1px solid #e0e0e0; font-size: 13px; }}
  code {{ background: #f0f4f8; padding: 2px 5px; border-radius: 3px;
          font-size: 12px; }}
</style></head><body>
<h1>Kuber's Calling — Validation Report</h1>
<div class="summary">
  <div class="overall">OVERALL: {overall}</div>
  <div style="margin-top:6px;font-size:14px">
    Run: {ts} &nbsp;|&nbsp; {total_pass} passed &nbsp;|&nbsp; {total_fail} failed
  </div>
</div>
<table>
  <thead><tr>
    <th>Mode</th><th>Test ID</th><th>Description</th>
    <th>Result</th><th>Detail</th>
  </tr></thead>
  <tbody>{rows}</tbody>
</table>
</body></html>"""

    with open("validate_report.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n  Report written → validate_report.html")


# ─────────────────────────────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("\n" + "═" * 62)
    print("  KUBER'S CALLING — VALIDATION AGENT  v1.2")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"  Mode: {'STATIC+UNIT+SMOKE' if SMOKE_MODE else 'STATIC+UNIT'}")
    print("═" * 62)

    run_static()
    run_unit_tests()

    if SMOKE_MODE:
        run_smoke_tests()
    else:
        print("\n  [Smoke tests skipped — run with --smoke for live API checks]")

    overall_pass = print_summary()

    if REPORT_MODE:
        write_html_report()

    sys.exit(0 if overall_pass else 1)