import sqlite3, datetime

conn = sqlite3.connect('database/kubers_live.db')

# TIME_STOP_CHECK trades - key ones to analyse
# (ticker, direction, entry_price, entry_time, hold_min, exit_price)
trades = [
    ('WIPRO',      'SHORT', 201.46,   '2026-03-12T09:48:23', 20.0, 201.45),
    ('BAJFINANCE', 'LONG',  877.6,    '2026-03-12T10:03:18', 28.4, 873.3),
    ('M&M',        'LONG',  3068.8,   '2026-03-12T10:03:19', 28.4, 3056.1),
    ('COLPAL',     'LONG',  2019.1,   '2026-03-12T10:03:22', 28.4, 2008.2),
    ('IRCTC',      'LONG',  523.65,   '2026-03-12T10:03:26', 28.3, 526.25),
    ('GODREJCP',   'LONG',  1062.0,   '2026-03-12T10:03:37', 28.2, 1058.6),
    ('HCLTECH',    'SHORT', 1352.4,   '2026-03-12T10:45:10', 20.2, 1361.4),
    ('TECHM',      'SHORT', 1338.3,   '2026-03-12T10:45:12', 20.2, 1342.1),
    ('LTTS',       'SHORT', 3149.7,   '2026-03-12T14:14:21', 20.1, 3146.1),
    ('TCS',        'SHORT', 2450.6,   '2026-03-12T14:12:56', 20.2, 2450.3),
    ('HINDUNILVR', 'SHORT', 2119.7,   '2026-03-12T14:12:45', 20.1, 2123.1),
    ('BALKRISIND', 'SHORT', 2236.7,   '2026-03-12T14:52:28', 20.3, 2278.9),
    ('MOTHERSON',  'SHORT', 119.45,   '2026-03-12T14:52:28', 20.2, 120.27),
    ('DIXON',      'SHORT', 10690.0,  '2026-03-12T14:52:28', 20.5, 10810.0),
    ('HINDALCO',   'SHORT', 963.0,    '2026-03-12T14:54:14', 20.1, 971.25),
    ('PERSISTENT', 'SHORT', 4698.0,   '2026-03-12T14:52:28', 20.2, 4717.2),
]

IST_OFFSET = 19800  # 5h30m in seconds

print(f"{'TICKER':<12} {'DIR':<6} {'ENTRY':>8} {'EXIT':>8} {'POST15m':>9} {'POST30m':>9} {'POST60m':>9} {'VERDICT'}")
print("-" * 85)

for ticker, direction, entry, entry_time, hold_min, exit_price in trades:
    et = datetime.datetime.fromisoformat(entry_time)
    exit_ts = int((et + datetime.timedelta(minutes=hold_min)).timestamp()) - IST_OFFSET

    results = {}
    for label, offset in [('p15', 900), ('p30', 1800), ('p60', 3600)]:
        ts = exit_ts + offset
        row = conn.execute(
            'SELECT close FROM historical_candles WHERE ticker=? AND timeframe="3m" AND time<=? ORDER BY time DESC LIMIT 1',
            (ticker, ts)
        ).fetchone()
        results[label] = row[0] if row else None

    sign = 1 if direction == 'SHORT' else -1

    def fmt_pnl(future_price):
        if future_price is None:
            return '  N/A'
        # positive = would have profited by holding
        pnl = (exit_price - future_price) * sign
        return f'{pnl:+.1f}'

    p15_pnl = fmt_pnl(results['p15'])
    p30_pnl = fmt_pnl(results['p30'])
    p60_pnl = fmt_pnl(results['p60'])

    # Verdict: was the exit premature?
    p30_val = (exit_price - results['p30']) * sign if results['p30'] else None
    if p30_val is None:
        verdict = '?'
    elif p30_val > 5:
        verdict = 'PREMATURE'
    elif p30_val < -5:
        verdict = 'GOOD EXIT'
    else:
        verdict = 'NEUTRAL'

    print(f"{ticker:<12} {direction:<6} {entry:>8.2f} {exit_price:>8.2f} {p15_pnl:>9} {p30_pnl:>9} {p60_pnl:>9} {verdict}")

conn.close()
