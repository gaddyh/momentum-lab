"""Pure descriptive peer breadth feature; NOT yet a predictive model."""
import pandas as pd

def peer_breadth(close: pd.DataFrame, symbol: str, peers: list[str], lookback: int = 5,
                 min_return: float = .05) -> pd.Series:
    """Share of available peers whose close increased >= min_return in lookback bars."""
    peer_names = [p for p in peers if p != symbol and p in close.columns]
    if not peer_names:
        raise ValueError("At least one peer is required")
    ret = close[peer_names].pct_change(lookback, fill_method=None)
    positive = (ret >= min_return).where(ret.notna())
    return positive.mean(axis=1)
