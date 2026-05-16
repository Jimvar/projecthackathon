# NR2Dashboard

Natural-language → dashboard over the SmartRep banking-voicebot dataset.

Type a question in English or Greek; get back a Plotly chart and a plain-language
answer. Built for the SmartRep Makeathon — Phases 0, 1, and 2.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Streamlit Chat UI  (history, charts, source toggle,        │
│                      time-scope pills, Show-SQL expander)   │
└───────────────────────────┬─────────────────────────────────┘
                            │ user message + history
                            ▼
┌─────────────────────────────────────────────────────────────┐
│   Orchestrator                                              │
│   - Gemini function-calling tool-use loop                   │
│   - System prompt = schema + metric formulas + chart rubric │
│   - Auto-injects metric defs when user names a KPI          │
│   - Parses JSON contract from final reply                   │
└───────┬──────────────────────────────────┬──────────────────┘
        │ tool calls                       │ chart spec + SQL + explanation
        ▼                                  ▼
┌─────────────────────────┐    ┌────────────────────────────┐
│   Tools (mcp_tools.py)  │    │   Renderer (Plotly)        │
│  • list_tables          │    │   bar / line / area /      │
│  • describe_schema      │    │   pie / donut / scatter /  │
│  • run_sql (validated)  │    │   heatmap / kpi / table    │
│  • get_metric_def       │    │   + palette / sort / top-N │
│  • sample_rows          │    │   + threshold colors       │
│  • value_counts         │    └────────────────────────────┘
│  • time_range           │
│  • switch_source        │
│  (Also re-exposed by    │
│   mcp_server.py over    │
│   stdio for the brief)  │
└──────────┬──────────────┘
           │ read-only
           ▼
   data/conversations.duckdb   ↔   data/conversations.jsonl
   (5 native flat views,            (read_json_auto → same column shape
    3 helper views)                  via v_conversations_jsonl)
```

## Stack

- **Streamlit 1.57** chat UI (uses `st.pills` for the time-scope chips)
- **DuckDB** read-only over the provided dataset; helper views are `TEMP`
- **MCP server** (`mcp_server.py`) wrapping the 8 tools via FastMCP/stdio
- **Gemini** (`gemini-2.5-flash` by default) for NL → SQL + chart-spec
- **Plotly** for rendering, 9 chart families
- **sqlglot** SELECT/WITH-only safety layer
- **pytest** for metric-correctness against the dictionary + tool unit tests

## Layout

```
.
├── app.py                       # Streamlit entry point
├── orchestrator.py              # LLM tool-use loop
├── llm_client.py                # Thin Gemini abstraction
├── mcp_server.py                # MCP server (stdio / http) wrapping mcp_tools
├── mcp_tools.py                 # The 8 tool implementations
├── db.py                        # DuckDB connection + source switching
├── sql_safety.py                # SELECT/WITH-only validator (sqlglot)
├── renderer.py                  # Chart spec → Plotly figure
├── views.sql                    # Helper views for hot joins
├── prompts/system.md            # Locked-down system prompt (incl. few-shots)
├── eval/
│   ├── questions.yaml           # 30 eval questions across all shapes/languages
│   ├── golden.yaml              # Hand-written SQL + bounds for 10
│   ├── test_metrics.py          # Metric-correctness pytest
│   └── test_orchestrator_stub.py# Orchestrator wiring test (no LLM call)
├── scripts/
│   ├── smoke.py                 # 5-question smoke against Gemini
│   └── run_eval.py              # Full eval-suite runner with pass/partial/fail grid
├── data/                        # Provided dataset — DO NOT MODIFY
│   ├── conversations.duckdb
│   ├── conversations.jsonl
│   ├── schema.md
│   └── metrics_dictionary.md
└── logs/                        # Per-turn JSONL logs (created at runtime)
```

## Setup

```bash
# Install deps
uv sync          # or: pip install -e .

# Configure Gemini
cp .env.example .env
# then edit .env and put your GEMINI_API_KEY in
```

Get an API key at https://aistudio.google.com/apikey. The default model is
`gemini-2.5-flash`; override with `GEMINI_MODEL` in `.env`.

## Run

```bash
# Streamlit UI
uv run streamlit run app.py

# MCP server as a separate process (stdio)
uv run python mcp_server.py

# 5-question smoke against Gemini
uv run python scripts/smoke.py

# Full eval suite with pass/partial/fail grid
uv run python scripts/run_eval.py
uv run python scripts/run_eval.py --shape ranking
uv run python scripts/run_eval.py --language el

# Metric-correctness + tool unit tests (no LLM needed)
uv run pytest eval/ -q
```

## What works

### Phase 0 — Setup
- uv project; MIT license; `.env.example` for `GEMINI_API_KEY` and `GEMINI_MODEL`.

### Phase 1 — Vertical slice
- Read-only DuckDB; 5 native views + 3 helper views (`v_conv_with_intent`,
  `v_eval_pivot`, `v_conv_with_dc`).
- Source switching: same SQL works against `duckdb` and `jsonl`.
- SELECT/WITH-only SQL safety; 10k-row cap; 256kB-byte cap.
- MCP server reachable over stdio with 8 tools.
- Gemini function-calling tool-use loop with auto-injected metric definitions
  on English and Greek triggers.
- Plotly renderer: bar / line / area / pie / donut / scatter / heatmap / kpi /
  table; palette, sort, top-N, and threshold-color overrides.
- 30-question eval, 10 golden formulas, 21 passing tests.

### Phase 2 — Breadth & language coverage
- Two new exploration tools: `value_counts(table, column)` and
  `time_range(table, column)` for fast schema/window probes.
- Helper view rename to match plan vocabulary: `v_conv_with_dc`.
- System prompt rewritten with: a chart-shape rubric, an explicit
  style-override mapping, a time-window anchoring rule (anchor relative
  dates to `MAX(start_date)`, not real-world today), and **8 few-shot
  examples** including 4 Greek ones plus a "clarify, don't invent"
  example for unanswerable questions.
- Streamlit time-scope pills ("Full window" / "Last 30 days" / "Last 7 days")
  that augment the next user message.
- Loading + error states: SQL failures, empty results, and orchestrator
  exceptions all surface in the chat instead of failing silently.
- `scripts/run_eval.py` — runs all 30 questions, tags pass/partial/fail,
  writes JSONL log, prints by-shape summary.

## Hard rules honored

- The dataset is never modified (read-only DuckDB; helper views are `TEMP`).
- No hand-built "if query contains X return Y" lookup table.
- MIT-licensed.

## License

MIT — see [LICENSE](LICENSE).
