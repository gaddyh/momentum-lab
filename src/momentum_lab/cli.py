"""Momentum Lab command line entry point."""
import argparse
import os
from pathlib import Path
import pandas as pd
from momentum_lab.miners.golden import fetch_massive, load_bars, mine_symbol

def main():
    parser = argparse.ArgumentParser(prog="momentum-lab")
    subs = parser.add_subparsers(dest="command", required=True)
    fetch = subs.add_parser("fetch", help="Fetch minute bars using Massive")
    for flag in ("symbol", "start", "end", "out"):
        fetch.add_argument(f"--{flag}", required=True)
    mine = subs.add_parser("mine", help="Mine retrospective gold opportunity windows")
    mine.add_argument("--input", required=True)
    mine.add_argument("--out", default="results")
    mine.add_argument("--symbol")
    mine.add_argument("--latency", type=int, default=2)
    mine.add_argument("--horizon-hours", type=int, default=60)
    mine.add_argument("--min-window", type=int, default=30)
    mine.add_argument("--candidate-step", type=int, default=1)
    args = parser.parse_args()
    if args.command == "fetch":
        key = os.environ.get("MASSIVE_API_KEY")
        if not key:
            parser.error("Set MASSIVE_API_KEY first")
        fetch_massive(args.symbol, args.start, args.end, key, Path(args.out))
        return
    df = load_bars(Path(args.input), args.symbol)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    event_frames, window_frames = [], []
    for symbol, bars in df.groupby("symbol"):
        events, windows = mine_symbol(
            bars, latency_min=args.latency,
            horizon_h=args.horizon_hours,
            min_window_min=args.min_window,
            candidate_step=args.candidate_step,
        )
        print(f"{symbol}: {len(events)} candidates, {len(windows)} windows, "
              f"{int(windows.gold.sum()) if len(windows) else 0} gold")
        if not events.empty:
            event_frames.append(events)
        if not windows.empty:
            window_frames.append(windows)
    if event_frames:
        pd.concat(event_frames, ignore_index=True).to_parquet(out / "candidate_outcomes.parquet", index=False)
    if window_frames:
        pd.concat(window_frames, ignore_index=True).to_csv(out / "opportunity_windows.csv", index=False)
    print("Gold means hindsight success; it is NOT a real-time trading signal.")

if __name__ == "__main__":
    main()
