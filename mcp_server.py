"""MCP server exposing the six dataset tools over stdio.

Run with:
    uv run python mcp_server.py            # stdio transport
    uv run python mcp_server.py --http     # streamable HTTP (for local debugging)

The orchestrator can either:
  * import `mcp_tools.call_tool` directly (in-process, the Phase-1 default), or
  * spawn this server and talk to it via the MCP stdio protocol.

The actual tool implementations live in `mcp_tools.py`; this file is the
process-isolated surface mandated by the PDF brief.
"""

from __future__ import annotations

import sys

from mcp.server.fastmcp import FastMCP

from mcp_tools import (
    describe_schema,
    get_metric_definition,
    list_tables,
    run_sql,
    sample_rows,
    switch_source,
)

mcp = FastMCP(
    "nr2dashboard",
    instructions=(
        "Read-only access to a synthetic banking voicebot dataset. "
        "Use `list_tables` and `describe_schema` to explore, "
        "`get_metric_definition` to look up exact KPI formulas, and "
        "`run_sql` to execute SELECT-only DuckDB queries."
    ),
)


@mcp.tool(description="List the tables and views available to query.")
def t_list_tables() -> dict:
    return list_tables()


@mcp.tool(description="Describe one table's columns (name, type, schema-doc note).")
def t_describe_schema(table: str) -> dict:
    return describe_schema(table)


@mcp.tool(
    description=(
        "Execute a read-only DuckDB SQL query (SELECT / WITH only). "
        "Returns at most 10,000 rows."
    )
)
def t_run_sql(query: str) -> dict:
    return run_sql(query)


@mcp.tool(
    description=(
        "Look up the exact formula for a metric (containment, CSAT, AHT, "
        "escalation, deflection, abandonment, etc.) from the project's "
        "metrics dictionary."
    )
)
def t_get_metric_definition(name: str) -> dict:
    return get_metric_definition(name)


@mcp.tool(description="Return up to N rows of a table for quick inspection.")
def t_sample_rows(table: str, n: int = 5) -> dict:
    return sample_rows(table, n)


@mcp.tool(
    description=(
        "Switch the data source backing the canonical views. "
        "Pass 'duckdb' for the native flat views or 'jsonl' for views "
        "derived from conversations.jsonl with the same column shape."
    )
)
def t_switch_source(source: str) -> dict:
    return switch_source(source)


def main() -> None:
    if "--http" in sys.argv:
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
