"""
Verify TIME_STOP_CHECK exits against Yahoo Finance 2m candle data.
Run: pip install yfinance && python3 verify_exits.py
"""
import yfinance as yf
from datetime import datetime, timezone, timedelta
import pytz

IST = pytz.timezone('Asia/Kolkata')

# The 3 trades in question
# (ticker_nse, yahoo_suffix, direction, entry_time_IST, exit_time_IST, entry_price, exit_price)
trades = [
    ('BAJFINANCE', 'BAJFINANCE.NS', 'LONG',  '2026-03-12 10:03:18', '2026-03-12 10:31:42', 877.60, 873.30),
    ('M&M',        'M&M.NS',        'LONG',  '2026-03-12 10:03:19', '2026-03-12 10:31:43', 3068.80, 3056.10),
    ('COLPAL',     'COLPAL.NS',     'LONG',  '2026-03-12 10:03:22', '2026-03-12 10:31:44', 2019.10, 2008.20),
]

print(f"\n{'='*90}")
print(f"POST-EXIT PRICE ANALYSIS — Did TIME_STOP_CHECK fire too early?")
print(f"{'='*90}\n")

for ticker, yahoo, direction, entry_str, exit_str, entry_px, exit_px in trades:
    entry_dt = IST.localize(datetime.strptime(entry_str, '%Y-%m-%d %H:%M:%S'))
    exit_dt  = IST.localize(datetime.strptime(exit_str,  '%Y-%m-%d %H:%M:%S'))

    # Fetch 2m candles for the day
    start = IST.localize(datetime(2026, 3, 12, 9, 0, 0))
    end   = IST.localize(datetime(2026, 3, 12, 15, 30, 0))

    print(f"Fetching {yahoo}...")
    df = yf.download(yahoo, start=start, end=end, interval='2m', progress=False)

    if df.empty:
        print(f"  No data returned for {yahoo}")
        continue

    df.index = df.index.tz_convert(IST)

    # Get candles after exit
    post = df[df.index >= exit_dt]

    print(f"\n{ticker} {direction} | Entry={entry_px} @ {entry_str[11:]} | Exit={exit_px} @ {exit_str[11:]}")
    print(f"  Gross P&L at exit: {(exit_px - entry_px) * (1 if direction=='LONG' else -1) * ([1 if direction=='LONG' else -1][0]):.2f}")
    print()
    print(f"  {'TIME':<8} {'CLOSE':>8} {'vs EXIT':>10} {'HELD LONGER P&L':>16} {'BETTER?'}")
    print(f"  {'-'*55}")

    prev_time = exit_dt
    for i, (ts, row) in enumerate(post.iterrows()):
        if i > 15:  # show 30 minutes post exit
            break
        mins_after = (ts - exit_dt).seconds // 60
        close = float(row['Close'])
        vs_exit = close - exit_px
        held_pnl = vs_exit * (1 if direction == 'LONG' else -1)
        better = '✅ BETTER' if held_pnl > 0 else '❌ WORSE'
        print(f"  +{mins_after:02d}m    {close:>8.2f} {vs_exit:>+10.2f} {held_pnl:>+16.2f}  {better}")

    # Summary
    if len(post) >= 5:
        close_30m = float(post.iloc[min(14, len(post)-1)]['Close'])
        held_pnl_30m = (close_30m - exit_px) * (1 if direction == 'LONG' else -1)
        print(f"\n  VERDICT: Holding 30m more would have {'GAINED' if held_pnl_30m > 0 else 'LOST'} ₹{abs(held_pnl_30m):.2f} per share")
        qty_map = {'BAJFINANCE': 17, 'M&M': 4, 'COLPAL': 5}
        qty = qty_map.get(ticker, 1)
        total = held_pnl_30m * qty
        print(f"  At qty {qty}: {'GAINED' if total > 0 else 'LOST'} ₹{abs(total):.2f} total by exiting early")
    print()

print(f"{'='*90}")
print("CONCLUSION: Trades marked BETTER = TIME_STOP_CHECK was premature")
print(f"{'='*90}\n")
