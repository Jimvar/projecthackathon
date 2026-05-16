"""SQL safety layer.

The LLM's `run_sql` tool is the only way it reaches the data. Everything
the LLM emits flows through `validate_sql` first:

* Accept only `SELECT` / `WITH` statements.
* Reject DDL/DML/PRAGMA/COPY/ATTACH/EXPORT/etc.
* Reject multi-statement queries.
* The actual row/byte caps are applied at execution time elsewhere.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp


# Anything that is NOT a SELECT- or WITH-expression should be rejected.
# sqlglot represents these as subclasses of `exp.Expression`. We check the
# root node's type. CTEs (WITH ...) parse as `exp.With` with an inner Select.
ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)

# Even inside a Select, certain constructs would be unsafe (e.g. INSERT in a CTE).
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Pragma,
    exp.Copy,
    exp.Attach,
    exp.Command,  # SET, EXPLAIN ANALYZE etc. lower to this.
    exp.Use,
)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    normalized_sql: str = ""


def validate_sql(query: str) -> ValidationResult:
    """Parse `query` and reject anything that isn't a pure read.

    Returns a ValidationResult where `.normalized_sql` is the
    canonical form to execute.
    """
    text = (query or "").strip().rstrip(";")
    if not text:
        return ValidationResult(False, "empty query")

    if ";" in text:
        return ValidationResult(False, "multi-statement queries are not allowed")

    try:
        parsed = sqlglot.parse(text, dialect="duckdb")
    except Exception as e:  # pragma: no cover - sqlglot raises a few subclasses
        return ValidationResult(False, f"parse error: {e}")

    if not parsed or parsed[0] is None:
        return ValidationResult(False, "could not parse query")
    if len(parsed) != 1:
        return ValidationResult(False, "multi-statement queries are not allowed")

    root = parsed[0]

    # Unwrap a WITH so we can check the inner Select.
    inner = root
    if isinstance(inner, exp.With):
        inner = inner.this  # the body Select/Union after CTEs

    if not isinstance(inner, ALLOWED_ROOTS):
        return ValidationResult(
            False,
            f"only SELECT / WITH queries are allowed (got {type(inner).__name__})",
        )

    # Walk the tree and look for forbidden subnodes anywhere.
    for node in root.walk():
        # sqlglot returns tuple (node, parent, key) in older versions; just-node in newer.
        n = node[0] if isinstance(node, tuple) else node
        if isinstance(n, FORBIDDEN_NODES):
            return ValidationResult(
                False,
                f"forbidden SQL construct: {type(n).__name__}",
            )

    return ValidationResult(True, "", root.sql(dialect="duckdb"))
