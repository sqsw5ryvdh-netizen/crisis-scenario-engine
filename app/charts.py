"""Plotly figures for the app. No Streamlit imports, so the charts can be tested alone."""

from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from engine.contagion import ContagionResult
from engine.model import SimulationResult

LINE_COLOR = "#2a78d6"
BAND_COLOR = "rgba(42, 120, 214, 0.18)"
NEWS_COLOR = "#898781"
# Status colours, always shown together with a text label.
TRIGGERS = {
    "recovery": ("Recovery", "#fab219"),
    "early_intervention": ("Early intervention", "#ec835a"),
    "likely_to_fail": ("Likely to fail", "#d03b3b"),
    "failure": ("Payments default", "#8f1d2c"),
}
# Vertical position of each trigger label (paper coordinates), staggered to avoid overlap.
_LABEL_Y = {"recovery": 1.05, "early_intervention": 1.09, "likely_to_fail": 1.13, "failure": 1.17}


def build_timeline(
    result: SimulationResult,
    horizon: int,
    unit: str,
    bands: Optional[dict[str, pd.DataFrame]] = None,
    until_day: Optional[int] = None,
) -> go.Figure:
    """Liquidity buffer, deposits and CET1 ratio by day, with trigger and news markers.

    `bands` (optional, from engine.montecarlo.bands) adds a shaded uncertainty band per panel.
    `until_day` (optional) reveals the run only up to that day. The axes keep the scale of
    the whole run, and the band is drawn only once the last day is shown.
    """
    full_table = result.table
    last_day = int(full_table["day"].max())
    if until_day is None or until_day >= last_day:
        until_day = last_day
    table = full_table[full_table["day"] <= until_day]
    events = [event for event in result.events if event.day <= until_day]
    bands_shown = bands if until_day == last_day else None
    panels = [
        (f"Liquidity buffer ({unit})", "liquidity_buffer", 1.0, "%{y:.2f}"),
        (f"Deposits ({unit})", "deposits_total", 1.0, "%{y:.2f}"),
        ("CET1 ratio (%)", "cet1_ratio", 100.0, "%{y:.1f}%"),
    ]
    fig = make_subplots(
        rows=len(panels),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.07,
        subplot_titles=[title for title, _, _, _ in panels],
    )
    for row, (title, column, factor, value_format) in enumerate(panels, start=1):
        top = float(full_table[column].max())
        if bands is not None:
            top = max(top, float(bands[column]["high"].max()))
        if bands_shown is not None:
            band = bands_shown[column]
            # Upper edge first, then the lower edge filled up to it.
            for edge, fill in (("high", None), ("low", "tonexty")):
                fig.add_trace(
                    go.Scatter(
                        x=band["day"],
                        y=band[edge] * factor,
                        mode="lines",
                        name=f"{title} band {edge}",
                        line=dict(width=0),
                        fill=fill,
                        fillcolor=BAND_COLOR,
                        hoverinfo="skip",
                    ),
                    row=row,
                    col=1,
                )
        fig.add_trace(
            go.Scatter(
                x=table["day"],
                y=table[column] * factor,
                mode="lines",
                name=title,
                line=dict(color=LINE_COLOR, width=2),
                hovertemplate="Day %{x}: " + value_format + "<extra></extra>",
            ),
            row=row,
            col=1,
        )
        # Fixed scale, so the axes do not jump while the days are revealed.
        fig.update_yaxes(range=[0, top * factor * 1.05 or 1], row=row, col=1)

    news = [event for event in events if event.kind == "news"]
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

    for event in events:
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
        height=600,
        showlegend=False,
        margin=dict(l=10, r=10, t=110, b=10),
        hovermode="x unified",
    )
    return fig


def build_failure_distribution(counts: pd.DataFrame, horizon: int) -> go.Figure:
    """Number of Monte Carlo runs reaching payments default on each day, plus a bar for the rest.

    `counts` comes from engine.montecarlo.failure_day_counts.
    """
    no_failure_x = horizon + 3
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=counts["day"],
            y=counts["runs"],
            name="Runs defaulting on this day",
            marker=dict(color=LINE_COLOR),
            hovertemplate="Day %{x}: %{y} runs<extra></extra>",
        )
    )
    fig.add_trace(
        go.Bar(
            x=[no_failure_x],
            y=[counts.attrs["no_failure"]],
            name="No default",
            marker=dict(color=NEWS_COLOR),
            hovertemplate=f"No payments default within {horizon} days: " + "%{y} runs<extra></extra>",
        )
    )
    ticks = list(range(0, horizon + 1, 5))
    fig.update_xaxes(
        title_text="Payments default day",
        tickvals=ticks + [no_failure_x],
        ticktext=[str(t) for t in ticks] + ["No default"],
        range=[-0.75, no_failure_x + 0.75],
    )
    fig.update_yaxes(title_text="Number of runs", rangemode="tozero")
    fig.update_layout(height=320, showlegend=False, bargap=0.25, margin=dict(l=10, r=10, t=20, b=10))
    return fig


def build_contagion_chart(contagion: ContagionResult, horizon: int) -> go.Figure:
    """Second bank with and without contagion, with the days the shocks arrive."""
    # Levels barely move on a zero-based axis, so the chart shows the mechanism (panic
    # multiplier) and its effect (share of deposits lost) instead.
    panels = [
        ("Second bank: panic multiplier", "panic_multiplier"),
        ("Second bank: deposits lost since the start (%)", "cum_deposit_loss_pct"),
    ]
    runs = [
        ("Without contagion", contagion.second_alone.table, dict(color=NEWS_COLOR, width=2, dash="dot")),
        ("With contagion", contagion.second.table, dict(color=LINE_COLOR, width=2)),
    ]
    fig = make_subplots(
        rows=len(panels),
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.12,
        subplot_titles=[title for title, _ in panels],
    )
    for row, (_, column) in enumerate(panels, start=1):
        for name, table, line in runs:
            fig.add_trace(
                go.Scatter(
                    x=table["day"],
                    y=table[column],
                    mode="lines",
                    name=name,
                    legendgroup=name,
                    showlegend=row == 1,
                    line=line,
                    hovertemplate=name + ", day %{x}: %{y:.2f}<extra></extra>",
                ),
                row=row,
                col=1,
            )
        fig.update_yaxes(rangemode="tozero", row=row, col=1)
    for index, event in enumerate(contagion.events):
        fig.add_vline(x=event.day, line=dict(color=NEWS_COLOR, width=1, dash="dash"))
        fig.add_annotation(
            x=event.day,
            y=1.08 + 0.05 * index,
            xref="x",
            yref="paper",
            text=f"Day {event.day}: shock {event.panic_shock:.3f}",
            showarrow=False,
            xanchor="left",
            yanchor="bottom",
            font=dict(size=12),
        )
    fig.update_xaxes(range=[0, horizon], dtick=5)
    fig.update_xaxes(title_text="Day", row=len(panels), col=1)
    fig.update_layout(
        height=520,
        margin=dict(l=10, r=10, t=110, b=10),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, x=0),
    )
    return fig
