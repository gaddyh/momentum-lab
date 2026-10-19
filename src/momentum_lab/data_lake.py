"""Immutable, restartable monthly Alpaca hourly-bar chunks."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pandas as pd
import requests

from momentum_lab.collectors.alpaca import BASE

BAR_COLUMNS = ["ts", "symbol", "open", "high", "low", "close", "volume"]


def _credentials(api_key=None, api_secret=None):
    key = api_key or os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID")
    secret = api_secret or os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise ValueError("Missing Alpaca credentials: ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
    return key, secret


def _month_ranges(start: str, end: str):
    begin = pd.Timestamp(start, tz="UTC").normalize()
    finish = pd.Timestamp(end, tz="UTC").normalize()
    cursor = begin.replace(day=1)
    while cursor < finish:
        following = cursor + pd.offsets.MonthBegin(1)
        yield cursor, min(following, finish)
        cursor = following


def validate_bars(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    missing = set(BAR_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing bar columns: {missing}")
    result = frame[BAR_COLUMNS].copy()
    result["symbol"] = result["symbol"].astype(str).str.upper()
    result["ts"] = pd.to_datetime(result["ts"], utc=True)
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    bad = result[["open", "high", "low", "close"]].isna().any(axis=1) | (result[["open", "high", "low", "close"]] <= 0).any(axis=1)
    bad |= result.low.gt(result.high) | result.high.lt(result[["open", "close"]].max(axis=1)) | result.low.gt(result[["open", "close"]].min(axis=1))
    if bad.any() or result.ts.duplicated().any() or (result.symbol != symbol.upper()).any():
        raise ValueError(f"Invalid or duplicate OHLC rows for {symbol}")
    return result.sort_values("ts").reset_index(drop=True)


def _fetch_chunk(symbol, start, end, feed, api_key, api_secret, session):
    params = {"start": start.isoformat(), "end": end.isoformat(), "timeframe": "1Hour", "feed": feed,
              "adjustment": "all", "limit": 10000, "sort": "asc"}
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
    rows = []
    url = f"{BASE}/{symbol.upper()}/bars"
    while True:
        response = session.get(url, headers=headers, params=params, timeout=45)
        if not response.ok:
            raise RuntimeError(f"Alpaca HTTP {response.status_code}: {response.text[:300]}")
        payload = response.json()
        rows.extend(payload.get("bars") or [])
        token = payload.get("next_page_token")
        if not token:
            break
        params["page_token"] = token
    if not rows:
        return pd.DataFrame(columns=BAR_COLUMNS)
    raw = pd.DataFrame(rows)
    return validate_bars(pd.DataFrame({"ts": raw.t, "symbol": symbol.upper(), "open": raw.o, "high": raw.h,
                                       "low": raw.l, "close": raw.c, "volume": raw.v}), symbol)


def _checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _manifest_path(root: Path) -> Path:
    return root / "manifest.json"


def download_chunks(symbols, start: str, end: str, root: Path, feed="iex", force_refresh=False,
                    api_key=None, api_secret=None, session=None) -> list[dict]:
    key, secret = _credentials(api_key, api_secret)
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = _manifest_path(root)
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"version": 1, "chunks": []}
    existing = {(row["symbol"], row["start"], row["end"], row["feed"], row["timeframe"], row["adjustment"]): row for row in manifest["chunks"]}
    client = session or requests.Session()
    own = session is None
    try:
        for symbol in sorted({str(s).upper() for s in symbols}):
            for chunk_start, chunk_end in _month_ranges(start, end):
                key_tuple = (symbol, chunk_start.isoformat(), chunk_end.isoformat(), feed, "1Hour", "all")
                path = root / "bars" / feed / "1Hour" / symbol / f"{chunk_start:%Y-%m}.parquet"
                previous = existing.get(key_tuple)
                if previous and path.exists() and not force_refresh and previous.get("status") in ("success", "empty") and _checksum(path) == previous.get("checksum"):
                    continue
                row = {"symbol": symbol, "start": chunk_start.isoformat(), "end": chunk_end.isoformat(), "feed": feed,
                       "timeframe": "1Hour", "adjustment": "all", "downloaded_at": pd.Timestamp.now(tz="UTC").isoformat()}
                try:
                    frame = _fetch_chunk(symbol, chunk_start, chunk_end, feed, key, secret, client)
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".parquet", delete=False) as temp:
                        temporary = Path(temp.name)
                    try:
                        frame.to_parquet(temporary, index=False)
                        os.replace(temporary, path)
                    finally:
                        temporary.unlink(missing_ok=True)
                    row.update({"status": "empty" if frame.empty else "success", "rows": len(frame), "checksum": _checksum(path)})
                except (OSError, RuntimeError, ValueError, KeyError) as exc:
                    row.update({"status": "failed", "rows": 0, "error": str(exc)[:500]})
                existing[key_tuple] = row
                manifest["chunks"] = sorted(existing.values(), key=lambda item: (item["symbol"], item["start"], item["feed"]))
                manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    finally:
        if own:
            client.close()
    return manifest["chunks"]


def read_chunks(root: Path, symbol: str, start: str | None = None, end: str | None = None,
                feed: str | None = None) -> pd.DataFrame:
    manifest_path = _manifest_path(root)
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    rows = []
    for item in manifest["chunks"]:
        if item["symbol"] != symbol.upper() or item["status"] == "failed" or (feed and item["feed"] != feed):
            continue
        path = root / "bars" / item["feed"] / item["timeframe"] / item["symbol"] / f"{item['start'][:7]}.parquet"
        if path.exists() and item["status"] == "success" and _checksum(path) == item["checksum"]:
            rows.append(pd.read_parquet(path))
    if not rows:
        return pd.DataFrame(columns=BAR_COLUMNS)
    result = pd.concat(rows, ignore_index=True).drop_duplicates(["symbol", "ts"]).sort_values("ts")
    if start:
        result = result[result.ts >= pd.Timestamp(start, tz="UTC")]
    if end:
        result = result[result.ts < pd.Timestamp(end, tz="UTC")]
    return result.reset_index(drop=True)
