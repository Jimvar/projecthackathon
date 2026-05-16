"""Executive-report generator tests.

The helper lives in `report.py` so it can be tested without spinning
up Streamlit. We stub the LLM client and assert on the prompt the
helper would send, and on how it composes the session transcript.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from report import (  # noqa: E402
    PROMPT_PATH,
    build_session_history,
    generate_executive_report,
    render_prompt,
)


@dataclass
class _StubLLMResponse:
    text: str | None


@dataclass
class _StubLLM:
    canned: str = "## Executive Summary\n\nAll systems nominal."
    seen: list[Any] = None
    force_text_seen: bool = False

    def __post_init__(self) -> None:
        if self.seen is None:
            self.seen = []

    def generate(self, messages, force_text: bool = False) -> _StubLLMResponse:
        self.seen.append(list(messages))
        self.force_text_seen = force_text
        return _StubLLMResponse(text=self.canned)


# ---------------------------------------------------------------------- build_session_history


def test_build_session_history_renders_user_and_assistant():
    history = [
        {"role": "user", "text": "Greek vs English split?"},
        {
            "role": "assistant",
            "explanation": "Greek dominates the mix.",
            "panels": [
                {"sql": "SELECT main_language, COUNT(*) FROM v_conversations GROUP BY 1"},
            ],
        },
    ]
    transcript = build_session_history(history)
    assert "[User Question]: Greek vs English split?" in transcript
    assert "[System Analyst Insight]: Greek dominates the mix." in transcript
    assert "[Executed SQL Query]:" in transcript
    assert "SELECT main_language, COUNT(*) FROM v_conversations" in transcript


def test_build_session_history_skips_prior_report_turns():
    """Generating a 2nd report must not feed the 1st report back as an
    'analyst insight'. Otherwise the LLM treats it as data and the
    second briefing inherits the first's conclusions verbatim."""
    history = [
        {"role": "user", "text": "Q1"},
        {"role": "assistant", "explanation": "A1.", "panels": []},
        {
            "role": "assistant",
            "explanation": "## Executive Summary (1)\n\nLeak-marker phrase.",
            "is_report": True,
        },
        {"role": "user", "text": "Q2"},
        {"role": "assistant", "explanation": "A2.", "panels": []},
    ]
    transcript = build_session_history(history)
    assert "Leak-marker phrase" not in transcript
    assert "Q1" in transcript and "A1" in transcript
    assert "Q2" in transcript and "A2" in transcript


def test_build_session_history_collects_all_panel_sqls():
    """Multi-panel turns must contribute every panel's SQL, not just
    the back-compat first one in `turn['sql']`."""
    history = [
        {"role": "user", "text": "How is the bot doing?"},
        {
            "role": "assistant",
            "explanation": "Headline KPIs + trend.",
            # The legacy back-compat field only has the first panel's SQL.
            "sql": "SELECT 1 AS containment",
            "panels": [
                {"sql": "SELECT 1 AS containment", "chart": {"type": "kpi"}},
                {"sql": "SELECT 2 AS csat",        "chart": {"type": "kpi"}},
                {"sql": "SELECT 3 AS aht",          "chart": {"type": "kpi"}},
                {"sql": "SELECT start_date, COUNT(*) FROM v_conversations GROUP BY 1",
                 "chart": {"type": "line"}},
            ],
        },
    ]
    transcript = build_session_history(history)
    assert "SELECT 1 AS containment" in transcript
    assert "SELECT 2 AS csat" in transcript
    assert "SELECT 3 AS aht" in transcript
    assert "SELECT start_date, COUNT(*)" in transcript
    # And no duplication of the first panel's SQL just because it also
    # lives in the back-compat `sql` field.
    assert transcript.count("SELECT 1 AS containment") == 1


def test_build_session_history_handles_empty_history():
    assert build_session_history([]) == ""
    assert build_session_history(None) == ""


def test_build_session_history_ignores_non_dict_entries():
    """The Streamlit state could in theory contain malformed turns. The
    builder should skip them rather than crash."""
    history = [
        None,
        "not a dict",
        {"role": "user", "text": "real question"},
    ]
    transcript = build_session_history(history)
    assert "real question" in transcript


# ---------------------------------------------------------------------- render_prompt


def test_render_prompt_uses_real_template_when_available():
    """If prompts/report_prompt.md exists, render_prompt must load it
    and substitute the session block — not fall back to the inline default."""
    assert PROMPT_PATH.exists(), "prompts/report_prompt.md is missing"
    rendered = render_prompt([
        {"role": "user", "text": "What's the bot doing?"},
        {"role": "assistant", "explanation": "Mostly fine.", "panels": []},
    ])
    # Anchors that live in the real prompt, not the fallback.
    assert "Senior Business Intelligence Analyst" in rendered
    assert "EXECUTIVE SUMMARY" in rendered
    assert "STRATEGIC & OPERATIONAL RECOMMENDATIONS" in rendered
    # Session block substituted in.
    assert "What's the bot doing?" in rendered
    assert "Mostly fine." in rendered
    # Placeholder fully consumed.
    assert "{session_history}" not in rendered


def test_render_prompt_handles_empty_history():
    rendered = render_prompt([])
    assert "no session activity yet" in rendered.lower()
    assert "{session_history}" not in rendered


# ---------------------------------------------------------------------- generate_executive_report


def test_generate_executive_report_returns_llm_text():
    stub = _StubLLM(canned="## Briefing\n\nAll green.")
    history = [
        {"role": "user", "text": "Ping"},
        {"role": "assistant", "explanation": "Pong.", "panels": []},
    ]
    out = generate_executive_report(history, client=stub)
    assert out == "## Briefing\n\nAll green."
    # The helper must use force_text=True so the LLM doesn't try to
    # call SQL tools when generating the prose report.
    assert stub.force_text_seen is True
    # The single message sent must include the rendered prompt with the
    # transcript substituted.
    sent = stub.seen[0]
    assert len(sent) == 1
    body = sent[0]["text"]
    assert "Ping" in body and "Pong" in body
    assert "Senior Business Intelligence Analyst" in body


def test_generate_executive_report_handles_empty_llm_response():
    """When the LLM returns no text, surface a clear error message
    instead of letting an empty string slip into the UI."""
    stub = _StubLLM(canned="")
    out = generate_executive_report([{"role": "user", "text": "x"}], client=stub)
    assert "failed" in out.lower() or "empty" in out.lower()


def test_generate_executive_report_handles_whitespace_only_response():
    stub = _StubLLM(canned="   \n   ")
    out = generate_executive_report([{"role": "user", "text": "x"}], client=stub)
    assert "failed" in out.lower() or "empty" in out.lower()
