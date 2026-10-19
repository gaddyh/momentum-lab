import pandas as pd
import pytest
from unittest.mock import Mock, patch
from momentum_lab.collectors.alpaca import fetch_alpaca


def test_fetch_hourly_pagination(tmp_path):
    first = {"bars": [{"t":"2025-01-02T14:30:00Z","o":10,"h":11,"l":9,"c":10.5,"v":300}],
             "next_page_token":"next"}
    second = {"bars": [{"t":"2025-01-02T15:30:00Z","o":10.5,"h":12,"l":10,"c":11,"v":400}],
              "next_page_token":None}
    responses = []
    for item in (first, second):
        r = Mock(status_code=200, ok=True)
        r.json.return_value = item
        responses.append(r)
    with patch("momentum_lab.collectors.alpaca.requests.Session") as session:
        session.return_value.__enter__.return_value.get.side_effect = responses
        result = fetch_alpaca("TEST", "2025-01-02", "2025-01-04",
                              tmp_path / "bars.csv", "key", "secret")
    assert len(result) == 2
    assert result.ts.dt.tz is not None
    assert (tmp_path / "bars.csv").exists()


def test_missing_credentials(tmp_path, monkeypatch):
    for name in ("ALPACA_API_KEY_ID","APCA_API_KEY_ID","ALPACA_API_SECRET_KEY","APCA_API_SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="Missing Alpaca credentials"):
        fetch_alpaca("TEST", "2025-01-02", "2025-01-04", tmp_path/"x.csv")
