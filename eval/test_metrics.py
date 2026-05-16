"""Metric-correctness pytest.

This is the test that catches the dominant failure mode: the LLM
inventing its own definition of containment / CSAT / etc. We compute
every formula directly against the dataset and assert against the
golden bounds in `golden.yaml`. We also exercise the helper views
and the run_sql tool's safety layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

# Allow running pytest from project root: `uv run pytest`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db import get_db  # noqa: E402
from mcp_tools import call_tool, get_metric_definition, run_sql  # noqa: E402


GOLDEN_PATH = ROOT / "eval" / "golden.yaml"


@pytest.fixture(scope="session")
def golden() -> list[dict]:
    return yaml.safe_load(GOLDEN_PATH.read_text())


@pytest.fixture(scope="session")
def db():
    return get_db()


# ---------------------------------------------------------------------- formula correctness


def test_golden_formulas(golden, db):
    """Every formula in golden.yaml must execute and stay within bounds."""
    for case in golden:
        cid = case["id"]
        sql = case["sql"]
        cur = db.execute(sql)
        rows = cur.fetchall()
        cols = [c[0] for c in cur.description]

        if "expect_rows" in case:
            assert [list(r) for r in rows] == case["expect_rows"], (
                f"{cid}: row mismatch (got {rows})"
            )
            continue

        if "expect_scalar_min" not in case and "expect_scalar_max" not in case:
            # Smoke entry: just confirm the query executes and returns something.
            assert rows, f"{cid}: query returned no rows"
            continue

        # Otherwise it's a scalar bound check.
        assert len(rows) == 1 and len(cols) == 1, f"{cid}: expected scalar, got {cols} / {rows}"
        val = rows[0][0]
        assert val is not None, f"{cid}: scalar was NULL"
        val_f = float(val)
        if "expect_scalar_min" in case:
            assert val_f >= case["expect_scalar_min"], (
                f"{cid}: {val_f} < min {case['expect_scalar_min']}"
            )
        if "expect_scalar_max" in case:
            assert val_f <= case["expect_scalar_max"], (
                f"{cid}: {val_f} > max {case['expect_scalar_max']}"
            )


# ---------------------------------------------------------------------- containment ≡ outcome='resolved'

def test_containment_equals_outcome_resolved(db):
    """The metrics dictionary says these two formulas should agree."""
    sql = """
        SELECT
          AVG(CASE WHEN call_successful = 'success' THEN 1.0 ELSE 0.0 END),
          AVG(CASE WHEN outcome = 'resolved' THEN 1.0 ELSE 0.0 END)
        FROM v_conversations
    """
    a, b = db.execute(sql).fetchone()
    assert abs(float(a) - float(b)) < 1e-9, (
        f"call_successful and outcome disagree: {a} vs {b}"
    )


# ---------------------------------------------------------------------- helper views

def test_helper_views_present(db):
    for v in ("v_conv_with_intent", "v_eval_pivot", "v_conv_with_dc", "v_conversations_active"):
        n = db.execute(f"SELECT COUNT(*) FROM {v}").fetchone()[0]
        assert n > 0, f"{v} returned zero rows"


def test_eval_pivot_columns(db):
    cols = [c[0] for c in db.execute("DESCRIBE v_eval_pivot").fetchall()]
    expected = {
        "authentication_completed", "intent_resolved", "escalation_triggered",
        "compliance_disclaimer_given", "pii_handled_safely",
        "fallback_count_acceptable", "language_consistency",
        "tool_call_success_rate",
    }
    assert expected.issubset(cols), f"missing eval pivot cols: {expected - set(cols)}"


def test_conv_with_dc_columns(db):
    cols = [c[0] for c in db.execute("DESCRIBE v_conv_with_dc").fetchall()]
    expected = {
        "auth_method_used", "transfer_amount_bucket", "promised_callback",
        "complaint_detected", "self_service_completed", "topic_tags",
    }
    assert expected.issubset(cols), f"missing dc pivot cols: {expected - set(cols)}"


# ---------------------------------------------------------------------- new exploration tools

def test_value_counts_returns_sorted_distinct():
    res = call_tool("value_counts", {"table": "v_conversations", "column": "main_language"})
    assert "error" not in res, res
    assert res["distinct_values"] == 2
    counts = [v["count"] for v in res["values"]]
    assert counts == sorted(counts, reverse=True), "value_counts must be sorted desc"
    assert {v["value"] for v in res["values"]} == {"el", "en"}


def test_value_counts_rejects_bad_identifier():
    res = call_tool("value_counts", {"table": "v c", "column": "x"})
    assert "error" in res


def test_value_counts_truncates_to_top_n():
    res = call_tool(
        "value_counts",
        {"table": "v_conversations", "column": "region", "top_n": 2},
    )
    assert "error" not in res
    assert len(res["values"]) == 2
    assert res["truncated"] is True


def test_time_range_matches_dataset_window():
    res = call_tool("time_range", {"table": "v_conversations", "column": "start_time"})
    assert "error" not in res
    assert res["min"] < res["max"]
    assert res["row_count"] == 10_000
    assert res["non_null_count"] == 10_000


def test_time_range_default_column():
    res = call_tool("time_range", {"table": "v_conversations"})
    assert "error" not in res
    assert res["column"] == "start_time"


# ---------------------------------------------------------------------- safety layer

def test_run_sql_rejects_writes():
    for bad in [
        "DROP TABLE v_conversations",
        "INSERT INTO v_conversations VALUES (1)",
        "UPDATE v_conversations SET csat_score = 5",
        "SELECT 1; DROP TABLE x",
        "PRAGMA table_info(v_conversations)",
    ]:
        res = run_sql(bad)
        assert "error" in res, f"should have rejected: {bad}"


def test_run_sql_caps_rows():
    res = run_sql("SELECT * FROM v_turns")
    assert "rows" in res
    assert len(res["rows"]) <= 10_000


# ---------------------------------------------------------------------- metric lookup

@pytest.mark.parametrize(
    "term",
    ["containment", "CSAT", "AHT", "escalation rate", "abandonment"],
)
def test_get_metric_definition_finds_term(term):
    res = get_metric_definition(term)
    assert res.get("found"), f"no hit for {term!r}"
    assert len(res["text"]) > 30


# ---------------------------------------------------------------------- source switching

def test_switch_source_round_trip():
    a = call_tool("switch_source", {"source": "jsonl"})
    assert a["active_source"] == "jsonl"
    rows_jsonl = run_sql(
        "SELECT main_language, COUNT(*) FROM v_conversations_active GROUP BY 1 ORDER BY 1"
    )["rows"]
    b = call_tool("switch_source", {"source": "duckdb"})
    assert b["active_source"] == "duckdb"
    rows_duckdb = run_sql(
        "SELECT main_language, COUNT(*) FROM v_conversations_active GROUP BY 1 ORDER BY 1"
    )["rows"]
    assert rows_jsonl == rows_duckdb, "same query should produce the same answer on both sources"
