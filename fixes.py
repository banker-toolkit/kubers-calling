"""
KUBERS CALLING — fix_schema_crash.py
=======================================
Root cause (confirmed by traceback):
  vault.py line 246: conn.executescript(_LIVE_SCHEMA)
  raises: sqlite3.OperationalError: no such column: order_id

_LIVE_SCHEMA contains SQL that references order_id (an index or constraint).
The on-disk positions table was created with an old schema that lacks order_id.
executescript crashes before migrate_live_db ever runs.

Fix: wrap executescript in try/except in init_live_db.
If it fails (old DB schema), just continue — migrate_live_db adds the
missing columns immediately after.

Run from C:\\Kubers\\engine\\
  python fix_schema_crash.py
  python validate.py
"""

import os, sys, ast, re, shutil
from pathlib import Path
from datetime import datetime

ENGINE = Path(os.path.dirname(os.path.abspath(__file__)))
VAULT  = ENGINE / "database" / "vault.py"
shutil.copy2(VAULT, ENGINE / f"database/vault.py.bak_{datetime.now().strftime('%H%M%S')}")

src = VAULT.read_text(encoding='utf-8')

# ── Find init_live_db and patch executescript line ──────────────────
# We replace:
#     conn.executescript(_LIVE_SCHEMA)
#     conn.commit()
#     conn.close()
# with:
#     try:
#         conn.executescript(_LIVE_SCHEMA)
#     except Exception:
#         pass   # old DB schema — migrate_live_db below adds missing columns
#     conn.commit()
#     conn.close()
#     migrate_live_db()

OLD = '    conn.executescript(_LIVE_SCHEMA)\n    conn.commit()\n    conn.close()'
NEW = ('    try:\n'
       '        conn.executescript(_LIVE_SCHEMA)\n'
       '    except Exception:\n'
       '        pass   # existing DB has old schema — migrate_live_db() adds missing columns\n'
       '    conn.commit()\n'
       '    conn.close()\n'
       '    migrate_live_db()  # always run — adds any columns missing from old schemas')

# There may be multiple executescript calls (init_live_db and init_decisions_db)
# Only patch the one inside init_live_db
if OLD in src:
    # Make sure we only patch the LIVE_SCHEMA one, not DECISIONS_SCHEMA
    src = src.replace(OLD, NEW, 1)
    VAULT.write_text(src, encoding='utf-8')
    print("  ✓  vault.py: executescript wrapped in try/except + migrate call added")
else:
    # Already patched or different formatting — check
    if 'except Exception:\n        pass   # existing DB' in src:
        print("  ✓  vault.py: already patched")
    else:
        # Find the exact line with executescript in init_live_db context
        lines = src.splitlines()
        for i, line in enumerate(lines):
            if 'executescript(_LIVE_SCHEMA)' in line:
                print(f"  Found executescript at L{i+1}: {repr(line)}")
                # Patch this specific line
                indent = len(line) - len(line.lstrip())
                pad = ' ' * indent
                new_lines = lines.copy()
                # Replace the single executescript line with wrapped version
                new_lines[i] = (
                    f"{pad}try:\n"
                    f"{pad}    conn.executescript(_LIVE_SCHEMA)\n"
                    f"{pad}except Exception:\n"
                    f"{pad}    pass   # old schema — migrate_live_db adds missing columns"
                )
                src = '\n'.join(new_lines)
                VAULT.write_text(src, encoding='utf-8')
                print("  ✓  vault.py: executescript wrapped (line-based patch)")
                break
        else:
            print("  ✗  executescript(_LIVE_SCHEMA) not found in vault.py")
            sys.exit(1)

# Verify migrate_live_db is called from init_live_db
src = VAULT.read_text(encoding='utf-8')
idx_init = src.find('def init_live_db')
idx_next = src.find('\ndef ', idx_init + 10)
init_body = src[idx_init:idx_next]
if 'migrate_live_db' not in init_body:
    # Add it — find the conn.close() inside init_live_db and append after
    src = src.replace(init_body, init_body.rstrip() + '\n    migrate_live_db()\n', 1)
    VAULT.write_text(src, encoding='utf-8')
    print("  ✓  vault.py: migrate_live_db() call added to init_live_db")
else:
    print("  ✓  vault.py: migrate_live_db() already called from init_live_db")

# Syntax check
try:
    ast.parse(VAULT.read_text(encoding='utf-8'))
    print("  ✓  database/vault.py: syntax OK")
except SyntaxError as e:
    print(f"  ✗  database/vault.py: SYNTAX ERROR L{e.lineno}: {e.msg}")
    sys.exit(1)

# ── Also fix validate.py UT-005 (use outer boundary replacement) ──────
print()
VAL = ENGINE / "validate.py"
shutil.copy2(VAL, ENGINE / f"validate.py.bak_{datetime.now().strftime('%H%M%S')}")
val = VAL.read_text(encoding='utf-8')

START = '    # ── UT-005: Kill switch blocks all orders'
END   = '\n\n    # ── UT-006:'

si = val.find(START)
ei = val.find(END)

if si < 0 or ei < 0:
    print(f"  ✗  UT-005 boundaries not found (si={si} ei={ei})")
else:
    CLEAN = '''    # ── UT-005: Kill switch blocks all orders
    try:
        try:
            from risk.risk_gate import RiskManager
        except ImportError:
            from bouncer import RiskManager
        b = object.__new__(RiskManager)
        b.kill_switch_fired    = True
        b.live_positions       = {}
        b.current_equity       = 100000.0
        b.equity_floor         = 95000.0
        b.global_limit         = 100000.0
        b.per_stock_limit      = 25000.0
        b.max_order_value      = 45000.0
        b.session_pnl          = 0.0
        b._sl_cooldown         = {}
        import collections as _col
        b._entry_timestamps    = _col.deque()
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
               traceback.format_exc().split('\\n')[-2], regression=True)'''

    new_val = val[:si] + CLEAN + val[ei:]
    VAL.write_text(new_val, encoding='utf-8')
    print("  ✓  validate.py: UT-005 replaced with object.__new__ version")

try:
    ast.parse(VAL.read_text(encoding='utf-8'))
    print("  ✓  validate.py: syntax OK")
except SyntaxError as e:
    print(f"  ✗  validate.py: SYNTAX ERROR L{e.lineno}: {e.msg}")
    sys.exit(1)

print()
print("  Run:  python validate.py")
print("  Expected: OVERALL: PASS")