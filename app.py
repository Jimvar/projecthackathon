"""Streamlit chat UI.

Run with:
    uv run streamlit run app.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import db as db_module
from db import MAX_USER_SOURCES, get_db
from mcp_client import get_mcp_client
from mcp_tools import call_tool
from orchestrator import Orchestrator
from renderer import choose_layout, render_panels

load_dotenv()

st.set_page_config(page_title="Raid.AI", page_icon="📊", layout="wide")


SCOPE_LABELS = ["Full window", "Last 30 days", "Last 7 days"]
SCOPE_PHRASE = {
    "Last 30 days": "Use only the last 30 days of data.",
    "Last 7 days": "Use only the last 7 days of data.",
}

# Sample prompts surfaced in the sidebar. Each one is a clickable
# button that submits as if the user had typed it into the chat box.
SAMPLE_PROMPTS = [
    "Show me a pie chart of Greek vs English users.",
    "How is the bot doing this week?",
    "Top 10 intents by AHT.",
    "Δείξε μου τον μέσο χρόνο κλήσης ανά περιοχή.",
    "Anything weird about tool success in the last 90 days?",
]


# ---------------------------------------------------------------------- cached resources


@st.cache_resource(show_spinner=False)
def _db_singleton():
    return get_db()


@st.cache_resource(show_spinner=False)
def _mcp_client_singleton():
    """Long-lived MCP stdio session — spawns mcp_server.py as a subprocess
    on first call and keeps it alive for the lifetime of the Streamlit
    process."""
    return get_mcp_client()


@st.cache_resource(show_spinner=False)
def _orchestrator_singleton() -> Orchestrator:
    # All of the orchestrator's LLM tool calls go over MCP. The renderer's
    # in-process SQL re-execution (`_df_from_sql`) stays direct — it's
    # not a tool call, just a DB query for the Plotly DataFrame.
    mcp = _mcp_client_singleton()
    return Orchestrator(call_tool=mcp.call_tool)


def _sync_to_mcp_server() -> None:
    """Tell the MCP server's DB to drop its connection so the next call
    rebuilds from the shared manifest. Use after any source-mutating op
    (register / unregister / switch). The manifest is the source of
    truth between the two processes; this just nudges the server."""
    try:
        _mcp_client_singleton().call_tool("reload_sources", {})
    except Exception:
        # Don't fail the UI if the server hiccups — the next tool call
        # from the orchestrator will reconnect and resync regardless.
        pass


# ---------------------------------------------------------------------- state


def _reset_history() -> None:
    st.session_state.history = []


def _ensure_state() -> None:
    st.session_state.setdefault("history", [])
    # active_source mirrors db.active_source_id so the radio/selectbox can
    # bind to it. The DB is the source of truth on disk; this is just the
    # widget's current value.
    db = _db_singleton()
    st.session_state.setdefault("active_source", db.active_source_id)
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
    st.sidebar.title("Raid.AI")
    st.sidebar.caption("Natural-language → dashboard")

    _source_picker()
    _import_panel()

    orch = _orchestrator_singleton()
    st.sidebar.text(f"Provider: {orch.client.provider}")
    st.sidebar.text(f"Model: {orch.client.model}")
    st.sidebar.text("Tools: MCP stdio (mcp_server.py)")

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
        st.caption("Click to send.")
        for i, prompt in enumerate(SAMPLE_PROMPTS):
            if st.button(prompt, key=f"_sample_{i}", use_container_width=True):
                # Streamlit's st.chat_input doesn't accept a programmatic
                # value, so we stash the prompt in session_state and the
                # main flow picks it up on the next rerun as if the user
                # had typed it.
                st.session_state["pending_prompt"] = prompt
                st.rerun()


# ---------------------------------------------------------------------- data-source picker + import


def _rebuild_db_cache() -> None:
    """Drop Streamlit's cached Database so the next render re-reads the
    manifest. Pairs with `db.register_source` / `db.unregister_source`,
    which already close the underlying DuckDB connection."""
    _db_singleton.clear()
    db_module.reset_db()


def _source_picker() -> None:
    db = _db_singleton()
    sources = db.sources()
    ids = [s.id for s in sources]
    labels = {
        s.id: f"{s.display_name}" + ("  · built-in" if s.is_builtin else "  · imported")
        for s in sources
    }
    current = st.session_state.active_source
    if current not in ids:
        current = db.active_source_id
        st.session_state.active_source = current
    chosen = st.sidebar.selectbox(
        "Data source",
        options=ids,
        index=ids.index(current),
        format_func=lambda i: labels.get(i, i),
        help="Built-ins ship with the demo. Imported sources sit in data/uploads/.",
        key="_source_selectbox",
    )
    if chosen != db.active_source_id:
        call_tool("switch_source", {"source": chosen})
        _sync_to_mcp_server()
        st.session_state.active_source = chosen


def _import_panel() -> None:
    db = _db_singleton()
    used = db.user_source_count()
    full = used >= MAX_USER_SOURCES
    title = f"Import database ({used}/{MAX_USER_SOURCES})"
    with st.sidebar.expander(title, expanded=False):
        st.caption(
            "Upload a .jsonl or .duckdb file with the same shape as the "
            "built-in dataset. Imports persist while this server runs; "
            "they're lost when the container is reclaimed."
        )
        uploaded = st.file_uploader(
            "File",
            type=["jsonl", "duckdb"],
            accept_multiple_files=False,
            key="_import_uploader",
            disabled=full,
            label_visibility="collapsed",
        )
        default_name = Path(uploaded.name).stem if uploaded else ""
        name = st.text_input(
            "Display name",
            value=default_name,
            placeholder="e.g. Q3 2025 data",
            disabled=full or uploaded is None,
            key="_import_name",
        )
        add = st.button(
            "Add source",
            use_container_width=True,
            disabled=full or uploaded is None,
            type="primary",
        )
        if full:
            st.warning(f"At the cap of {MAX_USER_SOURCES} imports. Remove one to add another.")
        if add and uploaded is not None:
            _handle_import(uploaded, name)

        # List + remove imported sources.
        imported = [s for s in db.sources() if not s.is_builtin]
        if imported:
            st.markdown("**Imported sources**")
            for src in imported:
                col_a, col_b = st.columns([5, 1])
                with col_a:
                    st.text(src.display_name)
                    st.caption(f"`{src.id}`  ·  {src.kind.replace('upload_', '.')}")
                with col_b:
                    if st.button("✕", key=f"_rm_{src.id}", help="Remove this source"):
                        _handle_remove(src.id)


def _handle_import(uploaded, display_name: str) -> None:
    suffix = Path(uploaded.name).suffix.lower()
    if suffix == ".jsonl":
        kind = "upload_jsonl"
    elif suffix == ".duckdb":
        kind = "upload_duckdb"
    else:
        st.error(f"Unsupported extension: {suffix}")
        return

    # Write the bytes to a temp file so register_source can copy from a
    # path. register_source closes the live connection — the next
    # _db_singleton() call rebuilds with the new source materialized.
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(uploaded.getbuffer())
        tmp_path = Path(tmp.name)

    db = _db_singleton()
    try:
        src = db.register_source(display_name, tmp_path, kind)
    except Exception as e:
        st.error(f"Could not register source: {e}")
        try:
            tmp_path.unlink()
        except Exception:
            pass
        return
    finally:
        try:
            tmp_path.unlink()
        except Exception:
            pass

    # Validate by switching to it and running a one-row probe. If it
    # fails, roll the registration back so a broken upload doesn't
    # poison the session.
    _rebuild_db_cache()
    db = _db_singleton()
    try:
        db.switch_source(src.id)
        db.execute("SELECT conversation_id FROM v_conversations_active LIMIT 1").fetchone()
    except Exception as e:
        st.error(f"Imported file failed validation: {e}")
        db.switch_source("duckdb")
        db.unregister_source(src.id)
        _rebuild_db_cache()
        return

    st.session_state.active_source = src.id
    _sync_to_mcp_server()
    st.success(f"Imported `{src.display_name}` — now active.")
    st.rerun()


def _handle_remove(source_id: str) -> None:
    db = _db_singleton()
    try:
        db.unregister_source(source_id)
    except Exception as e:
        st.error(f"Could not remove source: {e}")
        return
    _rebuild_db_cache()
    new_db = _db_singleton()
    st.session_state.active_source = new_db.active_source_id
    _sync_to_mcp_server()
    st.rerun()


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
        # Prefer the rows the LLM actually saw when its tool-call SQL
        # matched the contract's SQL — the orchestrator attaches them
        # to panel["_data"]. This eliminates the redundant re-execute
        # and the explanation-vs-chart divergence that can happen when
        # two runs produce different totals.
        data = panel.get("_data")
        if data is not None:
            dfs.append(
                pd.DataFrame(data.get("rows", []), columns=data.get("columns", []))
            )
            errs.append("")
            continue
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

    panels = _normalize_panels(turn)
    # Surface the explanation-vs-chart divergence when the contract's
    # SQL didn't match anything the LLM ran. The chart was re-executed
    # from a different query than the prose was written against, so the
    # numbers can legitimately disagree.
    diverged = [i + 1 for i, p in enumerate(panels) if p.get("_sql_divergence")]
    if diverged:
        which = ", ".join(str(i) for i in diverged)
        st.warning(
            f"Panel {which}: the chart's SQL doesn't match what the model "
            "ran while writing its answer, so numbers in the prose and the "
            "chart may not agree. Open **Show SQL** to compare."
        )

    if turn.get("explanation"):
        st.markdown(turn["explanation"])

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

    st.title("📊 Raid.AI")
    st.markdown(
        "##### Natural-language → dashboard over the SmartRep voicebot dataset"
    )
    st.caption(
        "Ask anything in English or Greek. Follow-ups, anomaly hunts, and "
        "donut-chart style overrides all welcome."
    )
    # Active-source banner — visible mid-conversation toggle for judges.
    orch = _orchestrator_singleton()
    db = _db_singleton()
    active = next(
        (s for s in db.sources() if s.id == db.active_source_id),
        None,
    )
    source_label = active.display_name if active else st.session_state.active_source
    st.markdown(
        f"**Source:** `{source_label}` &nbsp;·&nbsp; "
        f"**Provider:** `{orch.client.provider}` &nbsp;·&nbsp; "
        f"**Model:** `{orch.client.model}`"
    )
    _scope_bar()

    _replay_history()

    user_text = st.chat_input("Ask a question…")
    # A click on a sidebar sample prompt parks the text in session_state.
    # Pick it up here so the rest of the flow treats it as a regular
    # user message.
    if not user_text and st.session_state.get("pending_prompt"):
        user_text = st.session_state.pop("pending_prompt")
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
