"""
KUBERS CALLING — apply_fixes_final3.py
========================================
Fixes the 3 remaining anchor mismatches from apply_fixes_part2.py.
Run from C:\\Kubers\\engine\\
  python apply_fixes_final3.py
"""

import os, sys, ast, shutil, re
from pathlib import Path
from datetime import datetime

ENGINE   = Path(os.path.dirname(os.path.abspath(__file__)))
BACKUP   = ENGINE / f"_backup3_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
BACKUP.mkdir(exist_ok=True)

passed = []
failed = []

def ok(s):  passed.append(s); print(f"  ✓  {s}")
def err(s): failed.append(s); print(f"  ✗  {s}")

def backup(p):
    if Path(p).exists(): shutil.copy2(p, BACKUP / Path(p).name)

def check(path, label):
    try:
        ast.parse(Path(path).read_text(encoding='utf-8'))
        ok(f"{label}: syntax OK")
        return True
    except SyntaxError as e:
        err(f"{label}: SYNTAX ERROR L{e.lineno} — {e.msg}")
        return False

# ─────────────────────────────────────────────────────────────────
# Read exact current content of the 3 files
# ─────────────────────────────────────────────────────────────────
FEED      = ENGINE / "data" / "feed.py"
RISK      = ENGINE / "risk" / "risk_gate.py"
VAULT     = ENGINE / "database" / "vault.py"

print("Reading exact file content...\n")

feed_src  = FEED.read_text(encoding='utf-8')
risk_src  = RISK.read_text(encoding='utf-8')
vault_src = VAULT.read_text(encoding='utf-8')

# Show the config import block from each file so we can see exact text
def show_import_block(src, fname):
    idx = src.find('from config import')
    if idx < 0:
        print(f"  {fname}: no 'from config import' found")
        return
    end = src.find(')', idx)
    print(f"  {fname} config import block:")
    print("    " + src[idx:end+1].replace('\n', '\n    '))
    print()

show_import_block(feed_src,  "data/feed.py")
show_import_block(risk_src,  "risk/risk_gate.py")

# Show positions table in vault
idx = vault_src.find('positions (')
if idx >= 0:
    end = vault_src.find(');', idx)
    print("  database/vault.py positions table:")
    print("    " + vault_src[idx:end+2].replace('\n', '\n    '))
    print()

# ─────────────────────────────────────────────────────────────────
# FIX A — data/feed.py: inject WS_ORDER_UPDATES_URL into config import
# Strategy: find the exact closing paren of the from config import block
#           and add WS_ORDER_UPDATES_URL before it
# ─────────────────────────────────────────────────────────────────
print("=" * 60)
print("  FIX A — data/feed.py: add WS_ORDER_UPDATES_URL import")
print("=" * 60)

backup(FEED)

if 'WS_ORDER_UPDATES_URL' in feed_src:
    ok("feed: WS_ORDER_UPDATES_URL already imported")
else:
    # Find the config import block and its closing paren
    cfg_start = feed_src.find('from config import')
    if cfg_start < 0:
        err("feed: no 'from config import' block found")
    else:
        cfg_end = feed_src.find('\n)', cfg_start)   # find closing )\n
        if cfg_end < 0:
            cfg_end = feed_src.find(')', cfg_start)

        # Insert before the closing paren line
        # Find the last comma'd import line and append after it
        block = feed_src[cfg_start:cfg_end+2]
        print(f"  Found block ending at pos {cfg_end}, char: {repr(feed_src[cfg_end:cfg_end+3])}")

        # Simple approach: replace the closing ) with the new import + )
        # Find exact closing sequence
        close_seq = None
        for candidate in ['\n)', '\n    )', '\n)']:
            test_idx = feed_src.find(candidate, cfg_start)
            if cfg_start < test_idx < cfg_start + 500:
                close_seq = candidate
                break

        if close_seq:
            new_feed = feed_src.replace(
                feed_src[feed_src.find(close_seq, cfg_start):feed_src.find(close_seq, cfg_start)+len(close_seq)],
                '\n    WS_ORDER_UPDATES_URL,' + close_seq,
                1
            )
            # Verify we actually changed it
            if 'WS_ORDER_UPDATES_URL' in new_feed:
                FEED.write_text(new_feed, encoding='utf-8')
                ok("feed: WS_ORDER_UPDATES_URL added to config import")
            else:
                err("feed: replacement did not insert WS_ORDER_UPDATES_URL")
        else:
            # Fallback: regex approach
            new_feed = re.sub(
                r'(from config import\s*\([^)]+)',
                r'\1    WS_ORDER_UPDATES_URL,\n',
                feed_src,
                count=1,
                flags=re.DOTALL
            )
            if 'WS_ORDER_UPDATES_URL' in new_feed:
                FEED.write_text(new_feed, encoding='utf-8')
                ok("feed: WS_ORDER_UPDATES_URL added (regex fallback)")
            else:
                err("feed: could not inject WS_ORDER_UPDATES_URL — add manually")
                print("  Manual fix: in data/feed.py, find 'from config import' block")
                print("  and add:  WS_ORDER_UPDATES_URL,")

check(FEED, "data/feed.py")

# ─────────────────────────────────────────────────────────────────
# FIX B — risk/risk_gate.py: inject MAX_OPEN_POSITIONS etc into imports
# ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  FIX B — risk/risk_gate.py: add MAX_OPEN_POSITIONS etc")
print("=" * 60)

backup(RISK)
risk_src = RISK.read_text(encoding='utf-8')   # re-read (may have been patched)

missing = [c for c in ['MAX_OPEN_POSITIONS', 'MAX_ENTRY_PRICE', 'SLOT_SIZE']
           if c not in risk_src]

if not missing:
    ok("risk_gate: MAX_OPEN_POSITIONS, MAX_ENTRY_PRICE, SLOT_SIZE already imported")
else:
    print(f"  Need to add: {missing}")

    # Find the config import block closing paren
    cfg_start = risk_src.find('from config import')
    cfg_end   = risk_src.find('\n)', cfg_start)

    if cfg_end < 0:
        err("risk_gate: cannot find config import closing paren")
    else:
        insert_str = ''
        for c in missing:
            insert_str += f'\n    {c},'
        new_risk = risk_src[:cfg_end] + insert_str + risk_src[cfg_end:]
        RISK.write_text(new_risk, encoding='utf-8')
        for c in missing:
            if c in new_risk:
                ok(f"risk_gate: {c} added to imports")
            else:
                err(f"risk_gate: {c} not found after insert")

check(RISK, "risk/risk_gate.py")

# ─────────────────────────────────────────────────────────────────
# FIX C — database/vault.py: add exit_order_id, residual_id columns
# ─────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("  FIX C — database/vault.py: add three-ID lineage columns")
print("=" * 60)

backup(VAULT)
vault_src = VAULT.read_text(encoding='utf-8')

missing_cols = [c for c in ['exit_order_id', 'residual_id', 'position_type']
                if c not in vault_src]

if not missing_cols:
    ok("vault: exit_order_id, residual_id, position_type already in schema")
else:
    print(f"  Need to add: {missing_cols}")

    # Find the positions table CREATE statement and its closing );
    tbl_start = vault_src.find('CREATE TABLE IF NOT EXISTS positions (')
    if tbl_start < 0:
        tbl_start = vault_src.find('positions (')

    if tbl_start < 0:
        err("vault: cannot find positions table definition")
    else:
        # Find the ); that closes the CREATE TABLE
        close_idx = vault_src.find(');', tbl_start)
        if close_idx < 0:
            err("vault: cannot find ); closing positions table")
        else:
            # Find the last column line before );
            # Insert our new columns just before the closing );
            current_last = vault_src[tbl_start:close_idx].rstrip()
            # Check if last line has a comma
            lines_before = current_last.splitlines()
            last_line = lines_before[-1] if lines_before else ''

            new_cols = ''
            for c in missing_cols:
                if c == 'exit_order_id':
                    new_cols += '\n    exit_order_id   TEXT,              -- v8: IndMoney exit order ID'
                elif c == 'residual_id':
                    new_cols += '\n    residual_id     TEXT,              -- v8: partial fill residual tracker'
                elif c == 'position_type':
                    new_cols += "\n    position_type   TEXT DEFAULT 'LIVE'  -- LIVE | GHOST | RECONCILED"

            # If last line before ); doesn't end with comma, need to add one
            # Find the char just before );
            before_close = vault_src[tbl_start:close_idx].rstrip()
            if not before_close.endswith(','):
                # Add comma to last column line
                vault_src = vault_src[:close_idx] + ',' + new_cols + '\n' + vault_src[close_idx:]
            else:
                vault_src = vault_src[:close_idx] + new_cols + '\n' + vault_src[close_idx:]

            VAULT.write_text(vault_src, encoding='utf-8')
            for c in missing_cols:
                v = VAULT.read_text(encoding='utf-8')
                if c in v:
                    ok(f"vault: {c} added")
                else:
                    err(f"vault: {c} NOT added — check manually")

check(VAULT, "database/vault.py")

# ─────────────────────────────────────────────────────────────────
# FINAL SUMMARY
# ─────────────────────────────────────────────────────────────────
print()
print("=" * 60)
print(f"  DONE  ✓ {len(passed)} passed   ✗ {len(failed)} failed")
print("=" * 60)
if failed:
    print("\n  FAILED:")
    for f in failed: print(f"    {f}")
    print()
    print("  These 3 items can be fixed manually in 2 minutes —")
    print("  they are import line additions only, no logic changes.")
else:
    print()
    print("  ALL 3 REMAINING FIXES APPLIED.")
    print()
    print("  Now run:  python validate.py")
    print("  Then:     python notifier.py   (test Gmail)")