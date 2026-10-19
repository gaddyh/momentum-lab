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
momentum-lab mine --input data/raw/RKLB.parquet --latency 2 --horizon-hours 60 --min-window 30 --out results/rklb
```

PowerShell activation: `.venv\Scripts\Activate.ps1`. API history entitlement required; use your own 1-minute CSV/Parquet if you do not have it.

### Input format
CSV/Parquet: `ts,symbol,open,high,low,close,volume`. UTC timestamps at **start** of the 1-minute candle. `ts` must be parseable as UTC. Inputs must be adjusted consistently for corporate actions. Never put paid vendor raw data or API keys in git.

### Outputs
- `candidate_outcomes.parquet`: each candidate minute's hindsight target/stop result.
- `opportunity_windows.csv`: consecutive successful hindsight minutes; `gold` marks >=30 minute opportunity windows.

**Do not interpret the fraction of winning candidate minutes as trade win-rate**: nearby minutes share the same future price path. Labels do not constitute entry signals.

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

End time is **exclusive**. An IEX response is not consolidated whole-market volume; SIP entitlement may be required for representative bars. This adds **data collection only**: the existing `mine` command assumes 1-minute bars. Do **not** mine these hourly bars with the minute-specific miner. Next milestone: bar-size-aware retrospective miner with realistic hourly entry timing and unknown within-bar barrier ordering.
