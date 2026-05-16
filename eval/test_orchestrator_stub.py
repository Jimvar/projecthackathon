"""Stub-LLM test that exercises the orchestrator wiring without a real key.

We swap in a hand-scripted LLMClient that emits a `run_sql` tool call
followed by the JSON contract. This confirms:

* The orchestrator passes tool specs to the LLM.
* It dispatches tool calls back through `mcp_tools.call_tool`.
* It feeds tool results back as `role=tool` messages.
* It parses the final JSON contract correctly.

This is the seam where the Streamlit app meets Gemini in production.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_client import LLMResponse, ToolCall  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402


@dataclass
class _Script:
    """Drives an Orchestrator by replaying a canned sequence of responses."""

    responses: list[LLMResponse]
    seen: list[list[dict]]
    model: str = "stub"

    def generate(self, messages, force_text=False):  # noqa: D401, ARG002
        self.seen.append(messages)
        return self.responses.pop(0)


def test_orchestrator_runs_tool_then_returns_contract():
    sql = "SELECT main_language, COUNT(*) AS n FROM v_conversations GROUP BY 1 ORDER BY 2 DESC"
    contract = {
        "sql": sql,
        "chart": {
            "type": "pie",
            "x": "main_language",
            "y": "n",
            "series": None,
            "sort": "desc",
            "top_n": None,
            "title": "Calls by main language",
            "style": {"palette": "default", "thresholds": None},
        },
        "explanation": "Greek dominates the mix.",
    }

    script = _Script(
        seen=[],
        responses=[
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(name="run_sql", arguments={"query": sql})],
                raw=None,
            ),
            LLMResponse(text=json.dumps(contract), tool_calls=[], raw=None),
        ],
    )

    orch = Orchestrator(client=script)  # type: ignore[arg-type]
    log = orch.run("Show me a pie chart of Greek vs English users.")

    assert log.error == ""
    assert log.sql == sql
    assert log.chart_spec["type"] == "pie"
    assert log.explanation.startswith("Greek")
    assert log.tool_calls and log.tool_calls[0]["name"] == "run_sql"
    # Two LLM calls happened — the second one saw the tool result.
    assert len(script.seen) == 2
    second_round = script.seen[1]
    assert any(m["role"] == "tool" for m in second_round)


def test_orchestrator_handles_fenced_json():
    sql = "SELECT COUNT(*) AS n FROM v_conversations"
    contract = {"sql": sql, "chart": {"type": "kpi", "y": "n", "title": "Total calls"}, "explanation": "10000 calls."}
    fenced = f"```json\n{json.dumps(contract)}\n```"

    script = _Script(seen=[], responses=[LLMResponse(text=fenced, tool_calls=[], raw=None)])

    orch = Orchestrator(client=script)  # type: ignore[arg-type]
    log = orch.run("How many calls are in the dataset?")
    assert log.error == ""
    assert log.chart_spec["type"] == "kpi"
    assert log.sql == sql


def test_orchestrator_auto_injects_metric_hint():
    """When the user mentions 'containment', the orchestrator should slip the
    metric definition into the user message before the model sees it."""
    contract = {
        "sql": "SELECT AVG(CASE WHEN call_successful = 'success' THEN 1.0 ELSE 0.0 END) AS containment FROM v_conversations",
        "chart": {"type": "kpi", "y": "containment", "title": "Containment rate"},
        "explanation": "Containment is X.",
    }
    script = _Script(seen=[], responses=[LLMResponse(text=json.dumps(contract), tool_calls=[], raw=None)])
    orch = Orchestrator(client=script)  # type: ignore[arg-type]
    orch.run("What's our containment rate?")

    sent_user_text = next(m["text"] for m in script.seen[0] if m["role"] == "user")
    assert "metric reference" in sent_user_text
    assert "containment_rate" in sent_user_text
