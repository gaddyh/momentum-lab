import pandas as pd
import pytest

from momentum_lab.miners.golden import mine_opportunities, timeframe_duration


def bars(timestamps, opens=None, highs=None, lows=None, closes=None):
    n = len(timestamps)
    opens = opens or [10.0] * n
    highs = highs or [10.1] * n
    lows = lows or [9.9] * n
    closes = closes or opens
    return pd.DataFrame({
        "ts": pd.DatetimeIndex(timestamps), "symbol": "TEST",
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [100_000.0] * n,
    })


def test_hourly_entry_is_next_open_and_not_two_minute_fill():
    ts = pd.date_range("2025-01-02 14:00", periods=7, freq="h", tz="UTC")
    frame = bars(ts, opens=[10, 11, 11, 11, 11, 11, 11], closes=[10] * 7)
    events, _ = mine_opportunities(frame, timeframe="1h", horizon_hours=4)
    first = events.iloc[0]
    assert first.decision_at == pd.Timestamp("2025-01-02 15:00", tz="UTC")
    assert first.entry_at == pd.Timestamp("2025-01-02 16:00", tz="UTC")
    assert first.entry_price == 11
    assert first.entry_delay_minutes == 60
    assert first.entry_semantics == "next_candle_open_proxy"


def test_same_hourly_candle_is_ambiguous_not_confirmed_win():
    ts = pd.date_range("2025-01-02 14:00", periods=7, freq="h", tz="UTC")
    frame = bars(ts, opens=[10] * 7, highs=[10.1, 10.1, 13, 10.1, 10.1, 10.1, 10.1],
                 lows=[9.9, 9.9, 8.5, 9.9, 9.9, 9.9, 9.9])
    events, _ = mine_opportunities(frame, timeframe="1h", horizon_hours=4)
    first = events.iloc[0]
    assert first.outcome == "ambiguous"
    assert bool(first.ambiguity_flag)
    assert not bool(first.success)


def test_overnight_gap_uses_actual_open_price():
    ts = pd.DatetimeIndex([
        "2025-01-02 19:00", "2025-01-03 14:00", "2025-01-03 15:00",
        "2025-01-03 16:00", "2025-01-03 17:00", "2025-01-03 18:00",
        "2025-01-03 19:00", "2025-01-04 14:00", "2025-01-05 14:00",
    ], tz="UTC")
    frame = bars(ts, opens=[10, 10, 13, 13, 13, 13, 13, 13, 13],
                 highs=[10.1, 10.1, 13.1, 13.1, 13.1, 13.1, 13.1, 13.1, 13.1],
                 lows=[9.9, 9.9, 13, 13, 13, 13, 13, 13, 13], closes=[10] * 9)
    events, _ = mine_opportunities(frame, timeframe="1h", horizon_hours=60)
    first = events.iloc[0]
    assert first.outcome == "target_gap"
    assert first.exit_price == 13
    assert first.gross_return == pytest.approx(.30)


def test_missing_weekend_gap_does_not_join_hourly_episodes():
    ts = pd.DatetimeIndex([
        "2025-01-02 14:00", "2025-01-02 15:00", "2025-01-02 16:00",
        "2025-01-02 17:00", "2025-01-06 14:00", "2025-01-06 15:00",
        "2025-01-06 16:00", "2025-01-06 17:00", "2025-01-06 18:00",
    ], tz="UTC")
    highs = [10.1, 10.1, 13, 13, 10.1, 10.1, 13, 13, 10.1]
    frame = bars(ts, highs=highs)
    events, windows = mine_opportunities(frame, timeframe="1h", horizon_hours=2)
    assert len(events[events.success]) >= 4
    assert len(windows) == 2
    assert windows.gold.all()


def test_insufficient_future_history_is_censored_and_not_timeout():
    ts = pd.date_range("2025-01-02 14:00", periods=3, freq="h", tz="UTC")
    frame = bars(ts)
    events, windows = mine_opportunities(frame, timeframe="1h", horizon_hours=60)
    assert not events.empty
    assert set(events.outcome) == {"censored"}
    assert windows.empty


def test_partial_or_missing_candle_never_proves_barrier_order():
    ts = pd.DatetimeIndex([
        "2025-01-02 14:00", "2025-01-02 15:00", "2025-01-02 18:00",
        "2025-01-02 19:00", "2025-01-02 20:00", "2025-01-02 21:00",
    ], tz="UTC")
    frame = bars(ts, highs=[10.1] * 6, lows=[9.9] * 6)
    events, _ = mine_opportunities(frame, timeframe="1h", horizon_hours=4)
    assert "target_gap" not in set(events.outcome)
    assert "censored" in set(events.outcome)


def test_dst_boundaries_remain_utc_and_episode_gap_is_explicit():
    ts = pd.DatetimeIndex([
        "2025-03-07 19:00", "2025-03-07 20:00", "2025-03-10 13:00",
        "2025-03-10 14:00", "2025-03-10 15:00", "2025-03-10 16:00",
    ], tz="UTC")
    frame = bars(ts, highs=[10.1, 10.1, 13, 13, 13, 10.1])
    events, windows = mine_opportunities(frame, timeframe="1h", horizon_hours=2)
    assert events.decision_at.dt.tz is not None
    assert windows.empty or (windows.timeframe == "1h").all()


def test_timeframe_validation():
    assert timeframe_duration("1h") == pd.Timedelta(hours=1)
    with pytest.raises(ValueError):
        mine_opportunities(bars(pd.date_range("2025-01-01", periods=3, freq="h", tz="UTC")), timeframe="5m")
