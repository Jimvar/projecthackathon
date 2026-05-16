"""Chart spec → Plotly figure.

Consumes the JSON contract from `orchestrator.py` plus a `pandas.DataFrame`
(built from `run_sql`'s columns/rows) and returns a Plotly figure ready
for `st.plotly_chart`.

Supports the chart families listed in the system prompt's rubric and the
style overrides the PDF brief explicitly tests for:

* `palette` (named color sequence)
* `sort` (asc / desc / none)
* `top_n` (truncation after sort)
* `thresholds` (color bars by value cutoff)
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


PALETTES: dict[str, list[str]] = {
    "default": px.colors.qualitative.Plotly,
    "blue": px.colors.sequential.Blues[2:],
    "green": px.colors.sequential.Greens[2:],
    "purple": px.colors.sequential.Purples[2:],
    "orange": px.colors.sequential.Oranges[2:],
    "red": px.colors.sequential.Reds[2:],
    "viridis": px.colors.sequential.Viridis,
}


def render(spec: dict[str, Any], df: pd.DataFrame) -> go.Figure:
    """Build a Plotly figure for `spec` against `df`.

    A missing/unrecognized chart type falls back to a table.
    """
    chart = (spec or {}).get("chart") or spec or {}
    chart_type = (chart.get("type") or "table").lower()
    style = chart.get("style") or {}
    palette = _palette(style.get("palette"))

    df = _apply_sort_and_topn(df, chart)

    title = chart.get("title") or ""

    if chart_type == "bar":
        return _bar(df, chart, palette, title)
    if chart_type == "line":
        return _line(df, chart, palette, title)
    if chart_type == "area":
        return _area(df, chart, palette, title)
    if chart_type in ("pie", "donut"):
        return _pie(df, chart, palette, title, hole=0.4 if chart_type == "donut" else 0.0)
    if chart_type == "scatter":
        return _scatter(df, chart, palette, title)
    if chart_type == "heatmap":
        return _heatmap(df, chart, title)
    if chart_type == "kpi":
        return _kpi(df, chart, title)
    return _table(df, title)


# ---------------------------------------------------------------------- helpers


def _palette(name: str | None) -> list[str]:
    return PALETTES.get((name or "default").lower(), PALETTES["default"])


def _apply_sort_and_topn(df: pd.DataFrame, chart: dict) -> pd.DataFrame:
    if df.empty:
        return df
    y = chart.get("y")
    sort = (chart.get("sort") or "none").lower()
    top_n = chart.get("top_n")
    if y and y in df.columns and sort in ("asc", "desc"):
        df = df.sort_values(by=y, ascending=(sort == "asc"))
    if top_n and isinstance(top_n, int) and top_n > 0 and len(df) > top_n:
        df = df.head(top_n)
    return df


def _bar_colors(df: pd.DataFrame, chart: dict, palette: list[str]) -> list[str] | None:
    thresholds = (chart.get("style") or {}).get("thresholds")
    if not thresholds:
        return None
    col = thresholds.get("col") or chart.get("y")
    if not col or col not in df.columns:
        return None
    cutoff = thresholds.get("green_above") or thresholds.get("threshold") or 0.0
    colors = [
        ("#7E57C2" if v >= cutoff else "#FB8C00")  # purple ≥ cutoff, orange below — PPTX example
        for v in df[col].tolist()
    ]
    return colors


def _bar(df: pd.DataFrame, chart: dict, palette: list[str], title: str) -> go.Figure:
    x = chart.get("x")
    y = chart.get("y")
    series = chart.get("series")
    if not x or not y or x not in df.columns or y not in df.columns:
        return _table(df, title)
    threshold_colors = _bar_colors(df, chart, palette)
    if series and series in df.columns:
        fig = px.bar(
            df, x=x, y=y, color=series, title=title, color_discrete_sequence=palette
        )
    elif threshold_colors:
        fig = go.Figure(
            data=[go.Bar(x=df[x].astype(str), y=df[y], marker_color=threshold_colors)],
            layout=go.Layout(title=title, xaxis_title=x, yaxis_title=y),
        )
    else:
        fig = px.bar(
            df,
            x=x,
            y=y,
            title=title,
            color_discrete_sequence=palette,
        )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _line(df: pd.DataFrame, chart: dict, palette: list[str], title: str) -> go.Figure:
    x = chart.get("x")
    y = chart.get("y")
    series = chart.get("series")
    if not x or not y or x not in df.columns or y not in df.columns:
        return _table(df, title)
    if series and series in df.columns:
        fig = px.line(df, x=x, y=y, color=series, title=title, color_discrete_sequence=palette)
    else:
        fig = px.line(df, x=x, y=y, title=title, color_discrete_sequence=palette)
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _area(df: pd.DataFrame, chart: dict, palette: list[str], title: str) -> go.Figure:
    x = chart.get("x")
    y = chart.get("y")
    series = chart.get("series")
    if not x or not y or x not in df.columns or y not in df.columns:
        return _table(df, title)
    fig = px.area(
        df,
        x=x,
        y=y,
        color=series if series and series in df.columns else None,
        title=title,
        color_discrete_sequence=palette,
    )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _pie(df: pd.DataFrame, chart: dict, palette: list[str], title: str, hole: float) -> go.Figure:
    x = chart.get("x")
    y = chart.get("y")
    if not x or not y or x not in df.columns or y not in df.columns:
        return _table(df, title)
    fig = px.pie(
        df, names=x, values=y, hole=hole, title=title, color_discrete_sequence=palette
    )
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _scatter(df: pd.DataFrame, chart: dict, palette: list[str], title: str) -> go.Figure:
    x = chart.get("x")
    y = chart.get("y")
    series = chart.get("series")
    if not x or not y or x not in df.columns or y not in df.columns:
        return _table(df, title)
    fig = px.scatter(
        df,
        x=x,
        y=y,
        color=series if series and series in df.columns else None,
        title=title,
        color_discrete_sequence=palette,
    )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _heatmap(df: pd.DataFrame, chart: dict, title: str) -> go.Figure:
    x = chart.get("x")
    y = chart.get("y")
    series = chart.get("series")  # the numeric value
    cols = [x, y, series]
    if not all(c and c in df.columns for c in cols):
        return _table(df, title)
    pivot = df.pivot_table(index=y, columns=x, values=series, aggfunc="mean")
    fig = go.Figure(
        data=go.Heatmap(z=pivot.values, x=list(pivot.columns), y=list(pivot.index), colorscale="Viridis"),
        layout=go.Layout(title=title, xaxis_title=x, yaxis_title=y),
    )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _kpi(df: pd.DataFrame, chart: dict, title: str) -> go.Figure:
    if df.empty:
        return _table(df, title)
    y = chart.get("y")
    val = df.iloc[0][y] if y and y in df.columns else df.iloc[0, 0]
    label = title or (y or "Value")
    try:
        val_f = float(val)
    except (TypeError, ValueError):
        # Non-numeric scalar — render as a big text annotation. Plotly's
        # Indicator only supports numbers, so we fall back to a blank
        # figure with a single centered annotation.
        fig = go.Figure().add_annotation(
            text=str(val),
            showarrow=False,
            font=dict(size=42),
            x=0.5,
            y=0.5,
        )
        fig.update_layout(
            title=label,
            xaxis_visible=False,
            yaxis_visible=False,
            margin=dict(l=10, r=10, t=40, b=10),
        )
        return fig

    fig = go.Figure(
        go.Indicator(
            mode="number",
            value=val_f,
            title={"text": label},
            number={"valueformat": ",.2f"},
        )
    )
    fig.update_layout(margin=dict(l=10, r=10, t=40, b=10))
    return fig


def _table(df: pd.DataFrame, title: str) -> go.Figure:
    fig = go.Figure(
        data=[
            go.Table(
                header=dict(values=list(df.columns), fill_color="#f0f2f6", align="left"),
                cells=dict(values=[df[c].astype(str) for c in df.columns], align="left"),
            )
        ]
    )
    fig.update_layout(title=title, margin=dict(l=10, r=10, t=40, b=10))
    return fig
