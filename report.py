"""Executive-report generator.

`generate_executive_report(history, client=None)` walks the Streamlit
session's chat history, builds a transcript, loads
`prompts/report_prompt.md`, fills in the session block, and asks the
configured LLM (via `make_llm_client`) for a polished briefing.

Kept out of `app.py` so it can be unit-tested without spinning up
Streamlit. The Streamlit shell just imports `generate_executive_report`
and surfaces the button + download.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from llm_client import make_llm_client

PROMPT_PATH = Path(__file__).parent / "prompts" / "report_prompt.md"

_FALLBACK_PROMPT = (
    "You are a Senior Business Intelligence Analyst. Compile an "
    "executive briefing from the analytics session transcript below.\n\n"
    "{session_history}"
)


def _all_sqls_for_turn(turn: dict) -> list[str]:
    """Return every SQL query an assistant turn produced.

    Multi-panel turns (Phase 6+) hold their queries in `panels`; single-
    chart turns hold one query in the legacy `sql` field. We accept
    either and never duplicate.
    """
    panels = turn.get("panels") or []
    sqls: list[str] = [
        p.get("sql", "")
        for p in panels
        if isinstance(p, dict) and p.get("sql")
    ]
    legacy_sql = (turn.get("sql") or "").strip()
    if legacy_sql and legacy_sql not in sqls:
        sqls.insert(0, legacy_sql)
    return sqls


def build_session_history(history: Iterable[dict]) -> str:
    """Render the chat history into a transcript the LLM can read.

    Skips prior report turns (`is_report=True`) — otherwise asking for a
    2nd report would feed the 1st one back as an "analyst insight" and
    derail the model.
    """
    parts: list[str] = []
    for turn in history or []:
        if not isinstance(turn, dict):
            continue
        if turn.get("is_report"):
            continue
        role = turn.get("role")
        if role == "user":
            text = (turn.get("text") or "").strip()
            if text:
                parts.append(f"[User Question]: {text}")
        elif role in ("assistant", "model"):
            explanation = (turn.get("explanation") or turn.get("text") or "").strip()
            if explanation:
                parts.append(f"[System Analyst Insight]: {explanation}")
            for sql in _all_sqls_for_turn(turn):
                parts.append(
                    f"[Executed SQL Query]:\n```sql\n{sql.strip()}\n```"
                )
    return "\n\n".join(parts)


def render_prompt(history: Iterable[dict]) -> str:
    """Fill the report prompt template with the session transcript."""
    template = _FALLBACK_PROMPT
    if PROMPT_PATH.exists():
        try:
            template = PROMPT_PATH.read_text()
        except OSError:
            template = _FALLBACK_PROMPT
    session_history = build_session_history(history)
    if not session_history:
        session_history = "(no session activity yet)"
    return template.replace("{session_history}", session_history)


def generate_executive_report(
    history: Iterable[dict],
    client: Any | None = None,
) -> str:
    """Compose and return the executive briefing Markdown.

    `client` is optional — production callers leave it None and we
    build one via `make_llm_client()`. Tests pass a stub.
    """
    prompt = render_prompt(history)
    llm = client or make_llm_client()
    response = llm.generate(
        [{"role": "user", "text": prompt}],
        force_text=True,
    )
    text = (getattr(response, "text", "") or "").strip()
    return text or "Failed to generate report — the model returned an empty reply."
