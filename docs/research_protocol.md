# Research protocol v0.2

## Research question
Can observable, timestamp-clean features improve the ex-ante probability of a **+20%** target before a **-8%** barrier, within **60 elapsed calendar hours**, after a defined reaction latency? The current miner is a retrospective opportunity-discovery tool, not a predictive system.

## Hindsight discovery vs prediction
- The miner computes outcome labels using future OHLCV data solely to discover historical examples.
- Real-time/replay features may only use information whose `first_seen_at` is at or before `decision_at`; future labels and episode membership must never enter feature construction.
- Every output preserves UTC `decision_candle_at`, `decision_at`, `entry_at`, and `exit_at`. A decision is available only after the observed candle closes.
- Never train/test on randomly shuffled minutes or hours. An episode and overlapping future horizons must not cross a train/test boundary; embargo at least 60h and account for overlapping symbols.

## Timeframe and entry semantics
- Input `ts` is the UTC start of the OHLCV candle. Supported timeframes are `1m` and `1h`.
- For 1-minute data, a decision is available after the completed minute candle; the requested latency is applied and the first eligible minute-bar open is used, subject to the existing fill-wait rule.
- For 1-hour data, the desired two-minute reaction cannot be resolved by hourly OHLCV. The miner uses the first subsequent candle's open as a **coarse entry proxy**, records the actual delay from `decision_at`, and never presents it as a two-minute execution simulation.
- The horizon is 60 elapsed calendar hours. Nights, weekends and market closures consume horizon time even though no candles are emitted.
- Missing candles are not synthetic flat periods and never prove that a target or stop was not crossed. If future coverage is insufficient to evaluate the horizon, the outcome is `censored`, not `timeout`.
- An open beyond a barrier is labeled using the actual open and a gap outcome; the threshold price is never treated as an executable fill.

## Barrier measurement
- A confirmed `target` requires +20% before -8% within the evaluated horizon.
- A confirmed `stop` requires -8% first. `target_gap` and `stop_gap` retain actual gap-open returns.
- If both barriers occur in one candle, the outcome is `ambiguous` and is not a confirmed success. Conservative and optimistic interpretations must remain separate from confirmed results.
- The miner records gross return, exact entry/exit timestamps and prices, elapsed hours, maximum adverse excursion where observable, `ambiguity_flag`, and `coverage_flag`.

## Opportunity episodes
- Candidate decisions overlap in their future paths and are correlated observations, not independent trades.
- Successful decisions are grouped only when their timestamps are consecutive at the configured candle cadence. A missing candle, overnight gap, weekend, or other session break starts a new episode; unrelated sessions are never joined merely because the same target was eventually reached.
- An hourly `gold` episode requires successful decisions across at least two consecutive eligible hourly candles. This is an hourly proxy and is not a verified 30-minute opportunity.
- A minute `gold` episode retains the original repeated-success definition of at least 30 minutes. It is still not 30 independent trades.
- Episode output reports unique episode IDs, qualifying decision count, elapsed duration, session coverage, entry range, time to target, adverse excursion, and whether the classification is hourly-proxy or minute-validated.

## Output integrity
- `candidate_outcomes.parquet` contains successful, failed, ambiguous, and censored eligible decisions.
- `opportunity_windows.csv` contains historical episodes, not a list of independent trades.
- `research_summary.json` reports outcome counts, episode counts, duration distribution, median time to target, and symbol/calendar-period breakdowns. It must not report overlapping candidate success fractions as strategy win rate.
- `explorer.html` is a static hindsight-only report. It must show failures and censored cases alongside successful episodes, with candle boundaries, decision timestamps, entry markers, target/stop levels, and exact episode duration.

## Data and market limitations
- Measure a complete point-in-time universe, including delisted tickers, corporate actions, halts, missing bars, splits and historical peer memberships, before treating results as investable.
- Alpaca IEX data is not consolidated whole-market volume; SIP entitlement and feed choice affect representativeness.
- Hourly OHLC cannot identify intrabar order, exact two-minute fills, or a 30-minute sustained opportunity. These limitations are explicit labels, not assumptions hidden in the miner.
- Do not add ML, sentiment, news/social collection, or live order execution to this research foundation.

## Behavioral research readiness
When behavioral features are added, retain event-level `published_at`, `first_seen_at`, source, external ID, privacy-safe participant/publisher identifiers, and deduplication rules. A feature is eligible at a decision point only if it was observable by that timestamp. Do not claim news/Reddit coverage until collectors are implemented and measured.

## Next milestones
1. Validate 1m and 1h labels across a diverse point-in-time universe.
2. Inspect hourly episodes and matched failures with the static explorer.
3. Add timestamped causal attention sources with first-seen provenance.
4. Build a strict chronological replay with embargoed out-of-sample evaluation.
5. Evaluate paper trading only after independent validation; no live orders in this project.
