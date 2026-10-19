"""Batch retrospective mining and research summaries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

from momentum_lab.miners.golden import mine_opportunities

EVENT_COLUMNS = ["symbol", "timeframe", "decision_at", "entry_at", "entry_price", "exit_at", "exit_price", "outcome", "success",
                 "gross_return", "conservative_return", "optimistic_return", "elapsed_hours", "max_adverse_excursion",
                 "ambiguity_flag", "coverage_flag", "entry_delay_minutes", "entry_semantics", "decision_candle_at",
                 "last_close_before_decision", "decision_bar_dollar_volume"]
WINDOW_COLUMNS = ["episode_id", "symbol", "timeframe", "first_decision", "last_decision", "qualifying_decision_points", "duration_hours",
                 "session_coverage_hours", "min_entry_price", "max_entry_price", "median_hours_to_target", "max_adverse_excursion",
                 "confirmed_target_count", "ambiguous_count", "gold", "gold_basis", "hourly_proxy", "minute_validated"]


def load_metadata(path: Path | None) -> tuple[pd.DataFrame, str | None]:
    if path is None:
        return pd.DataFrame(columns=["symbol", "sector", "industry", "source", "as_of_date", "retrieved_at", "point_in_time"]), None
    frame = pd.read_csv(path)
    if "symbol" not in frame:
        raise ValueError("Metadata must contain symbol")
    for column in ("sector", "industry", "source", "as_of_date", "retrieved_at"):
        if column not in frame:
            frame[column] = pd.NA
    if "point_in_time" not in frame:
        frame["point_in_time"] = False
    frame["symbol"] = frame.symbol.astype(str).str.upper()
    version = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return frame[["symbol", "sector", "industry", "source", "as_of_date", "retrieved_at", "point_in_time"]].drop_duplicates("symbol"), version


def _with_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        if column not in result:
            result[column] = pd.Series(dtype="object")
    return result[columns]


def _counts(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"candidates": 0, "confirmed_successes": 0, "gold_episodes": 0, "stops": 0, "timeouts": 0, "ambiguous": 0, "censored": 0}
    return {"candidates": len(frame), "confirmed_successes": int((frame.outcome == "target").sum()),
            "stops": int(frame.outcome.isin(["stop", "stop_gap"]).sum()), "timeouts": int((frame.outcome == "timeout").sum()),
            "ambiguous": int((frame.outcome == "ambiguous").sum()), "censored": int((frame.outcome == "censored").sum())}


def research_summary(events: pd.DataFrame, windows: pd.DataFrame, manifest: list[dict] | None = None,
                     run_metadata: dict | None = None) -> dict:
    period, symbol = {}, {}
    if not events.empty:
        for name, group in events.assign(period=events.decision_at.dt.strftime("%Y-%m")).groupby("period"):
            period[name] = _counts(group)
        for name, group in events.groupby("symbol"):
            counts = _counts(group)
            counts["gold_episodes"] = int((windows.symbol == name).sum()) if not windows.empty else 0
            symbol[name] = counts
    target = events.loc[events.outcome == "target", "elapsed_hours"] if not events.empty else pd.Series(dtype=float)
    duration = windows.duration_hours if not windows.empty else pd.Series(dtype=float)
    result = {"retrospective_only": True, "run": run_metadata or {}, **_counts(events),
              "gold_episodes": int(windows.gold.sum()) if not windows.empty else 0, "episode_count": len(windows),
              "opportunity_window_duration_hours": {"count": int(duration.count()), "min": float(duration.min()) if len(duration) else None,
                                                     "median": float(duration.median()) if len(duration) else None, "max": float(duration.max()) if len(duration) else None},
              "time_to_target_hours": {"count": int(target.count()), "min": float(target.min()) if len(target) else None,
                                        "median": float(target.median()) if len(target) else None, "max": float(target.max()) if len(target) else None},
              "by_symbol": symbol, "by_calendar_period": period,
              "outcome_counts": {str(k): int(v) for k, v in events.outcome.value_counts().items()} if not events.empty else {},
              "manifest_status_counts": pd.Series([item.get("status") for item in (manifest or [])]).value_counts().to_dict(),
              "warning": "Overlapping candidate points are correlated observations, not independent trades; all metrics are retrospective."}
    return result


def rank_opportunities(windows: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    if windows.empty:
        return windows.copy()
    ranked = windows.copy()
    liquidity = events.groupby("symbol").decision_bar_dollar_volume.median().rename("median_decision_bar_dollar_volume")
    ranked = ranked.merge(liquidity, on="symbol", how="left")
    ranked["rank_duration"] = ranked.duration_hours.rank(ascending=False, method="dense")
    ranked["rank_time_to_target"] = ranked.median_hours_to_target.rank(ascending=True, method="dense")
    ranked["rank_liquidity"] = ranked.median_decision_bar_dollar_volume.rank(ascending=False, method="dense")
    ranked["hindsight_only"] = True
    ranked = ranked.sort_values(["gold", "median_hours_to_target", "duration_hours"], ascending=[False, True, False])
    return ranked.reset_index(drop=True)


def mine_market(bars_by_symbol: dict[str, pd.DataFrame], output: Path, snapshot_id: str,
                parameters: dict, metadata_path: Path | None = None, manifest: list[dict] | None = None) -> dict:
    output.mkdir(parents=True, exist_ok=True)
    event_frames, window_frames = [], []
    for symbol, bars in sorted(bars_by_symbol.items()):
        events, windows = mine_opportunities(bars, **parameters)
        event_frames.append(events)
        if not windows.empty:
            window_frames.append(windows)
    events = pd.concat(event_frames, ignore_index=True) if event_frames else pd.DataFrame()
    windows = pd.concat(window_frames, ignore_index=True) if window_frames else pd.DataFrame()
    metadata, metadata_version = load_metadata(metadata_path)
    metadata = metadata.rename(columns={"source": "metadata_source", "as_of_date": "metadata_as_of_date",
                                        "retrieved_at": "metadata_retrieved_at", "point_in_time": "metadata_point_in_time"})
    if not events.empty and not metadata.empty:
        events = events.merge(metadata, on="symbol", how="left")
    if not windows.empty and not metadata.empty:
        windows = windows.merge(metadata, on="symbol", how="left")
    events = _with_columns(events, EVENT_COLUMNS + ["sector", "industry", "metadata_source", "metadata_as_of_date", "metadata_retrieved_at", "metadata_point_in_time"])
    windows = _with_columns(windows, WINDOW_COLUMNS + ["sector", "industry", "metadata_source", "metadata_as_of_date", "metadata_retrieved_at", "metadata_point_in_time"])
    events.to_parquet(output / "candidate_outcomes.parquet", index=False)
    windows.to_csv(output / "opportunity_windows.csv", index=False)
    ranked = rank_opportunities(windows, events)
    ranked.to_csv(output / "opportunity_rankings.csv", index=False)
    run = {"snapshot_id": snapshot_id, "metadata_version": metadata_version, "parameters": parameters, "symbols": sorted(bars_by_symbol), "retrospective_only": True}
    (output / "run_metadata.json").write_text(json.dumps(run, indent=2, default=str) + "\n")
    (output / "research_summary.json").write_text(json.dumps(research_summary(events, windows, manifest, run), indent=2, default=str) + "\n")
    return {"events": events, "windows": windows, "rankings": ranked}
