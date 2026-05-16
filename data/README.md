# Data

Synthetic banking voicebot dataset in a realistic nested conversational-AI log format, with a DuckDB layer that flattens it for SQL.

**The dataset is pre-built and committed.** All teams work from the same `conversations.duckdb`. Do not regenerate.

## Quick start

```python
import duckdb
con = duckdb.connect("data/conversations.duckdb", read_only=True)
con.execute("SELECT COUNT(*) FROM v_conversations").fetchone()
```

## Files

| File | What |
| --- | --- |
| `conversations.jsonl` | Raw nested JSON, one conversation per line. ~50 MB. |
| `conversations.duckdb` | Single-file DB with the nested raw table + 5 flat views. ~25 MB. |
| `schema.md` | **Start here.** Column dictionary for the raw table and all views. |
| `metrics_dictionary.md` | KPI definitions — make sure your dashboards match these. |

## Patterns baked into the data

The dataset is deliberately *not* uniform. Several patterns are embedded so good dashboards can surface real findings. They include daily and weekly seasonality, a release step-change at a known date, an anomaly window, language tilt by region, and behavioral cascades between successive calls.

If a dashboard you're judging just shows flat trendlines on every metric, the team probably averaged across the entire window — push them to slice by time.

## Why this shape?

The nested format has built-in extension points for analytics — `analysis.evaluation_criteria_results`, `analysis.data_collection_results`, and `conversation_initiation_client_data.dynamic_variables` are all designed for downstream use.
