"""The six MCP tools, implemented as plain Python functions.

`mcp_server.py` wraps these via FastMCP so the LLM can call them over
stdio. The orchestrator imports them directly for the in-process path
(faster and simpler during Phase 1).

Every public function returns JSON-serializable data so it's portable
across both call paths.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from db import HELPER_VIEW_NAMES, NATIVE_VIEW_NAMES, get_db
from sql_safety import validate_sql

def _env_int(name: str, default: int) -> int:
    """Read a positive int from the environment; fall back to `default`."""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


# Tunables (env-overridable so demo-day judges can ask for "top 50,000"
# or run very long sessions without code changes).
ROW_CAP = _env_int("NR2_ROW_CAP", 10_000)          # max rows returned to LLM
BYTE_CAP = _env_int("NR2_BYTE_CAP", 256_000)       # max JSON payload bytes

METRICS_PATH = Path(__file__).parent / "data" / "metrics_dictionary.md"
SCHEMA_PATH = Path(__file__).parent / "data" / "schema.md"


# ---------------------------------------------------------------------- helpers


def _load_metrics_doc() -> str:
    return METRICS_PATH.read_text()


def _load_schema_doc() -> str:
    return SCHEMA_PATH.read_text()


def _column_notes_for(table: str) -> dict[str, str]:
    """Pull the schema.md row for each column of `table`.

    schema.md has tables shaped:
        | `column_name` | TYPE | notes |
    We grab anything that looks like ``\\`name\\``` at the start of a row.
    """
    notes: dict[str, str] = {}
    text = _load_schema_doc()
    for line in text.splitlines():
        m = re.match(r"\|\s*`([a-zA-Z0-9_\.]+)`\s*\|\s*[^|]*\|\s*([^|]+)\|", line)
        if m:
            col = m.group(1).split(".")[-1]
            note = m.group(2).strip()
            if note:
                notes.setdefault(col, note)
    return notes


# ---------------------------------------------------------------------- tools


def list_tables() -> dict[str, Any]:
    """Return the tables and views the LLM may query."""
    db = get_db()
    rows = db.execute("SHOW TABLES").fetchall()
    names = [r[0] for r in rows]
    helper_present = [v for v in HELPER_VIEW_NAMES if v in names or _exists(v)]
    return {
        "tables": sorted(set(names) | set(helper_present) | {"v_conversations_active"}),
        "preferred": list(NATIVE_VIEW_NAMES) + list(HELPER_VIEW_NAMES) + ["v_conversations_active"],
        "active_source": db.source,
    }


def _exists(name: str) -> bool:
    """True if `name` resolves as a relation on the current connection."""
    db = get_db()
    try:
        db.execute(f"SELECT * FROM {name} LIMIT 0")
        return True
    except Exception:
        return False


def describe_schema(table: str) -> dict[str, Any]:
    """Columns + types + schema-doc notes for one table."""
    db = get_db()
    if not _safe_identifier(table):
        return {"error": f"invalid table name: {table!r}"}
    try:
        rows = db.execute(f"DESCRIBE {table}").fetchall()
    except Exception as e:
        return {"error": f"could not describe {table}: {e}"}
    notes = _column_notes_for(table)
    columns = [
        {
            "name": r[0],
            "type": r[1],
            "nullable": r[2] == "YES",
            "note": notes.get(r[0], ""),
        }
        for r in rows
    ]
    return {"table": table, "columns": columns}


def run_sql(query: str) -> dict[str, Any]:
    """Validate and execute a read-only SQL query.

    Returns:
        {
            "columns": [...],
            "rows": [[...], ...],     # row_cap-capped
            "row_count": N,
            "truncated": bool,
            "sql": "<normalized>"
        }
        or {"error": "..."}.
    """
    v = validate_sql(query)
    if not v.ok:
        return {"error": v.reason, "sql": query}

    db = get_db()
    try:
        cur = db.execute(v.normalized_sql)
    except Exception as e:
        return {"error": f"execution error: {e}", "sql": v.normalized_sql}

    columns = [d[0] for d in cur.description] if cur.description else []
    rows = cur.fetchmany(ROW_CAP + 1)
    truncated = len(rows) > ROW_CAP
    rows = rows[:ROW_CAP]

    # JSON-safe coerce (timestamps, decimals, etc.)
    safe_rows = [[_jsonable(v) for v in r] for r in rows]

    payload = {
        "columns": columns,
        "rows": safe_rows,
        "row_count": len(safe_rows),
        "truncated": truncated,
        "sql": v.normalized_sql,
    }

    encoded = json.dumps(payload, default=str)
    if len(encoded) > BYTE_CAP:
        # Trim rows until we fit. Almost never triggered with row_cap=10k
        # on typical aggregated queries, but worth having.
        while len(safe_rows) > 1 and len(json.dumps({"rows": safe_rows}, default=str)) > BYTE_CAP:
            safe_rows = safe_rows[: len(safe_rows) // 2]
        payload["rows"] = safe_rows
        payload["row_count"] = len(safe_rows)
        payload["truncated"] = True

    return payload


def _jsonable(v: Any) -> Any:
    import datetime as dt
    from decimal import Decimal

    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (dt.date, dt.datetime, dt.time)):
        return v.isoformat()
    return str(v)


def get_metric_definition(name: str) -> dict[str, Any]:
    """Return the chunk of metrics_dictionary.md that defines `name`.

    Matching is loose: we look for a heading or bolded label that contains
    the requested name (case-insensitive).
    """
    text = _load_metrics_doc()
    norm = name.strip().lower().replace("-", "_").replace(" ", "_")
    sections = re.split(r"\n(?=##\s|\*\*[A-Z])", text)
    hits = []
    for section in sections:
        head = section.strip().splitlines()[0].lower() if section.strip() else ""
        head_norm = re.sub(r"[^a-z0-9_]+", "_", head)
        if norm in head_norm or norm.replace("_rate", "") in head_norm:
            hits.append(section.strip())
    if not hits:
        return {
            "name": name,
            "found": False,
            "note": "no exact match; here is the full dictionary",
            "text": text,
        }
    return {"name": name, "found": True, "text": "\n\n".join(hits)}


def sample_rows(table: str, n: int = 5) -> dict[str, Any]:
    """Return up to `n` rows of `table` for quick inspection."""
    if not _safe_identifier(table):
        return {"error": f"invalid table name: {table!r}"}
    n = max(1, min(int(n), 50))
    return run_sql(f"SELECT * FROM {table} LIMIT {n}")


def value_counts(table: str, column: str, top_n: int = 20) -> dict[str, Any]:
    """Distinct values of `column` and how often each occurs.

    Faster than asking the LLM to write a GROUP BY for column discovery.
    """
    if not _safe_identifier(table) or not _safe_identifier(column):
        return {"error": f"invalid identifier(s): table={table!r} column={column!r}"}
    n = max(1, min(int(top_n), 200))
    db = get_db()
    try:
        cur = db.execute(
            f"SELECT {column} AS value, COUNT(*) AS n "
            f"FROM {table} "
            f"GROUP BY 1 "
            f"ORDER BY n DESC, 1 ASC "
            f"LIMIT {n}"
        )
        rows = cur.fetchall()
        total = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        distinct = db.execute(
            f"SELECT COUNT(DISTINCT {column}) FROM {table}"
        ).fetchone()[0]
    except Exception as e:
        return {"error": f"value_counts failed: {e}"}
    return {
        "table": table,
        "column": column,
        "values": [{"value": _jsonable(r[0]), "count": int(r[1])} for r in rows],
        "total_rows": int(total),
        "distinct_values": int(distinct),
        "truncated": distinct > n,
    }


def time_range(table: str, column: str = "start_time") -> dict[str, Any]:
    """Min / max / count for a timestamp or date column.

    The LLM uses this to translate "this month" / "last quarter" into
    concrete WHERE clauses without having to probe the data first.
    """
    if not _safe_identifier(table) or not _safe_identifier(column):
        return {"error": f"invalid identifier(s): table={table!r} column={column!r}"}
    db = get_db()
    try:
        row = db.execute(
            f"SELECT MIN({column}), MAX({column}), "
            f"COUNT(*), COUNT({column}) "
            f"FROM {table}"
        ).fetchone()
    except Exception as e:
        return {"error": f"time_range failed: {e}"}
    return {
        "table": table,
        "column": column,
        "min": _jsonable(row[0]),
        "max": _jsonable(row[1]),
        "row_count": int(row[2]) if row[2] is not None else 0,
        "non_null_count": int(row[3]) if row[3] is not None else 0,
    }


def switch_source(source: str) -> dict[str, Any]:
    """Switch the active data source by id (or legacy 'duckdb' / 'jsonl')."""
    source = (source or "").strip()
    db = get_db()
    try:
        active = db.switch_source(source)
    except Exception as e:
        return {"error": str(e), "active_source": db.source}
    return {"active_source": active}


def list_sources() -> dict[str, Any]:
    """Enumerate the registered data sources (built-ins + user uploads)."""
    db = get_db()
    return {
        "active_source": db.source,
        "sources": [
            {
                "id": s.id,
                "name": s.display_name,
                "kind": s.kind,
                "builtin": s.is_builtin,
            }
            for s in db.sources()
        ],
    }


def reload_sources() -> dict[str, Any]:
    """Drop the live DuckDB connection so the next tool call rebuilds
    from data/uploads/manifest.json.

    The Streamlit app calls this over MCP after `register_source` or
    `unregister_source` so the server's DB picks up the new manifest
    state. The manifest itself is the shared truth between processes;
    this just nudges the server to re-read it.
    """
    db = get_db()
    db.close()
    return {"reloaded": True}


# ---------------------------------------------------------------------- internals


_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_identifier(name: str) -> bool:
    return bool(_IDENTIFIER_RE.match(name or ""))


# A registry the orchestrator + MCP server both consume.
TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "list_tables": {
        "fn": list_tables,
        "description": "List the tables and views available to query.",
        "parameters": {"type": "object", "properties": {}},
    },
    "describe_schema": {
        "fn": describe_schema,
        "description": "Describe one table's columns (name, type, schema-doc note).",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {
                    "type": "string",
                    "description": "Table or view name, e.g. 'v_conversations'.",
                },
            },
            "required": ["table"],
        },
    },
    "run_sql": {
        "fn": run_sql,
        "description": (
            "Execute a read-only DuckDB SQL query (SELECT / WITH only). "
            "Returns at most 10,000 rows. Use this to fetch the data your "
            "answer depends on."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A single SELECT or WITH … SELECT statement.",
                },
            },
            "required": ["query"],
        },
    },
    "get_metric_definition": {
        "fn": get_metric_definition,
        "description": (
            "Look up the exact formula for a metric (containment, CSAT, AHT, "
            "escalation, deflection, abandonment, etc.) from the project's "
            "metrics dictionary. Call this whenever the user mentions a metric "
            "by name before you write SQL."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Metric name, e.g. 'containment rate', 'CSAT'.",
                },
            },
            "required": ["name"],
        },
    },
    "sample_rows": {
        "fn": sample_rows,
        "description": "Return up to N rows of a table for quick inspection.",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "n": {"type": "integer", "minimum": 1, "maximum": 50, "default": 5},
            },
            "required": ["table"],
        },
    },
    "value_counts": {
        "fn": value_counts,
        "description": (
            "Return the distinct values of a column and how often each occurs, "
            "sorted by frequency descending. Use this to discover the universe "
            "of categories before writing a GROUP BY."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "column": {"type": "string"},
                "top_n": {"type": "integer", "minimum": 1, "maximum": 200, "default": 20},
            },
            "required": ["table", "column"],
        },
    },
    "time_range": {
        "fn": time_range,
        "description": (
            "Return min/max/count for a timestamp or date column. Use this to "
            "translate phrases like 'this week' or 'last quarter' into concrete "
            "WHERE clauses anchored to the dataset's actual time window."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string"},
                "column": {"type": "string", "default": "start_time"},
            },
            "required": ["table"],
        },
    },
    "switch_source": {
        "fn": switch_source,
        "description": (
            "Switch the data source backing v_conversations_active. Accepts "
            "either the legacy strings 'duckdb' / 'jsonl' or a registered "
            "source id (see list_sources). Call this only when the user "
            "asks to switch sources."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {
                    "type": "string",
                    "description": (
                        "'duckdb', 'jsonl', or the id of an imported source "
                        "(e.g. 'src_abc1234567')."
                    ),
                },
            },
            "required": ["source"],
        },
    },
    "list_sources": {
        "fn": list_sources,
        "description": (
            "List every registered data source (the two built-ins plus any "
            "user-imported uploads) and which one is currently active. Use "
            "this only when the user asks 'which datasets are loaded?' or "
            "to confirm an id before calling switch_source."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    "reload_sources": {
        "fn": reload_sources,
        "description": (
            "Drop the server's live DuckDB connection so the next tool call "
            "rebuilds from data/uploads/manifest.json. The app uses this to "
            "tell the MCP server about new or removed uploads; the LLM "
            "should not call it on its own."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}


def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
    """In-process tool dispatcher used by the orchestrator."""
    if name not in TOOL_REGISTRY:
        return {"error": f"unknown tool: {name}"}
    fn = TOOL_REGISTRY[name]["fn"]
    arguments = arguments or {}
    try:
        return fn(**arguments)
    except TypeError as e:
        return {"error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"error": f"tool {name} failed: {e}"}
