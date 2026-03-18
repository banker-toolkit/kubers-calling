import sqlite3
from datetime import datetime
from collections import defaultdict

conn = sqlite3.connect('database/kubers_live.db')
conn.row_factory = sqlite3.Row

# Get all 3m candles
print("Fetching candles...")
rows = conn.execute("""
    SELECT ticker, time FROM historical_candles 
    WHERE timeframe='3m'
    ORDER BY ticker, time
""").fetchall()

# Build per-ticker bucket set
ticker_buckets = defaultdict(set)
for r in rows:
    try:
        dt = datetime.fromtimestamp(r['time'])
        bucket = dt.strftime("%H:%M")
        ticker_buckets[r['ticker']].add(bucket)
    except:
        continue

# All valid NSE 3m trading buckets 09:15 to 15:27
from datetime import time as dtime, timedelta
all_buckets = set()
t = dtime(9, 15)
end = dtime(15, 27)
while t <= end:
    all_buckets.add(f"{t.hour:02d}:{t.minute:02d}")
    dt = datetime.combine(datetime.today(), t) + timedelta(minutes=3)
    t = dt.time()

total_buckets = len(all_buckets)
print(f"Total expected trading buckets per ticker: {total_buckets}")
print(f"Total tickers in DB: {len(ticker_buckets)}\n")

# Categorise each ticker
perfect = []      # >= 90% coverage
good = []         # 70-90%
thin = []         # 30-70%
critical = []     # < 30% (will fire false Z=10 frequently)

for ticker, buckets in ticker_buckets.items():
    covered = len(buckets & all_buckets)
    pct = 100 * covered / total_buckets
    missing = all_buckets - buckets
    if pct >= 90:
        perfect.append((ticker, pct, covered, len(missing)))
    elif pct >= 70:
        good.append((ticker, pct, covered, len(missing)))
    elif pct >= 30:
        thin.append((ticker, pct, covered, len(missing)))
    else:
        critical.append((ticker, pct, covered, len(missing)))

print(f"{'='*60}")
print(f"COVERAGE SUMMARY")
print(f"{'='*60}")
print(f"  ✅ FULL  (>=90% buckets): {len(perfect):>4} tickers")
print(f"  🟡 GOOD  (70-90%):        {len(good):>4} tickers")
print(f"  🟠 THIN  (30-70%):        {len(thin):>4} tickers  ← Z unreliable")
print(f"  🔴 CRIT  (<30%):          {len(critical):>4} tickers  ← Z=10 factory")
print(f"{'='*60}")

if critical:
    print(f"\n🔴 CRITICAL tickers (will fire Z=10 on almost every signal):")
    print(f"  {'TICKER':<15} {'COVERAGE':>9} {'BUCKETS':>8} {'MISSING':>8}")
    print(f"  {'-'*45}")
    for t, pct, cov, mis in sorted(critical, key=lambda x: x[1]):
        print(f"  {t:<15} {pct:>8.1f}% {cov:>8} {mis:>8}")

if thin:
    print(f"\n🟠 THIN tickers (Z unreliable for missing slots):")
    print(f"  {'TICKER':<15} {'COVERAGE':>9} {'BUCKETS':>8} {'MISSING':>8}")
    print(f"  {'-'*45}")
    for t, pct, cov, mis in sorted(thin, key=lambda x: x[1])[:20]:
        print(f"  {t:<15} {pct:>8.1f}% {cov:>8} {mis:>8}")
    if len(thin) > 20:
        print(f"  ... and {len(thin)-20} more")

# Which time slots are most commonly missing across all tickers
bucket_coverage = defaultdict(int)
for ticker, buckets in ticker_buckets.items():
    for b in buckets:
        if b in all_buckets:
            bucket_coverage[b] += 1

total_tickers = len(ticker_buckets)
missing_slots = [(b, total_tickers - bucket_coverage.get(b,0)) 
                 for b in sorted(all_buckets)]
worst_slots = sorted(missing_slots, key=lambda x: -x[1])[:10]

print(f"\n{'='*60}")
print(f"TOP 10 TIME SLOTS WITH MOST MISSING DATA")
print(f"{'='*60}")
print(f"  {'BUCKET':<8} {'MISSING TICKERS':>15} {'COVERAGE %':>12}")
print(f"  {'-'*40}")
for slot, missing in worst_slots:
    pct = 100*(total_tickers - missing)/total_tickers
    print(f"  {slot:<8} {missing:>15} {pct:>11.1f}%")

conn.close()