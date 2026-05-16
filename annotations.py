"""Post-render chart annotations.

`annotate(fig, panel, df)` enriches a Plotly figure with statistical and
contextual overlays the LLM didn't have to think about:

  * **Mean line** — dashed horizontal line at `mean(y)` on bar / line /
    area charts with a single numeric y.
  * **Release marker** — vertical dashed line at the v2.2.1→v2.3.0
    release date on any chart whose x-axis is a date covering the
    boundary. The date is derived from the data on first call.
  * **Outlier highlights** — when a line chart's explanation contains
    an anomaly keyword (English or Greek), points with |z| > 1.5 get
    larger contrasting markers.

Each rule is self-gating: it inspects the chart spec + DataFrame and
either modifies the figure in place or returns it unchanged. Opt-out is
per-panel via `chart.style.annotations = false`.
"""

from __future__ import annotations

import datetime as dt
import threading
from typing import Any

import pandas as pd
import plotly.graph_objects as go


# Cached on first call. Datums:
#   - The v2.3.0 release date (the first start_date where bot_version='2.3.0').
_RELEASE_DATE: dt.date | None = None
_RELEASE_LOCK = threading.Lock()

# Charts where adding a mean horizontal line makes sense.
_MEAN_LINE_CHART_TYPES = frozenset({"bar", "line", "area"})

# Keywords that signal an anomaly question (used to gate outlier highlights).
# Lowercased before comparison; covers EN + EL.
_ANOMALY_KEYWORDS = (
    "weird", "outlier", "outliers", "anomaly", "anomalies", "unusual",
    "spike", "spikes", "drop", "drops", "odd", "off",
    # Greek
    "περίεργο", "ασυνήθιστ", "ανωμαλ",
)

OUTLIER_THRESHOLD = 1.5  # |z| > 1.5σ qualifies


# ---------------------------------------------------------------------- public


def annotate(fig: go.Figure, panel: dict, df: pd.DataFrame,
             explanation: str = "") -> go.Figure:
    """Apply every applicable annotation rule.

    `panel` is one entry from the contract's `panels` list — a dict with
    `chart` (the spec) and optionally `sql`.
    """
    chart = (panel or {}).get("chart") or {}
    style = chart.get("style") or {}
    if style.get("annotations") is False:
        return fig
    if df is None or df.empty:
        return fig

    fig = _maybe_mean_line(fig, chart, df)
    fig = _maybe_release_marker(fig, chart, df)
    fig = _maybe_outlier_highlight(fig, chart, df, explanation)
    return fig


def get_release_date(db=None) -> dt.date | None:
    """Return the cached v2.3.0 release date, computing it once on first call.

    `db` is optional; if omitted we import lazily to avoid a circular import
    at module load.
    """
    global _RELEASE_DATE
    with _RELEASE_LOCK:
        if _RELEASE_DATE is not None:
            return _RELEASE_DATE
        try:
            if db is None:
                from db import get_db  # local import — avoids circular at load
                db = get_db()
            row = db.execute(
                "SELECT MIN(start_date) FROM v_conversations WHERE bot_version = '2.3.0'"
            ).fetchone()
            if row and row[0]:
                _RELEASE_DATE = (
                    row[0] if isinstance(row[0], dt.date) else dt.date.fromisoformat(str(row[0]))
                )
        except Exception:  # pragma: no cover — annotations must never crash a render
            _RELEASE_DATE = None
        return _RELEASE_DATE


def reset_release_date_cache() -> None:
    """For tests: force the next get_release_date() call to recompute."""
    global _RELEASE_DATE
    with _RELEASE_LOCK:
        _RELEASE_DATE = None


# ---------------------------------------------------------------------- rules


def _maybe_mean_line(fig: go.Figure, chart: dict, df: pd.DataFrame) -> go.Figure:
    chart_type = (chart.get("type") or "").lower()
    if chart_type not in _MEAN_LINE_CHART_TYPES:
        return fig
    y = chart.get("y")
    series = chart.get("series")
    # Skip multi-series charts — a single mean across colored groups would
    # be misleading.
    if series and series in df.columns:
        return fig
    if not y or y not in df.columns:
        return fig
    col = df[y]
    if not pd.api.types.is_numeric_dtype(col):
        return fig
    valid = col.dropna()
    if len(valid) < 3:
        return fig
    mean_val = float(valid.mean())
    label = f"avg: {mean_val:,.2f}" if abs(mean_val) < 1_000_000 else f"avg: {mean_val:,.0f}"
    # Build the shape + annotation manually — add_hline/add_vline's
    # `annotation_text` shortcut tries to mean(x) the x-axis to position
    # the annotation, which crashes on datetime axes.
    fig.add_shape(
        type="line",
        xref="x domain", yref="y",
        x0=0, x1=1, y0=mean_val, y1=mean_val,
        line=dict(color="#888", dash="dash", width=1),
    )
    fig.add_annotation(
        xref="x domain", yref="y",
        x=0.99, y=mean_val,
        text=label, showarrow=False,
        font=dict(size=10, color="#666"),
        xanchor="right", yanchor="bottom",
    )
    return fig


def _maybe_release_marker(fig: go.Figure, chart: dict, df: pd.DataFrame) -> go.Figure:
    x = chart.get("x")
    if not x or x not in df.columns:
        return fig
    col = df[x]
    # Detect date-typed x. Accept datetimes, dates, and strings that parse
    # as ISO dates. Skip already-numeric columns to avoid Pandas' inference
    # warning on mixed/non-date values.
    if pd.api.types.is_datetime64_any_dtype(col):
        series = col
    elif col.dtype == object and len(col) and isinstance(col.iloc[0], (dt.date, dt.datetime)):
        series = pd.to_datetime(col, errors="coerce")
    else:
        # Try parsing strings only when they look ISO-ish; otherwise bail.
        sample = str(col.iloc[0]) if len(col) else ""
        if not (len(sample) >= 8 and sample[4:5] in ("-", "/") and sample[:4].isdigit()):
            return fig
        series = pd.to_datetime(col, errors="coerce", format="ISO8601")
    if series.isna().all():
        return fig
    release = get_release_date()
    if release is None:
        return fig
    rel_ts = pd.Timestamp(release)
    if rel_ts < series.min() or rel_ts > series.max():
        return fig
    # Manual shape + annotation; add_vline's annotation_text path can't
    # cope with mixed date/string axes.
    x_val = rel_ts.isoformat()
    fig.add_shape(
        type="line",
        xref="x", yref="y domain",
        x0=x_val, x1=x_val, y0=0, y1=1,
        line=dict(color="#7E57C2", dash="dash", width=1),
    )
    fig.add_annotation(
        xref="x", yref="y domain",
        x=x_val, y=1.02,
        text="v2.3.0 released", showarrow=False,
        font=dict(size=10, color="#7E57C2"),
        xanchor="left", yanchor="bottom",
    )
    return fig


def _maybe_outlier_highlight(
    fig: go.Figure, chart: dict, df: pd.DataFrame, explanation: str
) -> go.Figure:
    if (chart.get("type") or "").lower() != "line":
        return fig
    text = (explanation or "").lower()
    if not any(kw in text for kw in _ANOMALY_KEYWORDS):
        return fig
    x = chart.get("x")
    y = chart.get("y")
    if not x or not y or x not in df.columns or y not in df.columns:
        return fig
    col = df[y]
    if not pd.api.types.is_numeric_dtype(col):
        return fig
    valid = col.dropna()
    if len(valid) < 4:
        return fig
    mean = float(valid.mean())
    std = float(valid.std())
    if std == 0 or pd.isna(std):
        return fig
    z = (col - mean) / std
    outlier_mask = z.abs() > OUTLIER_THRESHOLD
    if not outlier_mask.any():
        return fig
    out_df = df[outlier_mask]
    fig.add_trace(
        go.Scatter(
            x=out_df[x],
            y=out_df[y],
            mode="markers",
            marker=dict(size=14, color="#FB8C00", line=dict(width=2, color="#1F1F2E")),
            name="outliers",
            showlegend=True,
            hovertemplate=f"{x}: %{{x}}<br>{y}: %{{y}}<br>(|z| > {OUTLIER_THRESHOLD})<extra></extra>",
        )
    )
    return fig
