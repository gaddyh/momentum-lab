"""Frozen universe snapshots for reproducible retrospective research."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

ASSETS_URL = "https://api.alpaca.markets/v2/assets"
REQUIRED_COLUMNS = {"symbol"}


def _canonical_frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    if "symbol" not in result:
        raise ValueError("Universe must contain a symbol column")
    result["symbol"] = result["symbol"].astype(str).str.strip().str.upper()
    result = result[result.symbol.ne("")].drop_duplicates("symbol").sort_values("symbol")
    return result.reset_index(drop=True)


def snapshot_id(frame: pd.DataFrame, criteria: dict) -> str:
    payload = _canonical_frame(frame).to_csv(index=False, lineterminator="\n")
    digest = hashlib.sha256((json.dumps(criteria, sort_keys=True) + "\n" + payload).encode()).hexdigest()
    return digest[:16]


def discover_assets(api_key: str | None = None, api_secret: str | None = None,
                    session: requests.Session | None = None) -> pd.DataFrame:
    key = api_key or os.getenv("ALPACA_API_KEY_ID") or os.getenv("APCA_API_KEY_ID")
    secret = api_secret or os.getenv("ALPACA_API_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise ValueError("Missing Alpaca credentials: ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY")
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    own = session is None
    client = session or requests.Session()
    try:
        response = client.get(ASSETS_URL, headers=headers, params={"status": "active", "asset_class": "us_equity"}, timeout=45)
        if not response.ok:
            raise RuntimeError(f"Alpaca Assets HTTP {response.status_code}: {response.text[:300]}")
        rows = response.json()
    finally:
        if own:
            client.close()
    if not isinstance(rows, list):
        raise TypeError("Alpaca Assets returned an unexpected payload")
    frame = pd.DataFrame(rows)
    if frame.empty:
        raise RuntimeError("Alpaca Assets returned no assets")
    # Asset discovery is not a liquidity ranking. Keep only explicit eligibility fields here.
    if "class" in frame:
        frame = frame[frame["class"].eq("us_equity")]
    if "status" in frame:
        frame = frame[frame["status"].eq("active")]
    if "tradable" in frame:
        frame = frame[frame["tradable"].fillna(False)]
    return _canonical_frame(frame)


def screen_by_liquidity(assets: pd.DataFrame, liquidity: pd.DataFrame | None,
                        limit: int | None = None) -> tuple[pd.DataFrame, dict]:
    assets = _canonical_frame(assets)
    criteria = {"asset_discovery": "Alpaca Assets active US equities/tradable filter"}
    if liquidity is None:
        if limit is not None:
            raise ValueError("A liquidity file is required when --limit is used; Alpaca Assets has no historical liquidity ranking")
        criteria["liquidity_screen"] = "none; asset discovery only"
        return assets, criteria
    liquidity = _canonical_frame(liquidity)
    value_column = next((c for c in ("dollar_volume", "avg_dollar_volume", "median_dollar_volume") if c in liquidity), None)
    if value_column is None:
        raise ValueError("Liquidity file must contain dollar_volume, avg_dollar_volume, or median_dollar_volume")
    liquidity[value_column] = pd.to_numeric(liquidity[value_column], errors="coerce")
    liquidity = liquidity.dropna(subset=[value_column]).sort_values([value_column, "symbol"], ascending=[False, True])
    result = assets.merge(liquidity[["symbol", value_column]], on="symbol", how="inner")
    result = result.sort_values([value_column, "symbol"], ascending=[False, True])
    if limit is not None:
        result = result.head(limit)
    criteria["liquidity_screen"] = {"metric": value_column, "ranking": "descending", "limit": limit,
                                    "source": "user-supplied liquidity file; not historical unless documented"}
    return result.reset_index(drop=True), criteria


def write_snapshot(frame: pd.DataFrame, directory: Path, criteria: dict,
                   source: str, survivorship_bias: bool = True) -> tuple[str, Path]:
    frame = _canonical_frame(frame)
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    full_criteria = {**criteria, "source": source, "generated_at": generated_at}
    sid = snapshot_id(frame, full_criteria)
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / f"universe_{sid}.csv"
    frame.to_csv(csv_path, index=False)
    metadata = {
        "snapshot_id": sid,
        "generated_at": generated_at,
        "source": source,
        "selection_criteria": full_criteria,
        "symbol_count": len(frame),
        "survivorship_bias": survivorship_bias,
        "point_in_time": not survivorship_bias,
        "warning": "Current active Alpaca assets are not a point-in-time historical universe." if survivorship_bias else None,
    }
    (directory / f"universe_{sid}.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return sid, csv_path


def load_snapshot(path: Path) -> tuple[pd.DataFrame, dict]:
    if path.suffix.lower() != ".csv":
        raise ValueError("Snapshot path must be a CSV")
    frame = _canonical_frame(pd.read_csv(path))
    metadata_path = path.with_suffix(".json")
    metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {
        "snapshot_id": snapshot_id(frame, {}), "source": "imported CSV", "survivorship_bias": None
    }
    if metadata.get("snapshot_id") and path.stem.endswith(metadata["snapshot_id"]):
        expected = snapshot_id(frame, metadata.get("selection_criteria", {}))
        if expected != metadata["snapshot_id"]:
            raise ValueError("Universe snapshot contents do not match its recorded snapshot ID")
    return frame, metadata
