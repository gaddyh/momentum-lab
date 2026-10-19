"""Portable static Plotly explorer for retrospective opportunity labels."""
from __future__ import annotations

from pathlib import Path

import pandas as pd


def write_explorer(bars: pd.DataFrame, events: pd.DataFrame, windows: pd.DataFrame, output: Path,
                   target_pct: float = .20, stop_pct: float = .08) -> None:
    """Write a self-contained episode selector with successes and failures."""
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError as exc:
        raise RuntimeError("Install the explorer extra with: pip install -e '.[dev,explorer]'") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    if windows.empty:
        output.write_text("<html><body><h1>No opportunity episodes</h1><p>No successful episodes were found.</p></body></html>")
        return

    figures = []
    labels = []
    for _, episode in windows.iterrows():
        symbol = episode.symbol
        start = episode.first_decision - pd.Timedelta(hours=6)
        end = episode.last_decision + pd.Timedelta(hours=12)
        subset = bars[(bars.symbol == symbol) & (bars.ts >= start) & (bars.ts <= end)].sort_values("ts")
        candidates = events[(events.symbol == symbol) & (events.decision_at >= episode.first_decision) &
                            (events.decision_at <= episode.last_decision)]
        if subset.empty:
            continue
        fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.08,
                            row_heights=[.75, .25], subplot_titles=(
                                f"{symbol} {episode.episode_id} — retrospective hindsight analysis",
                                "Candidate outcome"))
        fig.add_trace(go.Candlestick(x=subset.ts, open=subset.open, high=subset.high,
                                     low=subset.low, close=subset.close, name="Hourly candles"), row=1, col=1)
        for outcome, color, label in (("target", "green", "Confirmed target"),
                                      ("ambiguous", "orange", "Ambiguous"),
                                      ("stop", "red", "Stop"),
                                      ("stop_gap", "darkred", "Stop gap"),
                                      ("timeout", "gray", "Timeout"),
                                      ("censored", "purple", "Censored"),
                                      ("target_gap", "darkgreen", "Target gap")):
            points = candidates[candidates.outcome == outcome]
            if points.empty:
                continue
            fig.add_trace(go.Scatter(x=points.entry_at, y=points.entry_price, mode="markers",
                                     marker={"color": color, "size": 9}, name=label,
                                     customdata=points[["decision_at", "entry_delay_minutes", "gross_return"]],
                                     hovertemplate="entry %{x}<br>price %{y:.2f}<br>decision %{customdata[0]}<br>delay %{customdata[1]:.1f}m<br>return %{customdata[2]:.1%}<extra></extra>"), row=1, col=1)
            fig.add_trace(go.Scatter(x=points.decision_at, y=[1] * len(points), mode="markers",
                                     marker={"color": color, "size": 8}, name=f"{label} decision",
                                     showlegend=False), row=2, col=1)
        first_entry = candidates.entry_price.dropna().iloc[0] if not candidates.entry_price.dropna().empty else None
        if first_entry is not None:
            fig.add_hline(y=first_entry * (1 + target_pct), line_dash="dash", line_color="green",
                          annotation_text="+20% target", row=1, col=1)
            fig.add_hline(y=first_entry * (1 - stop_pct), line_dash="dash", line_color="red",
                          annotation_text="-8% stop", row=1, col=1)
        fig.update_layout(height=800, xaxis_rangeslider_visible=False,
                          title=(f"{symbol} / {episode.episode_id} | "
                                 f"{episode.first_decision} → {episode.last_decision} | "
                                 f"{episode.gold_basis} | HINDSIGHT ONLY"),
                          legend={"orientation": "h"})
        figures.append(fig)
        labels.append(f"{symbol} {episode.episode_id} ({episode.gold_basis})")

    if not figures:
        output.write_text("<html><body><h1>No plottable opportunity episodes</h1></body></html>")
        return
    # A compact standalone report uses one Plotly div per episode and a small
    # selector that toggles visibility without requiring a web server.
    divs = []
    for i, fig in enumerate(figures):
        div = fig.to_html(full_html=False, include_plotlyjs=i == 0,
                          div_id=f"episode-{i}")
        divs.append(f'<section class="episode" id="section-{i}"><h2>{labels[i]}</h2>{div}</section>')
    options = "".join(f'<option value="{i}">{label}</option>' for i, label in enumerate(labels))
    script = """
<script>
function selectEpisode(value) {
  document.querySelectorAll('.episode').forEach((node, index) => {
    node.style.display = index === Number(value) ? 'block' : 'none';
  });
}
selectEpisode(0);
</script>
"""
    html = ("<!doctype html><html><head><meta charset='utf-8'><title>Momentum Lab explorer</title>"
            "<style>body{font-family:system-ui;margin:2rem}.episode{display:none}</style></head><body>"
            "<h1>Momentum Lab opportunity explorer</h1>"
            "<p><strong>Hindsight analysis only.</strong> Hourly entries are next-candle-open proxies, not two-minute executions.</p>"
            f"<label>Episode: <select onchange='selectEpisode(this.value)'>{options}</select></label>"
            + "".join(divs) + script + "</body></html>")
    output.write_text(html)
