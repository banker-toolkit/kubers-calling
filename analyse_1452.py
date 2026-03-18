"""
Analyse the 14:52 SHORT wave - what were candles doing at entry?
Were these volume spikes on UP candles or DOWN candles?
Run: python3 analyse_1452.py
"""
import yfinance as yf
from datetime import datetime
import pytz

IST = pytz.timezone('Asia/Kolkata')

# 14:52 SHORT entries that lost money
trades = [
    ('BALKRISIND', 'BALKRISIND.NS', 2236.7),
    ('DIXON',      'DIXON.NS',      10690.0),
    ('MOTHERSON',  'MOTHERSON.NS',  119.45),
    ('HINDALCO',   'HINDALCO.NS',   963.0),
    ('PERSISTENT', 'PERSISTENT.NS', 4698.0),
    ('IPCALAB',    'IPCALAB.NS',    1550.1),
    ('HCLTECH',    'HCLTECH.NS',    1355.0),
    ('TECHM',      'TECHM.NS',      1345.4),
    ('NATIONALUM', 'NATIONALUM.NS', 407.25),
    ('INFY',       'INFY.NS',       1265.1),
]

start = IST.localize(datetime(2026, 3, 12, 14, 0, 0))
end   = IST.localize(datetime(2026, 3, 12, 15, 30, 0))

print(f"\n{'='*85}")
print(f"14:52 SHORT WAVE — What were candles doing at entry?")
print(f"{'='*85}")
print(f"{'TICKER':<12} {'14:30':>8} {'14:40':>8} {'14:50':>8} {'14:52 ENTRY':>12} {'CANDLE DIR':>12} {'TREND'}")
print(f"{'-'*85}")

for ticker, yahoo, entry_px in trades:
    df = yf.download(yahoo, start=start, end=end, interval='2m', progress=False)
    if df.empty:
        print(f"{ticker:<12} NO DATA")
        continue
    df.index = df.index.tz_convert(IST)

    def get_close(target_time_str):
        target = IST.localize(datetime.strptime(f'2026-03-12 {target_time_str}', '%Y-%m-%d %H:%M'))
        subset = df[df.index <= target]
        return float(subset.iloc[-1]['Close']) if not subset.empty else None

    p1430 = get_close('14:30')
    p1440 = get_close('14:40')
    p1450 = get_close('14:50')
    p1452 = get_close('14:52')

    if not all([p1430, p1440, p1450, p1452]):
        print(f"{ticker:<12} INCOMPLETE DATA")
        continue

    # Was price trending UP or DOWN into entry?
    trend_30_50 = p1450 - p1430
    candle_dir = 'UP' if trend_30_50 > 0 else 'DOWN'
    trend_str = f'{trend_30_50:+.2f}'

    # Was the entry in line with trend (SHORT into DOWN) or against (SHORT into UP)?
    alignment = '✅ WITH TREND' if candle_dir == 'DOWN' else '❌ AGAINST TREND'

    print(f"{ticker:<12} {p1430:>8.2f} {p1440:>8.2f} {p1450:>8.2f} {p1452:>12.2f} {candle_dir:>12} {alignment}")

print(f"\n{'='*85}")
print("AGAINST TREND = SHORT entered while price was rising = bad entry")
print("WITH TREND    = SHORT entered while price was falling = valid entry")
print(f"{'='*85}\n")
