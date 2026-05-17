"""DuckDB access layer.

Opens the built-in dataset read-only, registers helper views, and lets
the UI swap between the built-in DuckDB / JSONL sources and up to five
user-imported uploads. The `switch_source` indirection means the
LLM-generated SQL stays portable across sources — every source produces
a `v_conversations_active` view with the canonical column shape.
"""

from __future__ import annotations

import json
import shutil
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

DATA_DIR = Path(__file__).parent / "data"
DUCKDB_PATH = DATA_DIR / "conversations.duckdb"
JSONL_PATH = DATA_DIR / "conversations.jsonl"
UPLOADS_DIR = DATA_DIR / "uploads"
MANIFEST_PATH = UPLOADS_DIR / "manifest.json"
VIEWS_SQL = Path(__file__).parent / "views.sql"

# Cap on user uploads (built-ins don't count). Hardcoded — the UI mirrors it.
MAX_USER_SOURCES = 5

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

# Stable ids for the two built-in sources. The legacy strings "duckdb" and
# "jsonl" remain accepted as aliases on switch_source for back-compat.
BUILTIN_DUCKDB_ID = "builtin_duckdb"
BUILTIN_JSONL_ID = "builtin_jsonl"


@dataclass
class Source:
    """One selectable data source.

    `kind` is one of:
      * "builtin_duckdb" — the bundled data/conversations.duckdb
      * "builtin_jsonl"  — the bundled data/conversations.jsonl
      * "upload_jsonl"   — user-uploaded JSONL with the canonical shape
      * "upload_duckdb"  — user-uploaded DuckDB file with the canonical views
    """

    id: str
    display_name: str
    kind: str
    path: str
    added_at: str = ""

    @property
    def is_builtin(self) -> bool:
        return self.kind.startswith("builtin_")

    @property
    def legacy_string(self) -> str:
        """Back-compat: callers that expect 'duckdb' / 'jsonl' get those for
        built-ins and the source id for uploads."""
        if self.kind == "builtin_duckdb":
            return "duckdb"
        if self.kind == "builtin_jsonl":
            return "jsonl"
        return self.id


@dataclass
class Database:
    """Wraps a single read-only DuckDB connection plus a source registry.

    The connection is created lazily and re-used for the lifetime of the
    process. `register_source` / `unregister_source` mutate the manifest
    and tear down the connection so the next call rebuilds the views.
    """

    active_source_id: str = BUILTIN_DUCKDB_ID
    _lock: threading.Lock = None  # type: ignore[assignment]
    _con: duckdb.DuckDBPyConnection | None = None
    _sources: dict[str, Source] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ connection

    def connect(self) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            return self._con
        con = duckdb.connect(str(DUCKDB_PATH), read_only=True)
        # Lock down extension auto-loading BEFORE any user-supplied file
        # gets attached — uploaded .duckdb files could otherwise pull in
        # extensions during ATTACH.
        for stmt in (
            "SET autoinstall_known_extensions = false",
            "SET autoload_known_extensions = false",
        ):
            try:
                con.execute(stmt)
            except Exception:  # pragma: no cover — older DuckDB versions
                pass

        # Register the two built-ins. The DuckDB file is the primary
        # connection; the JSONL file gets materialized into a temp view.
        self._sources = {
            BUILTIN_DUCKDB_ID: Source(
                id=BUILTIN_DUCKDB_ID,
                display_name="Built-in DuckDB",
                kind="builtin_duckdb",
                path=str(DUCKDB_PATH),
            ),
            BUILTIN_JSONL_ID: Source(
                id=BUILTIN_JSONL_ID,
                display_name="Built-in JSONL",
                kind="builtin_jsonl",
                path=str(JSONL_PATH),
            ),
        }
        self._install_jsonl_view(con, BUILTIN_JSONL_ID, JSONL_PATH)

        # Pull in any uploads from the manifest and remember the saved
        # active source. Bad entries are silently dropped so a single
        # broken upload doesn't break startup.
        saved_active = self._load_manifest_into(con)
        if saved_active and saved_active in self._sources:
            self.active_source_id = saved_active
        if self.active_source_id not in self._sources:
            self.active_source_id = BUILTIN_DUCKDB_ID

        self._apply_source_alias(con, self.active_source_id)
        self._install_helper_views(con)

        # NB: we used to `SET enable_external_access = false` here as a
        # belt-and-suspenders lockdown, but that's a DuckDB
        # instance-level (and irreversible) setting. The instance
        # persists across our close()-and-rebuild on register_source /
        # unregister_source, which would then block the JSONL ingestion
        # of the new upload. The primary defense remains
        # sql_safety.validate_sql (see test_run_sql_rejects_filesystem_reads),
        # which rejects read_csv_auto / read_text / read_json_auto / glob
        # / etc. at the AST level before any SQL reaches DuckDB.
        self._con = con
        return con

    def close(self) -> None:
        if self._con is not None:
            try:
                self._con.close()
            except Exception:
                pass
            self._con = None

    # ------------------------------------------------------------------ view installation

    @staticmethod
    def _install_helper_views(con: duckdb.DuckDBPyConnection) -> None:
        con.execute(VIEWS_SQL.read_text())

    @staticmethod
    def _install_jsonl_view(con: duckdb.DuckDBPyConnection, slug: str, path: Path) -> None:
        """Materialize one JSONL file into a TEMP TABLE and build a
        `v_conversations_<slug>` view with the canonical column shape.

        The CTAS reads the file eagerly so subsequent queries are
        in-memory only — that's what makes `enable_external_access=false`
        safe to flip on later.
        """
        table = f"conversations_raw_{slug}"
        view = f"v_conversations_{slug}"
        con.execute(
            f"""
            CREATE OR REPLACE TEMP TABLE {table} AS
            SELECT * FROM read_json_auto('{path}');
            """
        )
        con.execute(
            f"""
            CREATE OR REPLACE TEMP VIEW {view} AS
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
            FROM {table};
            """
        )

    @staticmethod
    def _install_duckdb_attach(con: duckdb.DuckDBPyConnection, slug: str, path: Path) -> None:
        """ATTACH an uploaded .duckdb file read-only and expose its
        `v_conversations` as `v_conversations_<slug>`.

        Assumes the upload follows the same shape as the bundled DuckDB
        file (a `v_conversations` view with the canonical columns).
        """
        alias = f"db_{slug}"
        con.execute(f"DETACH DATABASE IF EXISTS {alias}")
        con.execute(f"ATTACH '{path}' AS {alias} (READ_ONLY)")
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW v_conversations_{slug} AS "
            f"SELECT * FROM {alias}.v_conversations"
        )

    def _apply_source_alias(self, con: duckdb.DuckDBPyConnection, source_id: str) -> None:
        """Point `v_conversations_active` at the chosen source's view."""
        src = self._sources[source_id]
        if src.kind == "builtin_duckdb":
            target = "v_conversations"
        else:
            target = f"v_conversations_{source_id}"
        con.execute("DROP VIEW IF EXISTS v_conversations_active")
        con.execute(
            f"CREATE OR REPLACE TEMP VIEW v_conversations_active AS "
            f"SELECT * FROM {target}"
        )

    # ------------------------------------------------------------------ manifest

    def _load_manifest_into(self, con: duckdb.DuckDBPyConnection) -> str | None:
        """Register every upload listed in the manifest. Returns the
        saved `active_source_id`, or None if no manifest exists."""
        if not MANIFEST_PATH.exists():
            return None
        try:
            data = json.loads(MANIFEST_PATH.read_text())
        except Exception:
            return None
        for entry in data.get("sources", []):
            try:
                src = Source(
                    id=entry["id"],
                    display_name=entry["display_name"],
                    kind=entry["kind"],
                    path=entry["path"],
                    added_at=entry.get("added_at", ""),
                )
            except KeyError:
                continue
            path = Path(src.path)
            if not path.exists():
                continue
            try:
                if src.kind == "upload_jsonl":
                    self._install_jsonl_view(con, src.id, path)
                elif src.kind == "upload_duckdb":
                    self._install_duckdb_attach(con, src.id, path)
                else:
                    continue
            except Exception:
                # Skip broken uploads so a single bad file doesn't
                # poison startup.
                continue
            self._sources[src.id] = src
        return data.get("active_source_id")

    def _write_manifest(self) -> None:
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        uploads = [
            {
                "id": s.id,
                "display_name": s.display_name,
                "kind": s.kind,
                "path": s.path,
                "added_at": s.added_at,
            }
            for s in self._sources.values()
            if not s.is_builtin
        ]
        MANIFEST_PATH.write_text(
            json.dumps(
                {"active_source_id": self.active_source_id, "sources": uploads},
                indent=2,
            )
        )

    # ------------------------------------------------------------------ public ops

    @property
    def source(self) -> str:
        """Back-compat: legacy 'duckdb' / 'jsonl' string for built-ins,
        the source id for uploads."""
        # connect() populates _sources lazily — make sure it's done.
        self.connect()
        src = self._sources.get(self.active_source_id)
        if src is None:
            return "unknown"
        return src.legacy_string

    def sources(self) -> list[Source]:
        """All registered sources, built-ins first then uploads by added_at."""
        self.connect()
        builtins = [
            s for s in (self._sources.get(BUILTIN_DUCKDB_ID),
                        self._sources.get(BUILTIN_JSONL_ID)) if s
        ]
        uploads = sorted(
            (s for s in self._sources.values() if not s.is_builtin),
            key=lambda s: s.added_at,
        )
        return builtins + uploads

    def user_source_count(self) -> int:
        self.connect()
        return sum(1 for s in self._sources.values() if not s.is_builtin)

    def switch_source(self, source: str) -> str:
        """Switch the active source by id or by legacy string.

        Returns the new active source's legacy string ('duckdb' /
        'jsonl') if it's a built-in, else its id — matching the existing
        contract callers depend on.
        """
        with self._lock:
            con = self.connect()
            source_id = source
            if source == "duckdb":
                source_id = BUILTIN_DUCKDB_ID
            elif source == "jsonl":
                source_id = BUILTIN_JSONL_ID
            if source_id not in self._sources:
                raise ValueError(
                    f"unknown source: {source!r}; "
                    f"known ids: {sorted(self._sources)}"
                )
            self._apply_source_alias(con, source_id)
            self.active_source_id = source_id
            self._write_manifest()
        return self._sources[source_id].legacy_string

    def register_source(self, display_name: str, src_path: Path, kind: str) -> Source:
        """Copy a user-uploaded file into data/uploads/ and register it.

        After this returns the connection is closed; the next `connect()`
        will rebuild with the new source materialized. Caller is expected
        to drive a re-render so subsequent queries pick it up.
        """
        if kind not in {"upload_jsonl", "upload_duckdb"}:
            raise ValueError(f"unsupported source kind: {kind!r}")
        src_path = Path(src_path)
        if not src_path.exists():
            raise FileNotFoundError(f"upload source not found: {src_path}")

        if self.user_source_count() >= MAX_USER_SOURCES:
            raise ValueError(
                f"already at the user-source cap of {MAX_USER_SOURCES}; "
                "remove one before adding another"
            )

        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        new_id = "src_" + uuid.uuid4().hex[:10]
        ext = ".jsonl" if kind == "upload_jsonl" else ".duckdb"
        dest = UPLOADS_DIR / f"{new_id}{ext}"
        shutil.copy(str(src_path), str(dest))

        src = Source(
            id=new_id,
            display_name=(display_name or src_path.stem).strip() or new_id,
            kind=kind,
            path=str(dest),
            added_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        )
        with self._lock:
            self._sources[src.id] = src
            self._write_manifest()
            # Drop the existing connection so the next call rebuilds with
            # the new source materialized. Cheaper than re-running ATTACH
            # / read_json_auto on a live connection that has external
            # access disabled.
            self.close()
        return src

    def unregister_source(self, source_id: str) -> None:
        with self._lock:
            src = self._sources.get(source_id)
            if src is None:
                raise ValueError(f"unknown source: {source_id!r}")
            if src.is_builtin:
                raise ValueError("cannot remove a built-in source")
            if self.active_source_id == source_id:
                self.active_source_id = BUILTIN_DUCKDB_ID
            try:
                Path(src.path).unlink(missing_ok=True)
            except Exception:
                pass
            del self._sources[source_id]
            self._write_manifest()
            # Tear down so the next connect rebuilds without the removed
            # source (and re-applies the now-current active alias).
            self.close()

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


def reset_db() -> None:
    """Tear down the singleton — used by tests and after upload changes
    when callers want a hard rebuild rather than relying on the lazy
    close-on-write behaviour inside `register_source` / `unregister_source`."""
    global _DB
    if _DB is not None:
        _DB.close()
        _DB = None
