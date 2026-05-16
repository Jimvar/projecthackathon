"""Renderer + annotations tests for the multi-panel work.

Run with:
    uv run pytest eval/test_renderer.py -q
"""

from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from annotations import (  # noqa: E402
    annotate,
    get_release_date,
    reset_release_date_cache,
)
from renderer import (  # noqa: E402
    MAX_PANELS,
    choose_layout,
    extract_panels,
    render,
    render_panels,
)


# ---------------------------------------------------------------------- contract back-compat


def test_extract_panels_normalizes_single_chart_shape():
    """Legacy `{sql, chart}` is treated as a one-panel list."""
    spec = {
        "sql": "SELECT 1 AS n",
        "chart": {"type": "kpi", "y": "n", "title": "x"},
    }
    panels = extract_panels(spec)
    assert len(panels) == 1
    assert panels[0]["sql"] == "SELECT 1 AS n"
    assert panels[0]["chart"]["type"] == "kpi"


def test_extract_panels_prefers_panels_list():
    """When both shapes are present, `panels` wins."""
    spec = {
        "sql": "SELECT 99",
        "chart": {"type": "kpi"},
        "panels": [
            {"sql": "SELECT 1", "chart": {"type": "kpi"}},
            {"sql": "SELECT 2", "chart": {"type": "line"}},
        ],
    }
    panels = extract_panels(spec)
    assert len(panels) == 2
    assert panels[0]["sql"] == "SELECT 1"


def test_extract_panels_truncates_to_max():
    """Anything beyond MAX_PANELS is dropped on the floor."""
    spec = {"panels": [
        {"sql": f"SELECT {i}", "chart": {"type": "kpi"}}
        for i in range(MAX_PANELS + 3)
    ]}
    panels = extract_panels(spec)
    assert len(panels) == MAX_PANELS


# ---------------------------------------------------------------------- layout

def test_choose_layout_single():
    assert choose_layout([{"chart": {"type": "bar"}}]) == "single"


def test_choose_layout_all_kpi_one_row():
    panels = [{"chart": {"type": "kpi"}}] * 3
    assert choose_layout(panels) == "row"


def test_choose_layout_kpi_strip_plus_chart():
    panels = [
        {"chart": {"type": "kpi"}},
        {"chart": {"type": "kpi"}},
        {"chart": {"type": "line"}},
    ]
    assert choose_layout(panels) == "kpi_strip+chart"


def test_choose_layout_two_charts_row():
    panels = [{"chart": {"type": "bar"}}, {"chart": {"type": "line"}}]
    assert choose_layout(panels) == "row"


def test_choose_layout_three_charts_grid():
    panels = [{"chart": {"type": "bar"}}] * 3
    assert choose_layout(panels) == "grid"


# ---------------------------------------------------------------------- render_panels happy path


def test_render_panels_happy_path():
    """A KPI + bar panel should produce two figures of the right types."""
    spec = {
        "panels": [
            {"sql": "_", "chart": {"type": "kpi", "y": "n", "title": "Total"}},
            {"sql": "_", "chart": {"type": "bar", "x": "label", "y": "n", "title": "By label"}},
        ],
        "explanation": "ok",
    }
    dfs = [
        pd.DataFrame({"n": [42]}),
        pd.DataFrame({"label": ["a", "b", "c"], "n": [1, 2, 3]}),
    ]
    rendered = render_panels(spec, dfs, explanation="ok")
    assert len(rendered) == 2
    types = [t for t, _ in rendered]
    assert types == ["kpi", "bar"]
    for _, fig in rendered:
        assert fig is not None


def test_render_panels_empty_df_returns_placeholder():
    """A panel whose SQL returned zero rows gets a placeholder figure
    instead of crashing."""
    spec = {"panels": [
        {"sql": "_", "chart": {"type": "line", "x": "d", "y": "n", "title": "Trend"}},
    ]}
    rendered = render_panels(spec, [pd.DataFrame(columns=["d", "n"])])
    assert len(rendered) == 1
    chart_type, fig = rendered[0]
    assert chart_type == "line"
    assert fig is not None
    # Placeholder figure has the "No data" annotation.
    annotations = [a.text for a in (fig.layout.annotations or [])]
    assert any("No data" in (t or "") for t in annotations)


# ---------------------------------------------------------------------- annotations: mean line


def test_mean_line_added_to_bar_with_numeric_y():
    df = pd.DataFrame({"region": ["a", "b", "c", "d"], "n": [10, 20, 30, 40]})
    fig = render({"chart": {"type": "bar", "x": "region", "y": "n"}}, df)
    fig = annotate(fig, {"chart": {"type": "bar", "x": "region", "y": "n"}}, df)
    shapes = list(fig.layout.shapes or ())
    annotations = [a.text for a in (fig.layout.annotations or [])]
    assert len(shapes) >= 1
    assert any("avg" in (t or "") for t in annotations)


def test_mean_line_skipped_for_pie():
    df = pd.DataFrame({"lang": ["el", "en"], "n": [7694, 2306]})
    fig = render({"chart": {"type": "pie", "x": "lang", "y": "n"}}, df)
    before = len(list(fig.layout.shapes or ()))
    fig = annotate(fig, {"chart": {"type": "pie", "x": "lang", "y": "n"}}, df)
    after = len(list(fig.layout.shapes or ()))
    assert after == before


def test_mean_line_skipped_for_multi_series():
    """Multi-series charts have multiple lines/bars per x; a single mean
    across all of them would be misleading."""
    df = pd.DataFrame({
        "region": ["a", "a", "b", "b"],
        "n": [10, 20, 30, 40],
        "version": ["v1", "v2", "v1", "v2"],
    })
    fig = render({"chart": {"type": "bar", "x": "region", "y": "n", "series": "version"}}, df)
    before = len(list(fig.layout.shapes or ()))
    fig = annotate(fig, {"chart": {"type": "bar", "x": "region", "y": "n", "series": "version"}}, df)
    after = len(list(fig.layout.shapes or ()))
    assert after == before


def test_annotation_opt_out():
    df = pd.DataFrame({"region": ["a", "b", "c"], "n": [10, 20, 30]})
    fig = render({"chart": {"type": "bar", "x": "region", "y": "n"}}, df)
    before = len(list(fig.layout.shapes or ()))
    fig = annotate(
        fig,
        {"chart": {"type": "bar", "x": "region", "y": "n",
                   "style": {"annotations": False}}},
        df,
    )
    after = len(list(fig.layout.shapes or ()))
    assert after == before


# ---------------------------------------------------------------------- annotations: release marker


def test_release_date_is_real_date():
    """The release date is derived from the dataset; must be in 2026
    (the dataset's actual window)."""
    reset_release_date_cache()
    release = get_release_date()
    assert isinstance(release, dt.date), release
    assert release.year == 2026


def test_release_marker_added_when_x_covers_boundary():
    reset_release_date_cache()
    release = get_release_date()
    # Build a date range that brackets the release.
    dates = [release - dt.timedelta(days=10) + dt.timedelta(days=i)
             for i in range(30)]
    df = pd.DataFrame({"start_date": dates, "n": list(range(30))})
    fig = render({"chart": {"type": "line", "x": "start_date", "y": "n"}}, df)
    fig = annotate(fig, {"chart": {"type": "line", "x": "start_date", "y": "n"}}, df)
    annotations = [a.text for a in (fig.layout.annotations or [])]
    assert any("v2.3.0 released" in (t or "") for t in annotations)


def test_release_marker_skipped_for_categorical_x():
    df = pd.DataFrame({"region": ["a", "b", "c"], "n": [10, 20, 30]})
    fig = render({"chart": {"type": "bar", "x": "region", "y": "n"}}, df)
    fig = annotate(fig, {"chart": {"type": "bar", "x": "region", "y": "n"}}, df)
    annotations = [a.text for a in (fig.layout.annotations or [])]
    assert not any("v2.3.0" in (t or "") for t in annotations)


# ---------------------------------------------------------------------- annotations: outliers


def test_outlier_highlights_added_when_anomaly_keyword():
    dates = [dt.date(2026, 2, 1) + dt.timedelta(days=i) for i in range(20)]
    values = [10] * 19 + [500]  # one obvious outlier
    df = pd.DataFrame({"d": dates, "v": values})
    fig = render({"chart": {"type": "line", "x": "d", "y": "v"}}, df)
    before = len(fig.data)
    fig = annotate(
        fig,
        {"chart": {"type": "line", "x": "d", "y": "v"}},
        df,
        explanation="What's weird about this trend?",
    )
    after = len(fig.data)
    assert after == before + 1, "outlier highlight trace should be added"


def test_outlier_highlights_skipped_without_keyword():
    dates = [dt.date(2026, 2, 1) + dt.timedelta(days=i) for i in range(20)]
    df = pd.DataFrame({"d": dates, "v": [10] * 19 + [500]})
    fig = render({"chart": {"type": "line", "x": "d", "y": "v"}}, df)
    before = len(fig.data)
    fig = annotate(
        fig,
        {"chart": {"type": "line", "x": "d", "y": "v"}},
        df,
        explanation="Daily volume for the last 20 days.",
    )
    after = len(fig.data)
    assert after == before


def test_outlier_highlights_recognize_greek_keyword():
    dates = [dt.date(2026, 2, 1) + dt.timedelta(days=i) for i in range(20)]
    df = pd.DataFrame({"d": dates, "v": [10] * 19 + [500]})
    fig = render({"chart": {"type": "line", "x": "d", "y": "v"}}, df)
    before = len(fig.data)
    fig = annotate(
        fig,
        {"chart": {"type": "line", "x": "d", "y": "v"}},
        df,
        explanation="Υπάρχει κάτι περίεργο στις τελευταίες 20 ημέρες;",
    )
    after = len(fig.data)
    assert after == before + 1


# ---------------------------------------------------------------------- tag_turn / eval_grading


def test_tag_turn_passes_on_full_multi_panel():
    """All panels render → overall pass."""
    from dataclasses import dataclass, field
    from eval_grading import tag_turn

    @dataclass
    class _StubLog:
        user_message: str = ""
        sql: str = ""
        chart_spec: dict = field(default_factory=dict)
        panels: list = field(default_factory=list)
        explanation: str = ""
        error: str = ""

    log = _StubLog(
        explanation="ok",
        panels=[
            {"sql": "SELECT 1 AS n", "chart": {"type": "kpi", "y": "n"}},
            {"sql": "SELECT 2 AS n", "chart": {"type": "kpi", "y": "n"}},
        ],
    )
    status, reason = tag_turn(log)
    assert status == "pass", reason
    assert "2/2" in reason or reason == "ok"


def test_tag_turn_partial_when_one_panel_bad():
    """One panel's SQL fails → overall partial, others still counted."""
    from dataclasses import dataclass, field
    from eval_grading import tag_turn

    @dataclass
    class _StubLog:
        user_message: str = ""
        sql: str = ""
        chart_spec: dict = field(default_factory=dict)
        panels: list = field(default_factory=list)
        explanation: str = ""
        error: str = ""

    log = _StubLog(
        explanation="ok",
        panels=[
            {"sql": "SELECT 1 AS n", "chart": {"type": "kpi", "y": "n"}},
            {"sql": "NOT VALID SQL", "chart": {"type": "kpi", "y": "n"}},
        ],
    )
    status, reason = tag_turn(log)
    assert status == "partial", (status, reason)
