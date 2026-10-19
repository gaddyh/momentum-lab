import pandas as pd
from momentum_lab.miners.golden import find_entry_outcome, mine_symbol
from momentum_lab.features.contagion import peer_breadth

def test_first_bar_target():
    ts = pd.date_range("2025-01-01", periods=3, freq="min", tz="UTC").astype("int64").to_numpy()
    label, idx, pnl, _ = find_entry_outcome(ts, [10, 10, 10], [12.2, 10, 10],
                                            [9.8, 10, 10], [11, 10, 10], 0,
                                            int(ts[-1]), .20, .08)
    assert label == "target" and idx == 0 and pnl == .20

def test_both_barriers_is_not_win():
    ts = pd.date_range("2025-01-01", periods=3, freq="min", tz="UTC").astype("int64").to_numpy()
    label, _, _, _ = find_entry_outcome(ts, [10]*3, [12.3]*3, [9.1]*3,
                                        [10]*3, 0, int(ts[-1]), .20, .08)
    assert label == "ambiguous_as_stop"

def test_no_future_history_is_censored():
    ts = pd.date_range("2025-01-06 14:30", periods=40, freq="min", tz="UTC")
    bars = pd.DataFrame(dict(ts=ts, symbol="ABC", open=10., high=10.01, low=9.99,
                             close=10., volume=100000.))
    events, windows = mine_symbol(bars, horizon_h=60)
    assert events.empty and windows.empty

def test_breadth_excludes_self():
    d = pd.DataFrame({"A":[100,110],"B":[100,100],"C":[100,110]})
    s = peer_breadth(d,"A",["A","B","C"],lookback=1)
    assert s.iloc[1] == .5
