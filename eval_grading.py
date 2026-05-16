"""Shared grading helpers used by `scripts/run_eval.py` and
`scripts/preflight.py`.

Lives in the project root rather than under `eval/` so the eval directory
stays focused on test data + pytest fixtures.
"""

from __future__ import annotations

VALID_CHART_TYPES = frozenset({
    "bar", "line", "area", "pie", "donut",
    "scatter", "heatmap", "kpi", "table",
})


def tag_turn(turnlog) -> tuple[str, str]:
    """Score one orchestrator turn.

    Returns ``(status, reason)`` where status is one of "pass", "partial",
    or "fail". The rules collapse across however many panels the turn
    produced:

      * pass    — every panel has valid SQL that returns rows, plus a
                  non-empty explanation.
      * partial — some panels rendered, others didn't (no rows, or no
                  SQL on a non-clarification panel).
      * fail    — orchestrator error, no panels at all, or every panel
                  failed.

    A clarification-only turn (no SQL, KPI chart, explanation contains
    a question mark) counts as pass — that's the intended response to
    questions like "What's our NPS?" when NPS doesn't exist.
    """
    if turnlog.error:
        return "fail", turnlog.error

    panels = list(getattr(turnlog, "panels", None) or [])
    if not panels:
        # Legacy single-chart shape — synthesize a panel from chart_spec/sql
        # so the rest of the logic only has one path.
        if not turnlog.chart_spec:
            return "fail", "no chart spec"
        panels = [{"sql": turnlog.sql, "chart": turnlog.chart_spec}]

    if not turnlog.explanation:
        return "partial", "empty explanation"

    per_panel = [_tag_panel(p, turnlog.explanation) for p in panels]
    passes = sum(1 for s, _ in per_panel if s == "pass")
    fails = sum(1 for s, _ in per_panel if s == "fail")
    partials = sum(1 for s, _ in per_panel if s == "partial")

    if passes == len(panels):
        return "pass", "ok" if len(panels) == 1 else f"{passes}/{len(panels)} panels"
    if passes == 0:
        # Surface the first failure's reason for triage.
        return "fail", per_panel[0][1]
    return "partial", f"{passes}/{len(panels)} panels ok, {partials + fails} not"


def _tag_panel(panel: dict, explanation: str) -> tuple[str, str]:
    chart = panel.get("chart") or {}
    sql = panel.get("sql", "")
    chart_type = (chart.get("type") or "").lower()
    if chart_type not in VALID_CHART_TYPES:
        return "fail", f"unknown chart type {chart_type!r}"
    if not sql:
        if chart_type == "kpi" and "?" in explanation:
            return "pass", "clarification"
        return "partial", "no SQL"
    # Local import — avoids a cycle when orchestrator transitively imports
    # this module via the run_eval script.
    from mcp_tools import call_tool
    res = call_tool("run_sql", {"query": sql})
    if "error" in res:
        return "fail", f"sql failed: {res['error']}"
    if not res.get("rows"):
        return "partial", "sql ran but no rows"
    return "pass", "ok"
