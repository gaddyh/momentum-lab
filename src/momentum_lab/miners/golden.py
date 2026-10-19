#!/usr/bin/env python3
"""Golden Opportunity Miner 005: minute-bar retrospective opportunity discovery.

The gold labels intentionally use future data. They MUST NOT be used as live entry signals.
Timestamp convention: bar `ts` = start of one-minute bar, UTC.
Decision available at ts + 1 minute, fill earliest bar OPEN >= decision + latency.
"""
from __future__ import annotations

import argparse
import os
import time
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

MINUTE = pd.Timedelta(minutes=1)


def fetch_massive(symbol: str, start: str, end: str, api_key: str, output: Path):
    """Fetch 1m bars in weekly requests (max 50k base aggregates/request).
    Input date bounds inclusive; each returned minute timestamp is UTC.
    """
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
            part = pd.DataFrame(payload["results"])
            frames.append(part)
        print(f"{symbol} {cursor}..{window_end}: {len(payload.get('results', []))} bars")
        cursor = window_end + timedelta(days=1)
    if not frames:
        raise RuntimeError("No bars. Check symbol, date range and API subscription.")
    raw = pd.concat(frames, ignore_index=True).drop_duplicates('t').sort_values('t')
    df = pd.DataFrame({
        "ts": pd.to_datetime(raw['t'], unit='ms', utc=True),
        "symbol": symbol,
        "open": raw['o'].astype(float),
        "high": raw['h'].astype(float),
        "low": raw['l'].astype(float),
        "close": raw['c'].astype(float),
        "volume": raw['v'].astype(float),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output, index=False)
    return df


def load_bars(path: Path, symbol: str | None = None):
    df = pd.read_parquet(path) if path.suffix.lower() == '.parquet' else pd.read_csv(path)
    required = {'ts','open','high','low','close','volume'}
    if not required.issubset(df):
        raise ValueError(f"Missing columns: {required - set(df)}")
    df['ts'] = pd.to_datetime(df['ts'], utc=True)
    if 'symbol' not in df:
        if not symbol:
            raise ValueError('Provide --symbol when input lacks symbol column')
        df['symbol'] = symbol
    df = df.sort_values(['symbol','ts']).drop_duplicates(['symbol','ts']).reset_index(drop=True)
    for key in ('open','high','low','close','volume'):
        df[key] = pd.to_numeric(df[key], errors='coerce')
    bad = (df[['open','high','low','close']].isna().any(axis=1) |
           (df[['open','high','low','close']] <= 0).any(axis=1) |
           (df['low'] > df['high']) |
           (df['high'] < df[['open','close']].max(axis=1)) |
           (df['low'] > df[['open','close']].min(axis=1)))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} invalid OHLC rows, clean source before mining")
    return df


def find_entry_outcome(ts_ns, o, h, l, c, entry_i, deadline_ns, target, stop):
    """Return outcome and exit index; ambiguity conservatively treated as loss.
    With minute OHLC, intraminute order of high and low is unknowable.
    """
    entry_price = o[entry_i]
    target_price = entry_price * (1 + target)
    stop_price = entry_price * (1 - stop)
    limit = int(np.searchsorted(ts_ns, deadline_ns, side='right'))
    if limit <= entry_i:
        return 'missing', entry_i, np.nan, np.nan
    # At entry we fill at that bar's open; price crossings can occur after the open.
    for k in range(entry_i, limit):
        if k > entry_i:
            if o[k] >= target_price:
                return 'target_gap', k, o[k]/entry_price - 1, (ts_ns[k]-ts_ns[entry_i])/60e9
            if o[k] <= stop_price:
                return 'stop_gap', k, o[k]/entry_price - 1, (ts_ns[k]-ts_ns[entry_i])/60e9
        hit_t = h[k] >= target_price
        hit_s = l[k] <= stop_price
        if hit_t and hit_s:
            return 'ambiguous_as_stop', k, -stop, (ts_ns[k]-ts_ns[entry_i])/60e9
        if hit_s:
            return 'stop', k, -stop, (ts_ns[k]-ts_ns[entry_i])/60e9
        if hit_t:
            return 'target', k, target, (ts_ns[k]-ts_ns[entry_i])/60e9
    last = limit - 1
    return 'timeout', last, c[last]/entry_price - 1, (ts_ns[last]-ts_ns[entry_i])/60e9


def mine_symbol(df, latency_min=2, horizon_h=60, target=.20, stop=.08,
                min_window_min=30, min_price=2.0, min_bar_dollar_vol=10000,
                max_fill_wait_min=10, candidate_step=1):
    """Retrospective oracle mining; NEVER a signal generator.

    A time t is eligible as a candidate when close(t) >= min_price and minute
    dollar volume >= threshold. This is an arbitrary ex-ante liquidity screen,
    not a discovered alpha feature.
    """
    a = df.sort_values('ts').reset_index(drop=True)
    ts = a['ts'].astype('int64').to_numpy()
    o = a['open'].to_numpy(float)
    h = a['high'].to_numpy(float)
    l = a['low'].to_numpy(float)
    c = a['close'].to_numpy(float)
    v = a['volume'].to_numpy(float)
    horizon_ns = int(pd.Timedelta(hours=horizon_h).value)
    latency_ns = int(pd.Timedelta(minutes=latency_min + 1).value)
    max_wait_ns = int(pd.Timedelta(minutes=max_fill_wait_min).value)
    last_ts = ts[-1]
    rows = []
    for i in range(0, len(a), candidate_step):
        # We must be able to observe FULL calendar horizon, not just another bar.
        decision_ns = ts[i] + int(MINUTE.value)
        deadline = decision_ns + horizon_ns
        if deadline > last_ts:
            continue
        if c[i] < min_price or c[i]*v[i] < min_bar_dollar_vol:
            continue
        # Signal at bar END; latency starts when the bar ends.
        target_entry_ns = decision_ns + int(pd.Timedelta(minutes=latency_min).value)
        ei = int(np.searchsorted(ts, target_entry_ns, side='left'))
        if ei >= len(a) or ts[ei]-target_entry_ns > max_wait_ns:
            continue
        label, exit_i, gross_return, elapsed = find_entry_outcome(
            ts, o, h, l, c, ei, deadline, target, stop
        )
        rows.append({
            'symbol': a.loc[i, 'symbol'],
            'decision_at': pd.Timestamp(decision_ns, tz='UTC'),
            'entry_at': pd.Timestamp(ts[ei], tz='UTC'),
            'entry_price': o[ei],
            'exit_at': pd.Timestamp(ts[exit_i], tz='UTC'),
            'outcome': label,
            'success': label.startswith('target'),
            'gross_return': gross_return,
            'minutes_entry_to_exit': elapsed,
            'last_close_before_signal': c[i],
            'signal_minute_dollar_volume': c[i]*v[i],
        })
    events = pd.DataFrame(rows)
    if events.empty:
        return events, pd.DataFrame()
    # A window is a streak of qualifying decisions with <= 2-minute gaps.
    success = events.loc[events.success].copy().sort_values('decision_at')
    if success.empty:
        return events, pd.DataFrame()
    gaps = success['decision_at'].diff().dt.total_seconds().div(60)
    success['episode'] = (gaps.isna() | (gaps > 2*candidate_step)).cumsum()
    groups = success.groupby('episode', sort=False)
    windows = groups.agg(
        symbol=('symbol','first'),
        first_decision=('decision_at','first'),
        last_decision=('decision_at','last'),
        qualifying_minutes=('decision_at','size'),
        first_entry=('entry_at','first'),
        median_target_minutes=('minutes_entry_to_exit','median'),
        min_entry_price=('entry_price','min'),
        max_entry_price=('entry_price','max'),
    ).reset_index(drop=True)
    windows['duration_minutes'] = (
        windows.last_decision-windows.first_decision
    ).dt.total_seconds()/60
    # Demand sustained consecutive opportunity, not 30 sparse ticks.
    windows['gold'] = (windows.duration_minutes >= min_window_min) & (
        windows.qualifying_minutes >= min_window_min/candidate_step
    )
    return events, windows


