#!/usr/bin/env python3
"""Timeframe-aware retrospective Golden Opportunity mining.

Labels in this module intentionally use future data and MUST NOT be used as
live entry signals. Input timestamps are candle starts and must be UTC-aware.
"""
from __future__ import annotations

import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

SUPPORTED_TIMEFRAMES = {"1m": pd.Timedelta(minutes=1), "1h": pd.Timedelta(hours=1)}


def timeframe_duration(timeframe: str) -> pd.Timedelta:
    try:
        return SUPPORTED_TIMEFRAMES[timeframe.lower()]
    except KeyError as exc:
        raise ValueError("timeframe must be one of: 1m, 1h") from exc


def fetch_massive(symbol: str, start: str, end: str, api_key: str, output: Path):
    """Fetch 1m bars in weekly requests (max 50k base aggregates/request)."""
    start_d = pd.Timestamp(start).date()
    end_d = pd.Timestamp(end).date()
    session = requests.Session()
    frames = []
    cursor = start_d
    while cursor <= end_d:
        window_end = min(cursor + timedelta(days=6), end_d)
        url = (f"https://api.massive.com/v2/aggs/ticker/{symbol}/range/1/minute/"
               f"{cursor.isoformat()}/{window_end.isoformat()}")
        params = {"adjusted": "true", "sort": "asc", "limit": 50000, "apiKey": api_key}
        for attempt in range(5):
            response = session.get(url, params=params, timeout=60)
            if response.status_code in (429, 500, 502, 503, 504):
                time.sleep(min(2**attempt, 16))
                continue
            response.raise_for_status()
            break
        else:
            raise RuntimeError(f"Fetch failed: {symbol} {cursor} after retries")
        payload = response.json()
        if payload.get("next_url"):
            raise RuntimeError("Unexpected pagination in weekly query; narrow date chunks")
        if payload.get("results"):
            frames.append(pd.DataFrame(payload["results"]))
        print(f"{symbol} {cursor}..{window_end}: {len(payload.get('results', []))} bars")
        cursor = window_end + timedelta(days=1)
    if not frames:
        raise RuntimeError("No bars. Check symbol, date range and API subscription.")
    raw = pd.concat(frames, ignore_index=True).drop_duplicates("t").sort_values("t")
    df = pd.DataFrame({
        "ts": pd.to_datetime(raw["t"], unit="ms", utc=True),
        "symbol": symbol,
        "open": raw["o"].astype(float),
        "high": raw["h"].astype(float),
        "low": raw["l"].astype(float),
        "close": raw["c"].astype(float),
        "volume": raw["v"].astype(float),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    return df


def load_bars(path: Path, symbol: str | None = None):
    df = pd.read_parquet(path) if path.suffix.lower() == ".parquet" else pd.read_csv(path)
    required = {"ts", "open", "high", "low", "close", "volume"}
    if not required.issubset(df):
        raise ValueError(f"Missing columns: {required - set(df)}")
    df = df.copy()
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    if "symbol" not in df:
        if not symbol:
            raise ValueError("Provide --symbol when input lacks symbol column")
        df["symbol"] = symbol
    df = df.sort_values(["symbol", "ts"]).drop_duplicates(["symbol", "ts"]).reset_index(drop=True)
    for key in ("open", "high", "low", "close", "volume"):
        df[key] = pd.to_numeric(df[key], errors="coerce")
    bad = (df[["open", "high", "low", "close"]].isna().any(axis=1) |
           (df[["open", "high", "low", "close"]] <= 0).any(axis=1) |
           (df["low"] > df["high"]) |
           (df["high"] < df[["open", "close"]].max(axis=1)) |
           (df["low"] > df[["open", "close"]].min(axis=1)))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} invalid OHLC rows, clean source before mining")
    return df


def _outcome(ts_ns, o, h, l, c, entry_i, deadline_ns, target, stop, duration_ns):
    """Evaluate one entry without fabricating fills through gaps.

    A candle that contains both barriers is ambiguous. A candle overlapping the
    horizon boundary is not used to assert a crossing whose order is unknown.
    """
    entry_price = float(o[entry_i])
    target_price = entry_price * (1 + target)
    stop_price = entry_price * (1 - stop)
    limit = int(np.searchsorted(ts_ns, deadline_ns, side="left"))
    if limit <= entry_i:
        return {"outcome": "censored", "exit_i": None, "exit_price": np.nan,
                "gross_return": np.nan, "elapsed_hours": np.nan,
                "max_adverse_excursion": np.nan, "ambiguity_flag": False,
                "coverage_flag": "no_future_bars"}

    adverse = 0.0
    for k in range(entry_i, limit):
        bar_end = ts_ns[k] + duration_ns
        partial_horizon_bar = bar_end > deadline_ns
        if k > entry_i:
            if o[k] >= target_price:
                return {"outcome": "target_gap", "exit_i": k, "exit_price": float(o[k]),
                        "gross_return": float(o[k] / entry_price - 1),
                        "elapsed_hours": float((ts_ns[k] - ts_ns[entry_i]) / 3.6e12),
                        "max_adverse_excursion": adverse, "ambiguity_flag": False,
                        "coverage_flag": "complete"}
            if o[k] <= stop_price:
                return {"outcome": "stop_gap", "exit_i": k, "exit_price": float(o[k]),
                        "gross_return": float(o[k] / entry_price - 1),
                        "elapsed_hours": float((ts_ns[k] - ts_ns[entry_i]) / 3.6e12),
                        "max_adverse_excursion": adverse, "ambiguity_flag": False,
                        "coverage_flag": "complete"}
        adverse = min(adverse, float(l[k] / entry_price - 1))
        hit_target = h[k] >= target_price
        hit_stop = l[k] <= stop_price
        if partial_horizon_bar:
            return {"outcome": "censored", "exit_i": None, "exit_price": np.nan,
                    "gross_return": np.nan, "elapsed_hours": np.nan,
                    "max_adverse_excursion": adverse, "ambiguity_flag": hit_target and hit_stop,
                    "coverage_flag": "partial_horizon_bar"}
        if hit_target and hit_stop:
            return {"outcome": "ambiguous", "exit_i": k, "exit_price": np.nan,
                    "gross_return": np.nan, "elapsed_hours": float((ts_ns[k] - ts_ns[entry_i]) / 3.6e12),
                    "max_adverse_excursion": adverse, "ambiguity_flag": True,
                    "coverage_flag": "complete"}
        if hit_stop:
            return {"outcome": "stop", "exit_i": k, "exit_price": float(stop_price),
                    "gross_return": -float(stop),
                    "elapsed_hours": float((ts_ns[k] - ts_ns[entry_i]) / 3.6e12),
                    "max_adverse_excursion": adverse, "ambiguity_flag": False,
                    "coverage_flag": "complete"}
        if hit_target:
            return {"outcome": "target", "exit_i": k, "exit_price": float(target_price),
                    "gross_return": float(target),
                    "elapsed_hours": float((ts_ns[k] - ts_ns[entry_i]) / 3.6e12),
                    "max_adverse_excursion": adverse, "ambiguity_flag": False,
                    "coverage_flag": "complete"}

    if ts_ns[-1] < deadline_ns:
        return {"outcome": "censored", "exit_i": None, "exit_price": np.nan,
                "gross_return": np.nan, "elapsed_hours": np.nan,
                "max_adverse_excursion": adverse, "ambiguity_flag": False,
                "coverage_flag": "insufficient_future_history"}
    last = limit - 1
    return {"outcome": "timeout", "exit_i": last, "exit_price": float(c[last]),
            "gross_return": float(c[last] / entry_price - 1),
            "elapsed_hours": float((ts_ns[last] - ts_ns[entry_i]) / 3.6e12),
            "max_adverse_excursion": adverse, "ambiguity_flag": False,
            "coverage_flag": "complete"}


def find_entry_outcome(ts_ns, o, h, l, c, entry_i, deadline_ns, target, stop):
    """Compatibility wrapper for the original minute-miner API."""
    ts_ns = np.asarray(ts_ns, dtype=np.int64)
    # Older callers pass pandas datetime integers whose unit can be us under
    # newer pandas versions. Normalize the compatibility API to nanoseconds.
    scale = 1000 if len(ts_ns) > 1 and np.median(np.diff(ts_ns)) < 1_000_000_000 else 1
    normalized_ts = ts_ns * scale
    normalized_deadline = int(deadline_ns) * scale
    duration_ns = int(np.median(np.diff(normalized_ts))) if len(normalized_ts) > 1 else 60_000_000_000
    result = _outcome(normalized_ts, o, h, l, c, entry_i, normalized_deadline, target, stop, duration_ns)
    label = result["outcome"]
    if label == "ambiguous":
        label = "ambiguous_as_stop"
        result["gross_return"] = -stop
    return label, result["exit_i"] if result["exit_i"] is not None else entry_i, result["gross_return"], result["elapsed_hours"] * 60 if pd.notna(result["elapsed_hours"]) else np.nan


def _episode_rows(events: pd.DataFrame, timeframe: str, candidate_step: int, min_window_min: int):
    if events.empty:
        return pd.DataFrame()
    success = events[events["success"]].copy().sort_values("decision_at")
    if success.empty:
        return pd.DataFrame()
    duration = timeframe_duration(timeframe)
    expected_gap = duration * candidate_step
    gaps = success["decision_at"].diff()
    success["episode_group"] = (gaps.isna() | (gaps != expected_gap)).cumsum()
    rows = []
    for number, group in success.groupby("episode_group", sort=False):
        first = group.iloc[0]
        last = group.iloc[-1]
        duration_hours = (last.decision_at - first.decision_at).total_seconds() / 3600
        qualifying = len(group)
        hourly_gold = timeframe == "1h" and qualifying >= 2
        minute_gold = timeframe == "1m" and duration_hours * 60 >= min_window_min and qualifying >= min_window_min / candidate_step
        rows.append({
            "episode_id": f"{first.symbol}-{int(number):04d}",
            "symbol": first.symbol,
            "timeframe": timeframe,
            "first_decision": first.decision_at,
            "last_decision": last.decision_at,
            "qualifying_decision_points": qualifying,
            "duration_hours": duration_hours,
            "session_coverage_hours": duration_hours + duration.total_seconds() / 3600,
            "min_entry_price": group.entry_price.min(),
            "max_entry_price": group.entry_price.max(),
            "median_hours_to_target": group.elapsed_hours.median(),
            "max_adverse_excursion": group.max_adverse_excursion.min(),
            "confirmed_target_count": int((group.outcome == "target").sum()),
            "ambiguous_count": int(group.ambiguity_flag.sum()),
            "gold": bool(hourly_gold or minute_gold),
            "gold_basis": "hourly_two_consecutive_decisions" if hourly_gold else ("minute_30m_repeated_decisions" if minute_gold else "not_gold"),
            "hourly_proxy": timeframe == "1h",
            "minute_validated": timeframe == "1m",
        })
    return pd.DataFrame(rows)


def mine_opportunities(bars, timeframe="1h", target_pct=.20, stop_pct=.08,
                       horizon_hours=60, latency_minutes=2, candidate_step=1,
                       min_price=2.0, min_bar_dollar_vol=10000,
                       min_window_min=30, max_fill_wait_min=10):
    """Mine every eligible decision point using explicit timeframe semantics."""
    duration = timeframe_duration(timeframe)
    a = bars.sort_values("ts").reset_index(drop=True).copy()
    if a.empty:
        return pd.DataFrame(), pd.DataFrame()
    if a["ts"].dt.tz is None:
        raise ValueError("timestamps must be timezone-aware")
    # Pandas 3 may store UTC datetimes internally at microsecond precision;
    # Timestamp.value gives a stable nanosecond scale for elapsed comparisons.
    ts_ns = a["ts"].map(lambda value: value.value).to_numpy(dtype=np.int64)
    o, h, l, c, v = (a[key].to_numpy(float) for key in ("open", "high", "low", "close", "volume"))
    duration_ns = int(duration.value)
    horizon_ns = int(pd.Timedelta(hours=horizon_hours).value)
    rows = []
    for i in range(0, len(a), candidate_step):
        decision_at = a.loc[i, "ts"] + duration
        if c[i] < min_price or c[i] * v[i] < min_bar_dollar_vol:
            continue
        if timeframe == "1h":
            entry_target_ns = int(decision_at.value)
            entry_i = int(np.searchsorted(ts_ns, entry_target_ns, side="right"))
            entry_delay_minutes = ((ts_ns[entry_i] - entry_target_ns) / 60e9) if entry_i < len(a) else np.nan
            entry_coverage = "next_candle_open_proxy"
        else:
            entry_target_ns = int(decision_at.value + pd.Timedelta(minutes=latency_minutes).value)
            entry_i = int(np.searchsorted(ts_ns, entry_target_ns, side="left"))
            entry_delay_minutes = ((ts_ns[entry_i] - entry_target_ns) / 60e9) if entry_i < len(a) else np.nan
            entry_coverage = "minute_open_fill"
            if entry_i < len(a) and entry_delay_minutes > max_fill_wait_min:
                entry_i = len(a)
        deadline_ns = int(decision_at.value) + horizon_ns
        if ts_ns[-1] < deadline_ns:
            outcome = {"outcome": "censored", "exit_i": None, "exit_price": np.nan,
                       "gross_return": np.nan, "elapsed_hours": np.nan,
                       "max_adverse_excursion": np.nan, "ambiguity_flag": False,
                       "coverage_flag": "insufficient_future_history"}
        elif entry_i >= len(a):
            outcome = {"outcome": "censored", "exit_i": None, "exit_price": np.nan,
                       "gross_return": np.nan, "elapsed_hours": np.nan,
                       "max_adverse_excursion": np.nan, "ambiguity_flag": False,
                       "coverage_flag": "no_entry_candle"}
        else:
            outcome = _outcome(ts_ns, o, h, l, c, entry_i, deadline_ns,
                               target_pct, stop_pct, duration_ns)
        exit_i = outcome["exit_i"]
        rows.append({
            "symbol": a.loc[i, "symbol"], "timeframe": timeframe,
            "decision_at": decision_at, "entry_at": a.loc[entry_i, "ts"] if entry_i < len(a) else pd.NaT,
            "entry_price": o[entry_i] if entry_i < len(a) else np.nan,
            "exit_at": a.loc[exit_i, "ts"] if exit_i is not None else pd.NaT,
            "exit_price": outcome["exit_price"], "outcome": outcome["outcome"],
            "success": outcome["outcome"] == "target", "gross_return": outcome["gross_return"],
            "conservative_return": (-stop_pct if outcome["outcome"] == "ambiguous" else outcome["gross_return"]),
            "optimistic_return": (target_pct if outcome["outcome"] == "ambiguous" else outcome["gross_return"]),
            "elapsed_hours": outcome["elapsed_hours"],
            "max_adverse_excursion": outcome["max_adverse_excursion"],
            "ambiguity_flag": outcome["ambiguity_flag"],
            "coverage_flag": outcome["coverage_flag"], "entry_delay_minutes": entry_delay_minutes,
            "entry_semantics": entry_coverage, "decision_candle_at": a.loc[i, "ts"],
            "last_close_before_decision": c[i], "decision_bar_dollar_volume": c[i] * v[i],
        })
    events = pd.DataFrame(rows)
    return events, _episode_rows(events, timeframe, candidate_step, min_window_min)


def mine_symbol(df, latency_min=2, horizon_h=60, target=.20, stop=.08,
                min_window_min=30, min_price=2.0, min_bar_dollar_vol=10000,
                max_fill_wait_min=10, candidate_step=1):
    """Backward-compatible minute miner interface."""
    events, windows = mine_opportunities(
        df, timeframe="1m", target_pct=target, stop_pct=stop,
        horizon_hours=horizon_h, latency_minutes=latency_min,
        candidate_step=candidate_step, min_price=min_price,
        min_bar_dollar_vol=min_bar_dollar_vol, min_window_min=min_window_min,
        max_fill_wait_min=max_fill_wait_min,
    )
    # Preserve the old contract: candidates without a complete future horizon
    # were omitted rather than emitted as censored rows.
    if not events.empty:
        last_ts = df["ts"].max()
        horizon_end = events["decision_at"] + pd.Timedelta(hours=horizon_h)
        events = events[(events.coverage_flag == "complete") & (horizon_end <= last_ts)].reset_index(drop=True)
    return events, windows
