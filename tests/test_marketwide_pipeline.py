import json
from unittest.mock import Mock

import pandas as pd
import pytest

from momentum_lab.data_lake import download_chunks, read_chunks
from momentum_lab.research.marketwide import mine_market
from momentum_lab.universe import (
    load_snapshot,
    screen_by_liquidity,
    snapshot_id,
    write_snapshot,
)


def test_universe_requires_explicit_liquidity_for_limit(tmp_path):
    assets = pd.DataFrame({"symbol": ["BBB", "AAA"]})
    with pytest.raises(ValueError, match="liquidity file"):
        screen_by_liquidity(assets, None, 2)
    selected, criteria = screen_by_liquidity(assets, pd.DataFrame({"symbol": ["AAA", "BBB"], "dollar_volume": [10, 20]}), 1)
    assert selected.symbol.tolist() == ["BBB"]
    assert criteria["liquidity_screen"]["metric"] == "dollar_volume"
    sid, path = write_snapshot(selected, tmp_path, criteria, "test", survivorship_bias=False)
    loaded, metadata = load_snapshot(path)
    assert loaded.symbol.tolist() == ["BBB"]
    assert metadata["snapshot_id"] == sid
    assert snapshot_id(loaded, metadata["selection_criteria"]) == sid


def test_chunk_download_is_restartable_and_manifested(tmp_path):
    response = Mock(ok=True)
    response.json.return_value = {"bars": [{"t": "2025-01-02T14:00:00Z", "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100}], "next_page_token": None}
    session = Mock()
    session.get.return_value = response
    chunks = download_chunks(["TEST"], "2025-01-01", "2025-03-01", tmp_path, api_key="k", api_secret="s", session=session)
    assert len(chunks) == 2
    assert {item["status"] for item in chunks} == {"success"}
    calls = session.get.call_count
    download_chunks(["TEST"], "2025-01-01", "2025-03-01", tmp_path, api_key="k", api_secret="s", session=session)
    assert session.get.call_count == calls
    # Logical merge deduplicates repeated timestamps across independently fetched chunks.
    assert len(read_chunks(tmp_path, "TEST")) == 1
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert all(item["checksum"] for item in manifest["chunks"])


def test_market_summary_retains_failures_and_successes(tmp_path):
    ts = pd.date_range("2025-01-02 14:00", periods=8, freq="h", tz="UTC")
    bars = pd.DataFrame({"ts": ts, "symbol": "TEST", "open": [10] * 8,
                         "high": [10.1, 10.1, 13, 13, 10.1, 10.1, 10.1, 10.1],
                         "low": [9.9] * 8, "close": [10] * 8, "volume": [100000] * 8})
    result = mine_market({"TEST": bars}, tmp_path, "snapshot1", {"timeframe": "1h", "horizon_hours": 2})
    summary = json.loads((tmp_path / "research_summary.json").read_text())
    assert len(result["events"]) > 0
    assert summary["candidates"] == len(result["events"])
    assert "outcome_counts" in summary
    assert "opportunity_window_duration_hours" in summary
    assert json.loads((tmp_path / "run_metadata.json").read_text())["snapshot_id"] == "snapshot1"
