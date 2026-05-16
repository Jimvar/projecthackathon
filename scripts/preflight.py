"""60-second pre-demo smoke check.

Runs one representative question from each of the six question shapes plus
the demo's flagship asks. Prints a pass/fail grid and exits non-zero if
anything fails. Use this immediately before showing the system to a judge.

Different from:
  * `scripts/smoke.py` — 5 hand-picked questions, ~30s
  * `scripts/run_eval.py` — the full 40-question suite, ~2-3 min

Usage:
    uv run python scripts/preflight.py
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()


# One per shape + the briefs' verbatim asks + a Greek + a follow-up + a switch.
QUESTIONS = [
    # Verbatim from the SmartRep brief.
    ("brief_pie_languages",
     "Show me a pie chart of Greek vs English users.",
     "distribution"),
    ("brief_this_week",
     "How is the bot doing this week?",
     "open_ended"),
    ("brief_threshold_bar",
     "Show me containment rate by intent type this week.",
     "ranking"),

    # PPTX shape coverage.
    ("trend_csat",
     "Plot daily CSAT for the last 30 days.",
     "trend"),
    ("comparison_v23",
     "Did the v2.3.0 release help authentication-completed pass rate?",
     "comparison"),
    ("anomaly_tool_success",
     "Anything weird about tool success in the last 90 days?",
     "anomaly"),

    # Style override + Greek + multi-source.
    ("donut_blue_style",
     "Break down segments as a donut chart with blue colors.",
     "distribution"),
    ("greek_aht_by_region",
     "Δείξε μου τον μέσο χρόνο κλήσης ανά περιοχή.",
     "ranking"),
]


VALID_CHART_TYPES = {
    "bar", "line", "area", "pie", "donut",
    "scatter", "heatmap", "kpi", "table",
}


def _tag(turnlog) -> tuple[str, str]:
    if turnlog.error:
        return "fail", turnlog.error
    if not turnlog.chart_spec:
        return "fail", "no chart spec"
    if (turnlog.chart_spec.get("type") or "").lower() not in VALID_CHART_TYPES:
        return "fail", f"bad chart type {turnlog.chart_spec.get('type')!r}"
    if not turnlog.explanation:
        return "partial", "empty explanation"
    if not turnlog.sql:
        if "?" in turnlog.explanation:
            return "pass", "clarification"
        return "partial", "no SQL"
    from mcp_tools import call_tool
    r = call_tool("run_sql", {"query": turnlog.sql})
    if "error" in r:
        return "fail", f"sql failed: {r['error']}"
    if not r.get("rows"):
        return "partial", "no rows"
    return "pass", "ok"


def main() -> int:
    if not (os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or os.getenv("GOOGLE_API_KEY")):
        print("No API key set (GEMINI_API_KEY or OPENAI_API_KEY).")
        return 2

    from orchestrator import Orchestrator

    orch = Orchestrator()
    print(
        f"Preflight on provider={orch.client.provider} model={orch.client.model} "
        f"with {len(QUESTIONS)} questions\n"
    )

    started = time.monotonic()
    counts = {"pass": 0, "partial": 0, "fail": 0}
    for i, (qid, q, shape) in enumerate(QUESTIONS, 1):
        log = orch.run(q)
        status, reason = _tag(log)
        counts[status] += 1
        glyph = {"pass": "OK  ", "partial": "PART", "fail": "FAIL"}[status]
        chart_type = (log.chart_spec or {}).get("type") or "-"
        print(
            f"{glyph} [{i}/{len(QUESTIONS)}] {qid:25s} {shape:13s} "
            f"{chart_type:7s} {log.latency_ms:>5d} ms — {reason}"
        )

    elapsed = time.monotonic() - started
    print()
    print(
        f"{counts['pass']}/{len(QUESTIONS)} pass · "
        f"{counts['partial']} partial · {counts['fail']} fail · "
        f"{elapsed:.1f}s total"
    )
    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
