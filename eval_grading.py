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
    or "fail". The status rules are intentionally permissive at the
    edges:

      * pass    — non-empty SQL, recognized chart type, non-empty
                  explanation, AND the SQL still executes through the
                  safety layer and returns rows.
      * partial — produced an answer but missing one of the above
                  (e.g. no SQL, or SQL returns zero rows).
      * fail    — orchestrator error, no chart spec, invalid chart type,
                  or SQL safety/execution failure.

    A clarification-only turn (no SQL, KPI chart, explanation contains
    a question mark) counts as pass — that's the intended response to
    questions like "What's our NPS?" when NPS doesn't exist.
    """
    if turnlog.error:
        return "fail", turnlog.error
    if not turnlog.chart_spec:
        return "fail", "no chart spec"
    chart_type = (turnlog.chart_spec.get("type") or "").lower()
    if chart_type not in VALID_CHART_TYPES:
        return "fail", f"unknown chart type {chart_type!r}"
    if not turnlog.explanation:
        return "partial", "empty explanation"
    if not turnlog.sql:
        if chart_type == "kpi" and "?" in turnlog.explanation:
            return "pass", "clarification (no SQL needed)"
        return "partial", "no SQL produced"

    # Re-run the SQL through the safety layer + executor to confirm it
    # still works. Local import to avoid a cycle when the orchestrator
    # imports this module transitively.
    from mcp_tools import call_tool

    res = call_tool("run_sql", {"query": turnlog.sql})
    if "error" in res:
        return "fail", f"sql failed: {res['error']}"
    if not res.get("rows"):
        return "partial", "sql ran but no rows"
    return "pass", "ok"
