# Momentum Lab

A reproducible research project for short-lived stock momentum and **attention propagation**. Current stage: **retrospective opportunity mining**, *not* a predictive trading system.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
pytest -q
export MASSIVE_API_KEY='your_key'
momentum-lab fetch --symbol RKLB --start 2025-01-01 --end 2025-03-31 --out data/raw/RKLB.parquet
momentum-lab mine --input data/raw/RKLB.parquet --timeframe 1m --latency 2 --horizon-hours 60 --min-window 30 --out results/rklb
```

For hourly Alpaca bars, install the portable explorer extra and run:

```bash
pip install -e '.[dev,explorer]'
momentum-lab mine --input data/raw/RKLB_hour.csv --timeframe 1h \
  --target 0.20 --stop 0.08 --horizon-hours 60 --latency 2 \
  --out results/rklb_hourly
```

This writes `candidate_outcomes.parquet`, `opportunity_windows.csv`,
`research_summary.json`, and a local interactive `explorer.html` report.

PowerShell activation: `.venv\Scripts\Activate.ps1`. API history entitlement required; use your own 1-minute CSV/Parquet if you do not have it.

### Input format
CSV/Parquet: `ts,symbol,open,high,low,close,volume`. UTC timestamps at **start** of the 1-minute candle. `ts` must be parseable as UTC. Inputs must be adjusted consistently for corporate actions. Never put paid vendor raw data or API keys in git.

### Outputs
- `candidate_outcomes.parquet`: one row per eligible decision, including successful and failed outcomes, exact UTC timestamps, entry delay, ambiguity, and coverage flags.
- `opportunity_windows.csv`: correlated successful decision episodes. Hourly `gold` requires two consecutive eligible hourly decisions and is an hourly proxy, never a verified 30-minute window.
- `research_summary.json`: aggregate counts by symbol and calendar month, duration distribution, and median time to target.
- `explorer.html`: static interactive Plotly report with an episode selector and successful, failed, ambiguous, and censored candidates.

Hourly candles cannot resolve a two-minute execution delay. The hourly miner therefore enters at the first subsequent candle open and records the actual delay; this is a coarse research proxy, not a two-minute execution simulation. The 60-hour horizon is elapsed calendar time, including nights and weekends, while missing bars never prove that a barrier was not crossed.

### Market-wide pilot (Momentum Lab 006)

Create and freeze a reproducible universe snapshot. Alpaca Assets is only an asset-discovery source; it does not rank historical liquidity. A size-limited snapshot therefore requires a separate CSV containing an explicitly named liquidity metric such as `dollar_volume`:

```bash
momentum-lab universe --out data/universes --limit 500 --liquidity-file liquidity.csv
```

Without `--liquidity-file`, the command creates an unranked asset-discovery snapshot. The generated snapshot records its source, selection criteria, timestamp, and survivorship-bias warning. Current Alpaca active assets are not a point-in-time or delisted universe and must not be described as representative.

Download immutable monthly chunks and resume safely after interruption:

```bash
momentum-lab fetch-market --universe data/universes/universe_<snapshot-id>.csv \
  --start 2025-01-01 --end 2025-04-01 --feed iex --out data/raw/market
```

Each chunk is checksummed and recorded in `manifest.json`; `--force-refresh` is required to replace an existing chunk. Run the unchanged Golden Miner across the frozen universe:

```bash
momentum-lab mine-market --universe data/universes/universe_<snapshot-id>.csv \
  --data data/raw/market --out results/market-pilot
```

The market-wide output includes all candidate outcomes, opportunity windows, retrospective rankings, run provenance, and `research_summary.json`. The summary reports Golden Opportunity counts, failed/censored/ambiguous cases, symbol and calendar-period distributions, window durations, and time-to-target. Rankings are hindsight-only and are not trading signals.

**Do not interpret the fraction of winning candidate minutes or hours as trade win-rate**: nearby decisions share the same future price path and are correlated observations. Labels do not constitute entry signals.

## Modules
- `miners/golden.py`: existing v005 prototype transplanted into a package, with minute OHLC conservative ambiguity treatment.
- `collectors/massive.py`: minute history fetcher wrapper; API key required.
- `features/attention.py`: normalized timestamp-aware event schema only (collector not implemented).
- `features/contagion.py`: descriptive peer breadth helper (no predictive claim).
- `research/`: reserved for walk-forward replay.
- `docs/research_protocol.md`: fixed research protocol and next milestones.

## Limits / important caveats
- Minute candles have no bid/ask, so this is **not** an executable fill simulation. No spread, fees, market impact, halts or liquidity guarantees.
- **Gold is retrospective**. A valid predictive system needs a timestamp-clean feature pipeline, matched failures and chronological out-of-sample testing.
- The current downloader uses split-adjusted aggregates. Historical point-in-time treatment and delisted universe are not yet implemented.
- No news or social data is fetched yet; repository intentionally makes no unsupported data claims.
- Research software only; no live trading endpoints or orders.

## Publish as private repository
```bash
git init
git add .
git commit -m 'Initial Momentum Lab research scaffold'
git branch -M main
# Create a private GitHub repository called momentum-lab, then:
git remote add origin git@github.com:YOUR_USERNAME/momentum-lab.git
git push -u origin main
```

## Alpaca hourly historical bars (experimental)

Store credentials **locally** in an ignored `.env` file:

```dotenv
ALPACA_API_KEY_ID=your_key
ALPACA_API_SECRET_KEY=your_secret
```

Load them before invoking the CLI (the package does not automatically load `.env`):

```bash
set -a; source .env; set +a
momentum-lab fetch-alpaca --symbol RKLB --start 2025-01-01 --end 2025-04-01 --feed iex --out data/raw/RKLB_hour.csv
```

End time is **exclusive**. An IEX response is not consolidated whole-market volume; SIP entitlement may be required for representative bars. Hourly mining uses explicit next-candle-open proxy semantics and unknown within-bar barrier ordering. It is retrospective discovery only, not a real-time signal or live trading simulation.
