"""Streamlit chat UI.

Run with:
    uv run streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from db import get_db
from mcp_tools import call_tool, clear_sql_cache, sql_cache_stats
from orchestrator import Orchestrator
from renderer import render

load_dotenv()

st.set_page_config(page_title="NR2Dashboard", page_icon="📊", layout="wide")


SCOPE_LABELS = ["Full window", "Last 30 days", "Last 7 days"]
SCOPE_PHRASE = {
    "Last 30 days": "Use only the last 30 days of data.",
    "Last 7 days": "Use only the last 7 days of data.",
}


# ---------------------------------------------------------------------- cached resources


@st.cache_resource(show_spinner=False)
def _db_singleton():
    return get_db()


@st.cache_resource(show_spinner=False)
def _orchestrator_singleton() -> Orchestrator:
    return Orchestrator()


# ---------------------------------------------------------------------- state


def _reset_history() -> None:
    st.session_state.history = []


def _ensure_state() -> None:
    st.session_state.setdefault("history", [])
    st.session_state.setdefault("active_source", "duckdb")
    st.session_state.setdefault("scope", "Full window")


def _augment_with_scope(text: str) -> str:
    scope = st.session_state.get("scope", "Full window")
    phrase = SCOPE_PHRASE.get(scope)
    if not phrase:
        return text
    return f"{text}\n\n[scope] {phrase}"


def _df_from_sql(sql: str) -> tuple[pd.DataFrame, str | None]:
    """Re-run the LLM's SQL to get a DataFrame for plotting.

    Returns ``(df, error_message)``; error message is non-None if the SQL
    failed safety or execution, so the UI can show it instead of an empty
    chart.
    """
    if not sql:
        return pd.DataFrame(), None
    result = call_tool("run_sql", {"query": sql})
    if "error" in result:
        return pd.DataFrame(), str(result["error"])
    return (
        pd.DataFrame(result.get("rows", []), columns=result.get("columns", [])),
        None,
    )


# ---------------------------------------------------------------------- sidebar


def _sidebar() -> None:
    st.sidebar.title("NR2Dashboard")
    st.sidebar.caption("Natural-language → dashboard")

    source = st.sidebar.radio(
        "Data source",
        options=["duckdb", "jsonl"],
        index=0 if st.session_state.active_source == "duckdb" else 1,
        horizontal=True,
        help="Both sources expose the same column shape. Switch mid-conversation to compare.",
    )
    if source != st.session_state.active_source:
        call_tool("switch_source", {"source": source})
        st.session_state.active_source = source

    orch = _orchestrator_singleton()
    st.sidebar.text(f"Provider: {orch.client.provider}")
    st.sidebar.text(f"Model: {orch.client.model}")
    cache = sql_cache_stats()
    st.sidebar.text(f"SQL cache: {cache['entries']}/{cache['capacity']}")

    st.sidebar.divider()
    btn_cols = st.sidebar.columns(2)
    if btn_cols[0].button("Clear chat", use_container_width=True):
        _reset_history()
        st.rerun()
    if btn_cols[1].button("Clear cache", use_container_width=True):
        clear_sql_cache()
        st.rerun()

    with st.sidebar.expander("Schema cheatsheet", expanded=False):
        st.markdown(
            "- **v_conversations** — one row / call\n"
            "- **v_turns** — one row / turn\n"
            "- **v_evaluations** — 8 criteria per call (long)\n"
            "- **v_data_collection** — 14 fields per call (long)\n"
            "- **v_tool_calls** — one row / tool invocation\n"
            "- **v_conv_with_intent** — v_conversations + first user intent\n"
            "- **v_eval_pivot / v_conv_with_dc** — wide forms of the long views\n"
        )

    with st.sidebar.expander("Try one of these", expanded=False):
        st.markdown(
            "- Show me a pie chart of Greek vs English users.\n"
            "- How is the bot doing this week?\n"
            "- Top 10 intents by AHT.\n"
            "- Δείξε μου τον μέσο χρόνο κλήσης ανά περιοχή.\n"
            "- Anything weird about tool success in the last 90 days?\n"
        )


# ---------------------------------------------------------------------- top scope bar


def _scope_bar() -> None:
    """Time-scope pills that augment the next user message."""
    cols = st.columns([1, 6])
    with cols[0]:
        st.caption("Time scope")
    with cols[1]:
        chosen = st.pills(
            "scope",
            options=SCOPE_LABELS,
            default=st.session_state.get("scope", "Full window"),
            selection_mode="single",
            label_visibility="collapsed",
            key="_scope_pills",
        )
        if chosen and chosen != st.session_state.scope:
            st.session_state.scope = chosen
            st.rerun()


# ---------------------------------------------------------------------- replay + render helpers


def _render_assistant_turn(turn: dict) -> None:
    if turn.get("error"):
        st.error(turn["error"])
    if turn.get("explanation"):
        st.markdown(turn["explanation"])
    chart = turn.get("chart") or {}
    sql = turn.get("sql") or ""
    if chart and sql:
        # Build the figure once per turn and stash it on the turn dict
        # so subsequent re-renders (Streamlit reruns on every input)
        # don't re-execute the SQL and re-build the Plotly figure.
        fig = turn.get("_fig")
        err = turn.get("_err")
        if fig is None and err is None:
            df, err = _df_from_sql(sql)
            if err is None and not df.empty:
                fig = render({"chart": chart}, df)
            turn["_fig"] = fig
            turn["_err"] = err
        if err:
            st.warning(f"Could not render this chart: {err}")
        elif fig is None:
            st.info("Query returned no rows.")
        else:
            st.plotly_chart(fig, use_container_width=True)
    if sql:
        with st.expander("Show SQL"):
            st.code(sql, language="sql")
    # Copy-as-markdown panel: turns the answer + SQL into one
    # paste-ready block for sharing on Slack / a ticket / a PR.
    if turn.get("explanation") or sql:
        with st.expander("Copy as Markdown"):
            md_parts: list[str] = []
            if turn.get("explanation"):
                md_parts.append(turn["explanation"])
            if sql:
                md_parts.append(f"```sql\n{sql.strip()}\n```")
            st.code("\n\n".join(md_parts), language="markdown")


def _replay_history() -> None:
    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            if turn["role"] == "user":
                st.markdown(turn["text"])
            else:
                _render_assistant_turn(turn)


# ---------------------------------------------------------------------- main


def main() -> None:
    _ensure_state()
    _db_singleton()
    _sidebar()

    st.title("📊 NR2Dashboard")
    st.markdown(
        "##### Natural-language → dashboard over the SmartRep voicebot dataset"
    )
    st.caption(
        "Ask anything in English or Greek. Follow-ups, anomaly hunts, and "
        "donut-chart style overrides all welcome."
    )
    # Active-source banner — visible mid-conversation toggle for judges.
    orch = _orchestrator_singleton()
    st.markdown(
        f"**Source:** `{st.session_state.active_source}` &nbsp;·&nbsp; "
        f"**Provider:** `{orch.client.provider}` &nbsp;·&nbsp; "
        f"**Model:** `{orch.client.model}`"
    )
    _scope_bar()

    _replay_history()

    user_text = st.chat_input("Ask a question…")
    if not user_text:
        return

    st.session_state.history.append({"role": "user", "text": user_text})
    with st.chat_message("user"):
        st.markdown(user_text)

    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                orch = _orchestrator_singleton()
                turnlog = orch.run(
                    _augment_with_scope(user_text),
                    history=st.session_state.history[:-1],
                )
            except Exception as e:
                st.error(f"Orchestrator error: {e}")
                st.session_state.history.append(
                    {"role": "assistant", "error": str(e), "explanation": "", "sql": "", "chart": {}}
                )
                return

        turn = {
            "role": "assistant",
            "text": turnlog.explanation,
            "explanation": turnlog.explanation,
            "sql": turnlog.sql,
            "chart": turnlog.chart_spec or {},
            "error": turnlog.error,
        }
        _render_assistant_turn(turn)
        st.session_state.history.append(turn)


if __name__ == "__main__":
    main()
