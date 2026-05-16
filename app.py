"""Streamlit chat UI — Phase 1 vertical slice.

Run with:
    uv run streamlit run app.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from db import get_db
from mcp_tools import call_tool
from orchestrator import Orchestrator
from renderer import render

load_dotenv()

st.set_page_config(page_title="NR2Dashboard", page_icon="📊", layout="wide")


# ---------------------------------------------------------------------- cached resources


@st.cache_resource(show_spinner=False)
def _db_singleton():
    return get_db()


@st.cache_resource(show_spinner=False)
def _orchestrator_singleton() -> Orchestrator:
    return Orchestrator()


# ---------------------------------------------------------------------- helpers


def _reset_history() -> None:
    st.session_state.history = []


def _ensure_state() -> None:
    if "history" not in st.session_state:
        st.session_state.history = []
    if "active_source" not in st.session_state:
        st.session_state.active_source = "duckdb"


def _df_from_sql(sql: str) -> pd.DataFrame:
    """Re-run the LLM's SQL to get a DataFrame for plotting.

    We could thread the result through the orchestrator, but re-running is
    cheap, fully cacheable, and keeps the in-memory shape simple.
    """
    if not sql:
        return pd.DataFrame()
    result = call_tool("run_sql", {"query": sql})
    if "error" in result:
        return pd.DataFrame()
    return pd.DataFrame(result.get("rows", []), columns=result.get("columns", []))


# ---------------------------------------------------------------------- sidebar


def _sidebar() -> None:
    st.sidebar.title("NR2Dashboard")
    st.sidebar.caption("Natural-language → dashboard")

    # Active data source
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

    # Model info
    orch = _orchestrator_singleton()
    st.sidebar.text(f"Model: {orch.client.model}")

    st.sidebar.divider()
    if st.sidebar.button("Clear conversation", use_container_width=True):
        _reset_history()
        st.rerun()

    with st.sidebar.expander("Schema cheatsheet", expanded=False):
        st.markdown(
            "- **v_conversations** — one row / call\n"
            "- **v_turns** — one row / turn\n"
            "- **v_evaluations** — 8 criteria per call (long)\n"
            "- **v_data_collection** — 14 fields per call (long)\n"
            "- **v_tool_calls** — one row / tool invocation\n"
            "- **v_conv_with_intent** — v_conversations + first user intent\n"
            "- **v_eval_pivot / v_dc_pivot** — wide forms of the long views\n"
        )


# ---------------------------------------------------------------------- main


def main() -> None:
    _ensure_state()
    _db_singleton()  # warms the DuckDB connection
    _sidebar()

    st.title("NR2Dashboard")
    st.caption(
        "Ask the voicebot dataset anything — in English or Greek. "
        "Try: *Show me a pie chart of Greek vs English users.*"
    )

    # Replay history first so the chat stays anchored.
    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            if turn["role"] == "user":
                st.markdown(turn["text"])
            else:
                if turn.get("error"):
                    st.error(turn["error"])
                if turn.get("explanation"):
                    st.markdown(turn["explanation"])
                if turn.get("chart") and turn.get("sql"):
                    df = _df_from_sql(turn["sql"])
                    if not df.empty:
                        fig = render({"chart": turn["chart"]}, df)
                        st.plotly_chart(fig, use_container_width=True)
                if turn.get("sql"):
                    with st.expander("Show SQL"):
                        st.code(turn["sql"], language="sql")

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
                turnlog = orch.run(user_text, history=st.session_state.history[:-1])
            except Exception as e:  # pragma: no cover - shown live in UI
                st.error(f"Orchestrator error: {e}")
                st.session_state.history.append(
                    {"role": "assistant", "error": str(e), "explanation": "", "sql": "", "chart": {}}
                )
                return

        if turnlog.error:
            st.error(turnlog.error)
        if turnlog.explanation:
            st.markdown(turnlog.explanation)
        if turnlog.sql and turnlog.chart_spec:
            df = _df_from_sql(turnlog.sql)
            if df.empty:
                st.warning("Query returned no rows.")
            else:
                fig = render({"chart": turnlog.chart_spec}, df)
                st.plotly_chart(fig, use_container_width=True)
        if turnlog.sql:
            with st.expander("Show SQL"):
                st.code(turnlog.sql, language="sql")

        st.session_state.history.append(
            {
                "role": "assistant",
                "text": turnlog.explanation,
                "explanation": turnlog.explanation,
                "sql": turnlog.sql,
                "chart": turnlog.chart_spec or {},
                "error": turnlog.error,
            }
        )


if __name__ == "__main__":
    main()
