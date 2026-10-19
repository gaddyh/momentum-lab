"""Momentum Lab command line entry point."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import pandas as pd

from momentum_lab.collectors.alpaca import fetch_alpaca
from momentum_lab.miners.golden import fetch_massive, load_bars, mine_opportunities


def _summary(events: pd.DataFrame, windows: pd.DataFrame) -> dict:
    outcomes = events.outcome.value_counts().to_dict() if not events.empty else {}
    period = {}
    if not events.empty:
        period_frame = events.assign(period=events.decision_at.dt.strftime("%Y-%m"))
        for key, group in period_frame.groupby("period"):
            period[key] = {
                "candidates": len(group),
                "confirmed_successes": int((group.outcome == "target").sum()),
                "stops": int(group.outcome.isin(["stop", "stop_gap"]).sum()),
                "timeouts": int((group.outcome == "timeout").sum()),
                "ambiguous": int((group.outcome == "ambiguous").sum()),
                "censored": int((group.outcome == "censored").sum()),
            }
    by_symbol = {}
    if not events.empty:
        for symbol, group in events.groupby("symbol"):
            by_symbol[symbol] = {
                "candidates": len(group),
                "confirmed_successes": int((group.outcome == "target").sum()),
                "stops": int(group.outcome.isin(["stop", "stop_gap"]).sum()),
                "timeouts": int((group.outcome == "timeout").sum()),
                "ambiguous": int((group.outcome == "ambiguous").sum()),
                "censored": int((group.outcome == "censored").sum()),
            }
    target_times = events.loc[events.outcome == "target", "elapsed_hours"] if not events.empty else pd.Series(dtype=float)
    return {
        "retrospective_only": True,
        "candidates": len(events),
        "confirmed_successes": int((events.outcome == "target").sum()) if not events.empty else 0,
        "stops": int(events.outcome.isin(["stop", "stop_gap"]).sum()) if not events.empty else 0,
        "timeouts": int((events.outcome == "timeout").sum()) if not events.empty else 0,
        "ambiguous": int((events.outcome == "ambiguous").sum()) if not events.empty else 0,
        "censored": int((events.outcome == "censored").sum()) if not events.empty else 0,
        "outcome_counts": {str(k): int(v) for k, v in outcomes.items()},
        "gold_episodes": int(windows.gold.sum()) if not windows.empty else 0,
        "episode_count": len(windows),
        "opportunity_duration_hours": {
            "count": int(windows.duration_hours.count()) if not windows.empty else 0,
            "min": float(windows.duration_hours.min()) if not windows.empty else None,
            "median": float(windows.duration_hours.median()) if not windows.empty else None,
            "max": float(windows.duration_hours.max()) if not windows.empty else None,
        },
        "median_hours_to_target": float(target_times.median()) if not target_times.empty else None,
        "by_symbol": by_symbol,
        "by_calendar_period": period,
        "warning": "Overlapping candidate points are correlated observations, not independent trades.",
    }


def _write_frame(frame: pd.DataFrame, path: Path, columns: list[str]) -> None:
    frame = frame.copy()
    for column in columns:
        if column not in frame:
            frame[column] = pd.Series(dtype="object")
    frame[columns].to_parquet(path, index=False) if path.suffix == ".parquet" else frame[columns].to_csv(path, index=False)


def main():
    parser = argparse.ArgumentParser(prog="momentum-lab")
    subs = parser.add_subparsers(dest="command", required=True)
    fetch = subs.add_parser("fetch", help="Fetch minute bars using Massive")
    for flag in ("symbol", "start", "end", "out"):
        fetch.add_argument(f"--{flag}", required=True)
    alpaca = subs.add_parser("fetch-alpaca", help="Fetch historical hourly bars from Alpaca")
    for flag in ("symbol", "start", "end", "out"):
        alpaca.add_argument(f"--{flag}", required=True)
    alpaca.add_argument("--feed", choices=("iex", "sip"), default="iex")
    mine = subs.add_parser("mine", help="Mine retrospective opportunity windows")
    mine.add_argument("--input", required=True)
    mine.add_argument("--out", default="results")
    mine.add_argument("--symbol")
    mine.add_argument("--timeframe", choices=("1m", "1h"), default="1m")
    mine.add_argument("--target", type=float, default=.20)
    mine.add_argument("--stop", type=float, default=.08)
    mine.add_argument("--latency", type=int, default=2)
    mine.add_argument("--horizon-hours", type=int, default=60)
    mine.add_argument("--min-window", type=int, default=30)
    mine.add_argument("--candidate-step", type=int, default=1)
    mine.add_argument("--no-explorer", action="store_true")
    args = parser.parse_args()
    if args.command == "fetch":
        key = os.environ.get("MASSIVE_API_KEY")
        if not key:
            parser.error("Set MASSIVE_API_KEY first")
        fetch_massive(args.symbol, args.start, args.end, key, Path(args.out))
        return
    if args.command == "fetch-alpaca":
        data = fetch_alpaca(args.symbol, args.start, args.end, Path(args.out), feed=args.feed)
        print(f"Downloaded {len(data)} hourly bars for {args.symbol} ({args.feed})")
        return

    bars = load_bars(Path(args.input), args.symbol)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    event_frames, window_frames = [], []
    for symbol, symbol_bars in bars.groupby("symbol"):
        events, windows = mine_opportunities(
            symbol_bars, timeframe=args.timeframe, target_pct=args.target,
            stop_pct=args.stop, horizon_hours=args.horizon_hours,
            latency_minutes=args.latency, candidate_step=args.candidate_step,
            min_window_min=args.min_window,
        )
        event_frames.append(events)
        if not windows.empty:
            window_frames.append(windows)
        print(f"{symbol}: {len(events)} candidates, {len(windows)} episodes, "
              f"{int(windows.gold.sum()) if len(windows) else 0} gold")
    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    windows = pd.concat(window_frames, ignore_index=True) if window_frames else pd.DataFrame()
    event_columns = ["symbol", "timeframe", "decision_at", "entry_at", "entry_price", "exit_at",
                     "exit_price", "outcome", "success", "gross_return", "conservative_return",
                     "optimistic_return", "elapsed_hours", "max_adverse_excursion", "ambiguity_flag",
                     "coverage_flag", "entry_delay_minutes",
                     "entry_semantics", "decision_candle_at", "last_close_before_decision",
                     "decision_bar_dollar_volume"]
    window_columns = ["episode_id", "symbol", "timeframe", "first_decision", "last_decision",
                      "qualifying_decision_points", "duration_hours", "session_coverage_hours",
                      "min_entry_price", "max_entry_price", "median_hours_to_target",
                      "max_adverse_excursion", "confirmed_target_count", "ambiguous_count", "gold",
                      "gold_basis", "hourly_proxy", "minute_validated"]
    _write_frame(events, out / "candidate_outcomes.parquet", event_columns)
    _write_frame(windows, out / "opportunity_windows.csv", window_columns)
    (out / "research_summary.json").write_text(json.dumps(_summary(events, windows), indent=2) + "\n")
    if not args.no_explorer:
        from momentum_lab.research.report import write_explorer
        write_explorer(bars, events, windows, out / "explorer.html", args.target, args.stop)
    print("Gold means hindsight success; it is NOT a real-time trading signal.")


if __name__ == "__main__":
    main()
