# Research protocol v0.1

## Research question
Can observable features (price, volume, unique participants, independent news publishers, peer breadth) improve the ex-ante probability of a **+20%** target before a **-8%** barrier, within **60 elapsed hours**, using 2-minute reaction latency?

## Hindsight discovery vs prediction
- Gold miner computes outcome labels using *future* data, solely to discover examples.
- Real-time/replay features may only use events with `first_seen_at <= decision_at`.
- Never train/test on randomly shuffled minutes. An episode and overlapping future horizons must not cross a train/test boundary; embargo at least 60h and account for overlapping symbols.
- Measure complete universe including delisted tickers, corporate actions, halts, missing bars, splits, and historical peer memberships before treating results as investable.

## Measurement
- The latency begins after a completed one-minute bar; 2 minutes after that, fill is approximated at the first available minute-bar open (within 10m).
- A +20% gain before -8% within 60 calendar hours is a hindsight success. Ambiguous within-minute target and stop count as a failure.
- A `gold` window is at least 30 minutes of repeated successful hindsight candidate decisions. This does not mean 30 independent trades.
- Report event-level recall, candidate-level precision, precision by sector/year, net expectancy after cost/slippage, realized execution rate, exposure, and max drawdown.

## Social data
Track `published_at`, `first_seen_at`, `source`, `external_id`, `publisher_id` or privacy-safe author hash, and story/thread deduplication. Do not claim news/Reddit coverage until collectors are implemented and measured. Count new *unique* participants and distinct publishers, not only sentiment.

## Next milestones
1. Fetch validated real 1m history for a diverse point-in-time universe.
2. Inspect 30-minute gold windows and matched failures by market regime.
3. Connect one actual timestamped news source (with first-seen provenance).
4. Implement causal feature builder and strict chronological replay.
5. Evaluate independent out-of-sample and paper trade before any real orders.
