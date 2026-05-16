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


# ---------------------------------------------------------------------- Phase 3 / rules

def test_no_hardcoded_dispatch_in_source():
    """The brief forbids hand-built NL-to-result lookup tables.

    Static check: no production module may *define* a `hardcoded_dispatch`
    function/variable or a `HARDCODED_RESPONSES` lookup table — these
    are the exact shapes the example repo's starter used. We match on
    the definition form so explanatory comments that reference the
    pattern by name don't trigger a false positive.
    """
    import re as _re
    # `def name(`, `name =`, or `name: ` — any introduction of a binding.
    # The leading non-`#`/`"` anchor ensures we only match code, not
    # docstrings or comments.
    needles = ("hardcoded" + "_dispatch", "HARDCODED" + "_RESPONSES")
    patterns = [
        _re.compile(rf"^\s*(def\s+{n}\b|{n}\s*[:=])", _re.MULTILINE)
        for n in needles
    ]
    py_files = [
        p for p in ROOT.rglob("*.py")
        if ".venv" not in p.parts and p.name != "test_metrics.py"
    ]
    for path in py_files:
        text = path.read_text()
        for pat, needle in zip(patterns, needles):
            assert not pat.search(text), (
                f"{path}: contains banned definition matching {needle!r}"
            )


def test_questions_yaml_has_required_shapes():
    """All five PPTX shapes plus the Phase-3 anomaly hunts must be present."""
    qs = yaml.safe_load((ROOT / "eval" / "questions.yaml").read_text())
    shapes = {q["shape"] for q in qs}
    expected = {
        "distribution", "trend", "ranking", "comparison",
        "anomaly", "open_ended",
    }
    assert expected.issubset(shapes), f"missing shapes: {expected - shapes}"
    greek = [q for q in qs if q["language"] == "el"]
    assert len(greek) >= 6, f"need ≥6 Greek questions, have {len(greek)}"
    followups = [q for q in qs if q.get("followup_to")]
    assert len(followups) >= 2, f"need ≥2 follow-up questions, have {len(followups)}"


# ---------------------------------------------------------------------- file-system safety

@pytest.mark.parametrize(
    "bad",
    [
        "SELECT * FROM read_csv_auto('/etc/hostname')",
        "SELECT * FROM read_text('/etc/hostname')",
        "SELECT * FROM read_json_auto('/etc/passwd')",
        "SELECT * FROM read_parquet('/some.parquet')",
        "SELECT * FROM read_blob('/etc/hostname')",
        "SELECT * FROM glob('/etc/*')",
        "WITH a AS (SELECT * FROM read_csv_auto('x.csv')) SELECT * FROM a",
    ],
)
def test_run_sql_rejects_filesystem_reads(bad):
    """sql_safety must reject DuckDB's table-valued file readers — they'd
    let a prompt exfiltrate /etc/passwd etc."""
    res = run_sql(bad)
    assert "error" in res, f"should have rejected: {bad}"
    assert "can read the filesystem" in res["error"]


def test_db_connection_blocks_external_access(db):
    """Belt-and-suspenders: even if a file-read function slipped past
    sql_safety, the DuckDB connection itself should refuse external IO."""
    import duckdb as ddb
    with pytest.raises(ddb.PermissionException):
        db.execute("SELECT count(*) FROM read_csv_auto('/etc/hostname')").fetchone()


# ---------------------------------------------------------------------- pivot drift

@pytest.mark.parametrize(
    "view, source_view, column",
    [
        ("v_eval_pivot", "v_evaluations", "criterion_id"),
        ("v_conv_with_dc", "v_data_collection", "field_id"),
    ],
)
def test_pivot_in_lists_cover_dataset(db, view, source_view, column):
    """The pivot views in `views.sql` hardcode an IN-list of criterion /
    field names. If the dataset ever adds a new value, the pivot view
    silently drops it — this test catches that drift."""
    distinct = {
        r[0] for r in db.execute(
            f"SELECT DISTINCT {column} FROM {source_view}"
        ).fetchall()
    }
    pivot_cols = {c[0] for c in db.execute(f"DESCRIBE {view}").fetchall()}
    missing = distinct - pivot_cols
    assert not missing, (
        f"{view} is missing pivot columns for {column} values: {missing}. "
        f"Update the IN-list in views.sql."
    )


# ---------------------------------------------------------------------- few-shot leakage guards

def test_few_shot_explanations_have_no_baked_in_percentages():
    """Regression guard: the few-shot explanations must not contain
    specific percentages or counts pulled from the dataset.

    The model parrots the few-shot's explanation verbatim when the
    user's question matches the few-shot's question closely. We hit
    this with EN-1: the prompt said "~85% / ~15%" but the actual
    split is 76.94% / 23.06%, so the chart and the explanation
    disagreed. Strip specific numbers from few-shot prose to prevent
    recurrence.
    """
    import re as _re
    prompt = (ROOT / "prompts" / "system.md").read_text()
    # Pull every "explanation": "..." string from the few-shots block.
    explanations = _re.findall(r'"explanation"\s*:\s*"([^"]+)"', prompt)
    # Skip placeholder text from the output-contract template.
    explanations = [
        e for e in explanations
        if "one or two sentences" not in e
    ]
    # Anything that looks like a percentage in prose is suspicious —
    # numeric thresholds belong in the chart spec, not the explanation.
    # We allow methodology references like "1.5 stddev" because those
    # describe the analysis, not a data value. We also allow "≥85%" /
    # "<85%" since those describe the threshold-color rule, not the
    # observed data.
    pct = _re.compile(r"(~|approximately\s*|about\s*)\d+(\.\d+)?%")
    offenders = [e for e in explanations if pct.search(e)]
    assert not offenders, (
        "Few-shot explanations contain baked-in approximate percentages "
        "that the LLM will parrot verbatim:\n"
        + "\n".join(f"  - {o}" for o in offenders)
        + "\nRewrite to qualitative phrasing ('dominates', 'minority share')."
    )


def test_how_to_work_warns_against_reciting_numbers():
    """The 'How to work' section must explicitly tell the model not to
    recite numbers from the few-shots. Without this, the model treats
    the few-shots as a template and copies their figures."""
    prompt = (ROOT / "prompts" / "system.md").read_text()
    # The exact phrasing isn't load-bearing, but the rule must be there.
    rule_keywords = ["recite", "few-shot", "SQL result"]
    section = prompt.split("## How to work", 1)
    assert len(section) == 2, "'How to work' section missing from system.md"
    body = section[1].split("\n## ", 1)[0].lower()
    for kw in rule_keywords:
        assert kw.lower() in body, (
            f"'How to work' is missing the keyword {kw!r}. Without an "
            "explicit warning, the model will parrot few-shot figures."
        )


# ---------------------------------------------------------------------- anomaly-hunt prompt guards

def test_anomaly_hunt_section_requires_time_series():
    """Regression guard: the anomaly-hunt section must tell the model
    that a time-series chart is required and a bare KPI is not enough.
    Otherwise the LLM regresses and returns a KPI-only answer to
    "what's weird about X?" — which hides the very anomaly the
    user is asking about.
    """
    prompt = (ROOT / "prompts" / "system.md").read_text()
    # Find the anomaly-hunt section body.
    sections = prompt.split("## ")
    anomaly_sections = [s for s in sections if s.startswith("Anomaly-hunt")]
    assert anomaly_sections, "no Anomaly-hunt section found in system.md"
    body = anomaly_sections[0].lower()
    # The section must mention that a time-series / line chart is required
    # and call out that a bare KPI is insufficient.
    must_contain = ["time-series", "line", "kpi"]
    for needle in must_contain:
        assert needle in body, (
            f"Anomaly-hunt section is missing the keyword {needle!r}. "
            "Without this, the LLM regresses to KPI-only answers."
        )


def test_anomaly_hunt_fewshot_is_multi_panel():
    """The anomaly-hunt few-shot is the model's primary reference for
    the shape of an anomaly-hunt answer. It must demonstrate a
    multi-panel response — a single-chart line would let the model
    drift toward single-chart KPIs."""
    prompt = (ROOT / "prompts" / "system.md").read_text()
    # Pull the anomaly-hunt example by header.
    anchor = "### Anomaly hunt"
    idx = prompt.find(anchor)
    assert idx >= 0, "anomaly-hunt few-shot header missing from system.md"
    # The example ends at the next top-level or third-level heading.
    rest = prompt[idx:]
    end = min(
        (rest.find("\n## ", 1) if rest.find("\n## ", 1) != -1 else len(rest)),
        (rest.find("\n### ", 1) if rest.find("\n### ", 1) != -1 else len(rest)),
    )
    snippet = rest[:end].lower()
    assert '"panels"' in snippet, (
        "anomaly-hunt few-shot must be a multi-panel response — see "
        "the 'Anomaly-hunt mode' rule. Single-chart examples teach the "
        "LLM the wrong pattern."
    )
    # And the panels must include a line chart (the trend over time).
    assert '"type": "line"' in snippet, (
        "anomaly-hunt few-shot must include a line panel."
    )


# ---------------------------------------------------------------------- metric formula consistency

def test_metric_formulas_match_dictionary():
    """The system prompt must quote the metric formulas verbatim from
    the dictionary. If `prompts/system.md` drifts away from
    `data/metrics_dictionary.md`, the LLM gets the wrong formula and
    the existing tests don't catch it."""
    prompt = (ROOT / "prompts" / "system.md").read_text()
    # The exact formula strings the dictionary defines (copied verbatim
    # from data/metrics_dictionary.md). If the dictionary changes upstream,
    # these need a manual update — but that's exactly the human-checked
    # gate we want.
    required = [
        "COUNT(call_successful = 'success') / COUNT(*)",
        "COUNT(call_successful = 'unknown') / COUNT(*)",
        "COUNT(termination_reason = 'caller_hung_up') / COUNT(*)",
        "AVG(csat_score)",
        "AVG(call_duration_secs)",
    ]
    missing = [s for s in required if s not in prompt]
    assert not missing, (
        f"system prompt is missing verbatim metric formulas: {missing}. "
        "If the dictionary changed, update prompts/system.md."
    )


# ---------------------------------------------------------------------- env override

def test_row_cap_env_override(monkeypatch):
    """ROW_CAP must be controllable via NR2_ROW_CAP for ad-hoc judge requests."""
    monkeypatch.setenv("NR2_ROW_CAP", "42")
    # Force a re-import so the module-level constant re-reads the env.
    import importlib
    import mcp_tools
    importlib.reload(mcp_tools)
    assert mcp_tools.ROW_CAP == 42
    # Restore so subsequent tests see the default.
    monkeypatch.delenv("NR2_ROW_CAP")
    importlib.reload(mcp_tools)
    assert mcp_tools.ROW_CAP == 10_000
