"""Historical Alpaca stock bars collector (research only).

Uses market-data credentials; no brokerage/trading permissions required.
Bars are returned in UTC and timestamps mark the start of the candle.
"""
from __future__ import annotations

import os
from pathlib import Path
import requests
import pandas as pd

BASE = "https://data.alpaca.markets/v2/stocks"


def fetch_alpaca(
    symbol: str, start: str, end: str, output: Path,
    api_key: str | None = None, api_secret: str | None = None,
    feed: str = "iex", timeframe: str = "1Hour",
) -> pd.DataFrame:
    key = api_key or os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID")
    secret = api_secret or os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise ValueError("Missing Alpaca credentials: ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")

    url = f"{BASE}/{symbol.upper()}/bars"
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    params = {
        "start": pd.Timestamp(start, tz="UTC").isoformat() if len(start) == 10 else pd.Timestamp(start).tz_convert("UTC").isoformat(),
        "end": pd.Timestamp(end, tz="UTC").isoformat() if len(end) == 10 else pd.Timestamp(end).tz_convert("UTC").isoformat(),
        "timeframe": timeframe,
        "feed": feed,
        "adjustment": "all",
        "limit": 10000,
        "sort": "asc",
    }
    rows = []
    with requests.Session() as session:
        while True:
            response = session.get(url, headers=headers, params=params, timeout=45)
            if not response.ok:
                # Never log headers or API secrets.
                raise RuntimeError(f"Alpaca HTTP {response.status_code}: {response.text[:300]}")
            payload = response.json()
            rows.extend(payload.get("bars") or [])
            token = payload.get("next_page_token")
            if not token:
                break
            params["page_token"] = token

    if not rows:
        raise RuntimeError("Alpaca returned no bars. Check symbol, date range and feed entitlement.")

    raw = pd.DataFrame(rows)
    result = pd.DataFrame({
        "ts": pd.to_datetime(raw["t"], utc=True),
        "symbol": symbol.upper(),
        "open": raw["o"].astype(float),
        "high": raw["h"].astype(float),
        "low": raw["l"].astype(float),
        "close": raw["c"].astype(float),
        "volume": raw["v"].astype(float),
    }).sort_values("ts").drop_duplicates("ts")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.suffix.lower() == ".csv":
        result.to_csv(output, index=False)
    elif output.suffix.lower() == ".parquet":
        result.to_parquet(output, index=False)
    else:
        raise ValueError("Output must end with .csv or .parquet")
    return result
