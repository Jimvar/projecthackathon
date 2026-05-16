"""Streamlit chat UI.

Run with:
    uv run streamlit run app.py
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

from db import get_db
from mcp_tools import call_tool
from orchestrator import Orchestrator
from renderer import choose_layout, render_panels

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


def _normalize_panels(turn: dict) -> list[dict]:
    """Return the turn's panel list, falling back to the legacy single-chart shape."""
    panels = turn.get("panels")
    if panels:
        return panels
    chart = turn.get("chart") or {}
    sql = turn.get("sql") or ""
    if chart:
        return [{"sql": sql, "chart": chart}]
    return []


def _prepare_panel_figures(turn: dict) -> list[tuple[str, object, str]]:
    """Run each panel's SQL and build its figure once per turn.

    Returns ``[(chart_type, fig_or_None, err_or_empty), ...]``. Results
    are cached on the turn dict so Streamlit re-renders don't re-do the
    SQL or rebuild the Plotly figure.
    """
    if "_panel_figs" in turn:
        return turn["_panel_figs"]

    panels = _normalize_panels(turn)
    spec = {"panels": panels, "explanation": turn.get("explanation", "")}
    dfs: list[pd.DataFrame] = []
    errs: list[str] = []
    for panel in panels:
        df, err = _df_from_sql(panel.get("sql", ""))
        dfs.append(df)
        errs.append(err or "")

    rendered = render_panels(
        spec, dfs, explanation=turn.get("explanation", "")
    )
    out: list[tuple[str, object, str]] = [
        (chart_type, fig, err)
        for (chart_type, fig), err in zip(rendered, errs)
    ]
    turn["_panel_figs"] = out
    return out


def _lay_out_panels(turn: dict, panel_figs: list[tuple[str, object, str]]) -> None:
    """Place panel figures on screen per the chosen layout."""
    panels = _normalize_panels(turn)
    layout = turn.get("layout") or "auto"
    if layout == "auto":
        layout = choose_layout(panels)

    def _render(idx: int) -> None:
        chart_type, fig, err = panel_figs[idx]
        if err:
            st.warning(f"Panel {idx + 1}: {err}")
        elif fig is None:
            st.info("No data.")
        else:
            st.plotly_chart(fig, use_container_width=True)

    n = len(panel_figs)
    if n == 0:
        return
    if layout == "single" or n == 1:
        _render(0)
        return
    if layout == "row":
        cols = st.columns(n)
        for col, i in zip(cols, range(n)):
            with col:
                _render(i)
        return
    if layout == "kpi_strip+chart":
        kpi_idx = [i for i, (t, _, _) in enumerate(panel_figs) if t == "kpi"]
        rest_idx = [i for i in range(n) if i not in kpi_idx]
        if kpi_idx:
            cols = st.columns(len(kpi_idx))
            for col, i in zip(cols, kpi_idx):
                with col:
                    _render(i)
        for i in rest_idx:
            _render(i)
        return
    # "grid" — pairs per row.
    for start in range(0, n, 2):
        cols = st.columns(2)
        for col, i in zip(cols, range(start, min(start + 2, n))):
            with col:
                _render(i)


def _render_assistant_turn(turn: dict) -> None:
    if turn.get("error"):
        st.error(turn["error"])
    if turn.get("explanation"):
        st.markdown(turn["explanation"])

    panels = _normalize_panels(turn)
    if panels:
        panel_figs = _prepare_panel_figures(turn)
        _lay_out_panels(turn, panel_figs)

    # Show SQL — every panel's query, separated by a blank line.
    sqls = [p.get("sql", "") for p in panels if p.get("sql")]
    if sqls:
        with st.expander(f"Show SQL ({len(sqls)} {'query' if len(sqls) == 1 else 'queries'})"):
            for i, sql in enumerate(sqls, 1):
                if len(sqls) > 1:
                    st.caption(f"Panel {i}")
                st.code(sql, language="sql")

    # Copy-as-markdown panel: turns the answer + SQL into one
    # paste-ready block for sharing on Slack / a ticket / a PR.
    if turn.get("explanation") or sqls:
        with st.expander("Copy as Markdown"):
            md_parts: list[str] = []
            if turn.get("explanation"):
                md_parts.append(turn["explanation"])
            for sql in sqls:
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
            # Back-compat fields used elsewhere (logging, replay of older turns).
            "sql": turnlog.sql,
            "chart": turnlog.chart_spec or {},
            # The full panel set (1 panel = single chart, up to MAX_PANELS).
            "panels": turnlog.panels,
            "layout": turnlog.layout,
            "error": turnlog.error,
        }
        _render_assistant_turn(turn)
        st.session_state.history.append(turn)


if __name__ == "__main__":
    main()
