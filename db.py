"""DuckDB access layer.

Opens the dataset read-only, registers helper views, and supports
toggling between the native DuckDB views and JSONL-backed views that
expose the same column names. The `switch_source` indirection means
the LLM-generated SQL stays portable across sources.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).parent / "data"
DUCKDB_PATH = DATA_DIR / "conversations.duckdb"
JSONL_PATH = DATA_DIR / "conversations.jsonl"
VIEWS_SQL = Path(__file__).parent / "views.sql"

NATIVE_VIEW_NAMES = (
    "v_conversations",
    "v_turns",
    "v_evaluations",
    "v_data_collection",
    "v_tool_calls",
)

HELPER_VIEW_NAMES = (
    "v_conv_with_intent",
    "v_eval_pivot",
    "v_conv_with_dc",
)


@dataclass
class Database:
    """Wraps a single read-only DuckDB connection.

    The connection is created lazily and re-used for the lifetime of the
    process. `source` toggles whether SELECTs against the canonical view
    names hit the native flat views or JSONL-derived views with the same
    column shape.
    """

    source: str = "duckdb"  # "duckdb" or "jsonl"
    _lock: threading.Lock = None  # type: ignore[assignment]
    _con: duckdb.DuckDBPyConnection | None = None

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ connection

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        self._install_helper_views(con)
        self._install_jsonl_views(con)
        self._apply_source_aliases(con, self.source)
        # Belt-and-suspenders: lock down the connection's filesystem and
        # network access AFTER the JSONL views are registered. Anything
        # the LLM sends via run_sql is parsed by sql_safety.py first
        # (which rejects read_csv_auto / read_text / etc.); this DuckDB
        # flag is the second layer in case anything slips through.
        try:
            con.execute("SET enable_external_access = false")
        except Exception:  # pragma: no cover — older DuckDB versions
            pass
        self._con = con
        return con

    def close(self) -> None:
        if self._con is not None:
            self._con.close()
            self._con = None

    # ------------------------------------------------------------------ setup

    @staticmethod
    def _install_helper_views(con: duckdb.DuckDBPyConnection) -> None:
        con.execute(VIEWS_SQL.read_text())

    @staticmethod
    def _install_jsonl_views(con: duckdb.DuckDBPyConnection) -> None:
        """Register JSONL-backed views with the same column shape as the
        native views. We name them `<view>_jsonl` so we can alias them
        later via `switch_source`.

        The JSONL is materialized into a TEMP TABLE on connect so the
        underlying file is read exactly once. That makes it safe to set
        `enable_external_access = false` after this runs — subsequent
        queries against the JSONL views hit the in-memory table, not
        the filesystem.
        """
        # Materialize once. CTAS with read_json_auto reads the file
        # eagerly; subsequent queries against conversations_raw_jsonl
        # are pure in-memory and need no filesystem access.
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE conversations_raw_jsonl AS
            SELECT * FROM read_json_auto('{JSONL_PATH}');
            """
        )

        # v_conversations_jsonl: flat per-call view, columns matching v_conversations.
        con.execute(
            """
            CREATE OR REPLACE TEMP VIEW v_conversations_jsonl AS
            SELECT
              conversation_id,
              agent_id,
              agent_name,
              user_id,
              status,
              start_time::TIMESTAMP                              AS start_time,
              CAST(start_time AS DATE)                           AS start_date,
              EXTRACT(HOUR FROM start_time)::INTEGER             AS start_hour,
              EXTRACT(DOW  FROM start_time)::INTEGER             AS start_dow,
              call_duration_secs,
              metadata.cost.amount                               AS cost_amount,
              metadata.cost.currency                             AS cost_currency,
              metadata.phone_call.direction                      AS call_direction,
              metadata.phone_call.from_number                    AS from_number,
              metadata.termination_reason                        AS termination_reason,
              metadata.bot_version                               AS bot_version,
              analysis.transcript_summary                        AS transcript_summary,
              analysis.call_successful                           AS call_successful,
              analysis.main_language                             AS main_language,
              conversation_initiation_client_data.dynamic_variables.user_id     AS dv_user_id,
              conversation_initiation_client_data.dynamic_variables.segment     AS segment,
              conversation_initiation_client_data.dynamic_variables.region      AS region,
              conversation_initiation_client_data.dynamic_variables.preferred_language AS preferred_language,
              conversation_initiation_client_data.dynamic_variables.channel_origin     AS channel_origin,
              conversation_initiation_client_data.dynamic_variables.csat_score  AS csat_score,
              conversation_initiation_client_data.dynamic_variables.outcome     AS outcome
            FROM conversations_raw_jsonl;
            """
        )

    @staticmethod
    def _apply_source_aliases(con: duckdb.DuckDBPyConnection, source: str) -> None:
        """For now only `v_conversations` has a JSONL-backed twin.
        When source=jsonl, alias the canonical name to the JSONL view.
        The native DuckDB file already exposes the canonical names, so
        for source=duckdb we don't need to do anything.
        """
        if source == "jsonl":
            con.execute("DROP VIEW IF EXISTS v_conversations_active")
            con.execute(
                "CREATE OR REPLACE TEMP VIEW v_conversations_active AS "
                "SELECT * FROM v_conversations_jsonl"
            )
        else:
            con.execute("DROP VIEW IF EXISTS v_conversations_active")
            con.execute(
                "CREATE OR REPLACE TEMP VIEW v_conversations_active AS "
                "SELECT * FROM v_conversations"
            )

    # ------------------------------------------------------------------ public ops

    def switch_source(self, source: str) -> str:
        if source not in {"duckdb", "jsonl"}:
            raise ValueError(f"unknown source: {source}; must be duckdb or jsonl")
        with self._lock:
            con = self.connect()
            self._apply_source_aliases(con, source)
            self.source = source
        return source

    def execute(self, query: str, params: tuple | None = None):
        """Execute a SQL statement and return the DuckDB result handle.
        Caller is responsible for fetching (so we can stream / limit).
        """
        with self._lock:
            con = self.connect()
            return con.execute(query, params) if params else con.execute(query)


_DB: Database | None = None


def get_db() -> Database:
    """Process-wide singleton. Streamlit's @cache_resource hooks call this."""
    global _DB
    if _DB is None:
        _DB = Database()
        _DB.connect()
    return _DB
