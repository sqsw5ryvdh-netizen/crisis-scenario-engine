"""Plotly figures for the app. No Streamlit imports, so the charts can be tested alone."""

from __future__ import annotations

import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine.model import SimulationResult

LINE_COLOR = "#2a78d6"
NEWS_COLOR = "#898781"
# Status colours, always shown together with a text label.
TRIGGERS = {
    "recovery": ("Recovery", "#fab219"),
    "early_intervention": ("Early intervention", "#ec835a"),
    "failure": ("Failure", "#d03b3b"),
}
# Vertical position of each trigger label (paper coordinates), staggered to avoid overlap.
_LABEL_Y = {"recovery": 1.05, "early_intervention": 1.09, "failure": 1.13}


def build_timeline(result: SimulationResult, horizon: int, unit: str) -> go.Figure:
    """Liquidity buffer, deposits and CET1 ratio by day, with trigger and news markers."""
    table = result.table
    panels = [
        (f"Liquidity buffer ({unit})", table["liquidity_buffer"], "%{y:.2f}"),
        (f"Deposits ({unit})", table["deposits_total"], "%{y:.2f}"),
        ("CET1 ratio (%)", table["cet1_ratio"] * 100.0, "%{y:.1f}%"),
    ]
    fig = make_subplots(
        rows=len(panels),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=[title for title, _, _ in panels],
    )
    for row, (title, values, value_format) in enumerate(panels, start=1):
        fig.add_trace(
            go.Scatter(
                x=table["day"],
                y=values,
                mode="lines",
                name=title,
                line=dict(color=LINE_COLOR, width=2),
                hovertemplate="Day %{x}: " + value_format + "<extra></extra>",
            ),
            row=row,
            col=1,
        )
        fig.update_yaxes(rangemode="tozero", row=row, col=1)

    news = [event for event in result.events if event.kind == "news"]
    if news:
        buffer_by_day = table.set_index("day")["liquidity_buffer"]
        fig.add_trace(
            go.Scatter(
                x=[event.day for event in news],
                y=[buffer_by_day.get(event.day) for event in news],
                mode="markers",
                name="News",
                marker=dict(symbol="diamond", size=10, color=NEWS_COLOR,
                            line=dict(color="#ffffff", width=2)),
                text=[event.detail for event in news],
                hovertemplate="Day %{x} news: %{text}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    for event in result.events:
        if event.kind not in TRIGGERS:
            continue
        label, color = TRIGGERS[event.kind]
        fig.add_vline(x=event.day, line=dict(color=color, width=2, dash="dash"))
        fig.add_annotation(
            x=event.day,
            y=_LABEL_Y[event.kind],
            xref="x",
            yref="paper",
            text=f"{label}: day {event.day}",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font=dict(size=12),
        )

    fig.update_xaxes(range=[0, horizon], dtick=5)
    fig.update_xaxes(title_text="Day", row=len(panels), col=1)
    fig.update_layout(
        height=680,
        showlegend=False,
        margin=dict(l=10, r=10, t=120, b=10),
        hovermode="x unified",
    )
    return fig
