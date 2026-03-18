"""
KUBER'S CALLING — observation/trade_log.py
============================================
Layer 6: Trade outcome recording.

Called on position close. Updates signal_log outcome fields
and writes to trade_log table. Separate from broker.py to
maintain clean layer separation.
"""

import os, sys, logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

log = logging.getLogger("trade_log")
