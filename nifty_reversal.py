"""
What caused the reversal after 14:52 entries?
Was it a NIFTY-wide reversal or stock-specific?
Run: python3 nifty_reversal.py
"""
import yfinance as yf
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')
start = IST.localize(datetime(2026, 3, 12, 14, 0, 0))
end   = IST.localize(datetime(2026, 3, 12, 15, 30, 0))

# Check NIFTY first
print("\n=== NIFTY 2m CANDLES 14:00-15:20 ===")
nifty = yf.download('^NSEI', start=start, end=end, interval='2m', progress=False)
if not nifty.empty:
    nifty.index = nifty.index.tz_convert(IST)
    for ts, row in nifty.iterrows():
        t = ts.strftime('%H:%M')
        if t >= '14:20':
            c = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
            o = float(row['Open'].iloc[0]) if hasattr(row['Open'], 'iloc') else float(row['Open'])
            v = float(row['Volume'].iloc[0]) if hasattr(row['Volume'], 'iloc') else float(row['Volume'])
            dir = '▲' if c > o else '▼'
            print(f"  {t}  {c:>9.2f}  {dir}  vol={v:>10.0f}")

# Check a sample stock - BALKRISIND reversal timing
print("\n=== BALKRISIND 2m — entry 14:52 @ 2236.7, exited 15:12 @ 2278.9 ===")
bk = yf.download('BALKRISIND.NS', start=start, end=end, interval='2m', progress=False)
if not bk.empty:
    bk.index = bk.index.tz_convert(IST)
    for ts, row in bk.iterrows():
        t = ts.strftime('%H:%M')
        if t >= '14:48':
            c = float(row['Close'].iloc[0]) if hasattr(row['Close'], 'iloc') else float(row['Close'])
            o = float(row['Open'].iloc[0]) if hasattr(row['Open'], 'iloc') else float(row['Open'])
            v = float(row['Volume'].iloc[0]) if hasattr(row['Volume'], 'iloc') else float(row['Volume'])
            dir = '▲' if c > o else '▼'
            marker = ' ← ENTRY' if t == '14:52' else ' ← EXIT' if t == '15:12' else ''
            print(f"  {t}  {c:>8.2f}  {dir}  vol={v:>8.0f}{marker}")
