# NR2Dashboard

Natural-language → dashboard over the SmartRep banking-voicebot dataset.

Type a question in English or Greek; get back a Plotly chart and a plain-language
answer. Built for the SmartRep Makeathon — Phase 0 + Phase 1 vertical slice.

## Stack

- **Streamlit** chat UI
- **DuckDB** read-only over the provided dataset (5 flat views + 3 helper views)
- **MCP server** with six read-only tools, runnable as a separate process
- **Gemini** (`gemini-2.5-flash` by default) for NL→SQL+chart-spec via function calling
- **Plotly** for rendering
- **pytest** for metric-correctness tests against the dictionary

## Layout

```
.
├── app.py                       # Streamlit entry point
├── orchestrator.py              # LLM tool-use loop
├── llm_client.py                # Thin Gemini abstraction
├── mcp_server.py                # MCP server (stdio / http) wrapping mcp_tools
├── mcp_tools.py                 # The six tool implementations
├── db.py                        # DuckDB connection + source switching
├── sql_safety.py                # SELECT/WITH-only validator (sqlglot)
├── renderer.py                  # Chart spec → Plotly figure
├── views.sql                    # Helper views for hot joins
├── prompts/system.md            # Locked-down system prompt
├── eval/
│   ├── questions.yaml           # 30 eval questions across all shapes/languages
│   ├── golden.yaml              # Hand-written SQL + bounds for 10
│   ├── test_metrics.py          # Metric-correctness pytest
│   └── test_orchestrator_stub.py# Orchestrator wiring test (no LLM call)
├── scripts/smoke.py             # End-to-end smoke against Gemini
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

# End-to-end smoke against Gemini
uv run python scripts/smoke.py

# Metric-correctness tests (no LLM needed)
uv run pytest eval/ -q
```

## What works in this slice (Phase 1)

- Read-only DuckDB connection opens; 5 native views + 3 helper views present
- Source switching: `switch_source('duckdb' | 'jsonl')` flips the views and SQL stays portable
- Six MCP tools (`list_tables`, `describe_schema`, `run_sql`, `get_metric_definition`,
  `sample_rows`, `switch_source`) — all reachable both in-process and over MCP stdio
- SQL safety: `SELECT` / `WITH` only, multi-statement rejected, 10k-row cap, 256kB-byte cap
- System prompt encodes schema summary, verbatim metric formulas, chart rubric,
  language-mirroring instruction, and a JSON output contract
- Auto-injection of metric definitions when the user mentions a known KPI name
  (English or Greek triggers)
- Renderer supports bar / line / area / pie / donut / scatter / heatmap / kpi / table
  with style overrides (palette, sort, top-N, threshold colors)
- Streamlit shell with chat history, dataset switcher, "Show SQL" expander,
  "Clear conversation" button, and schema cheatsheet
- 30-question eval suite, 10 hand-written golden answers, 15 passing tests

## Hard rules honored

- The dataset is never modified (read-only DuckDB; helper views are TEMP views).
- No hand-built "if query contains X return Y" lookup table.
- MIT-licensed.

## License

MIT — see [LICENSE](LICENSE).
