# NR2Dashboard

Natural-language → dashboard over the SmartRep banking-voicebot dataset.

Type a question in English or Greek; get back a Plotly chart and a plain-language
answer. Built for the SmartRep Makeathon — Phases 0, 1, 2, 3, and 4.

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
├── orchestrator.py              # LLM tool-use loop (+ per-turn JSONL logging)
├── llm_client.py                # Thin Gemini abstraction
├── mcp_server.py                # MCP server (stdio / http) wrapping mcp_tools
├── mcp_tools.py                 # The 8 tool implementations
├── db.py                        # DuckDB connection + source switching
├── sql_safety.py                # SELECT/WITH-only validator (sqlglot)
├── renderer.py                  # Chart spec → Plotly figure
├── views.sql                    # Helper views for hot joins
├── turn_log.py                  # Per-turn JSONL logger (logs/turns-YYYY-MM-DD.jsonl)
├── prompts/system.md            # Locked-down system prompt (incl. few-shots)
├── eval/
│   ├── questions.yaml           # 30 eval questions across all shapes/languages
│   ├── golden.yaml              # Hand-written SQL + bounds for 10
│   ├── test_metrics.py          # Metric-correctness pytest
│   └── test_orchestrator_stub.py# Orchestrator wiring test (no LLM call)
├── scripts/
│   ├── smoke.py                 # 5-question smoke against Gemini
│   ├── preflight.py             # 60-s pre-demo check (one per shape + flagships)
│   └── run_eval.py              # Full eval-suite runner with pass/partial/fail grid
├── .streamlit/
│   ├── config.toml              # Custom light theme (purple primary)
│   └── secrets.toml.example     # Template for Streamlit Cloud secrets
├── Dockerfile                   # Railway / HF Spaces / any container target
├── docs/DEMO.md                 # 5-minute demo script (rehearse twice)
├── data/                        # Provided dataset — DO NOT MODIFY
│   ├── conversations.duckdb
│   ├── conversations.jsonl
│   ├── schema.md
│   └── metrics_dictionary.md
└── logs/                        # Per-turn JSONL logs (created at runtime)
```

## Setup

Python 3.10 or newer.

```bash
# Pick one:
uv sync                              # uses uv.lock for reproducible installs
pip install -e .                     # editable install via setuptools
pip install -r requirements.txt      # dependency-only install (no editable)

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

# 60-second pre-demo check (one question per shape + the brief's flagships)
uv run python scripts/preflight.py

# Full eval suite with pass/partial/fail grid
uv run python scripts/run_eval.py
uv run python scripts/run_eval.py --shape ranking
uv run python scripts/run_eval.py --language el

# Metric-correctness + tool unit tests (no LLM needed)
uv run pytest eval/ -q
```

### Optional: switch to OpenAI

For demo-day outage insurance, the orchestrator supports OpenAI as a
drop-in alternative. Set both keys in `.env`, then flip the provider:

```bash
OPENAI_API_KEY=sk-...
LLM_PROVIDER=openai   # default is "gemini"
OPENAI_MODEL=gpt-4o-mini
```

The sidebar shows the active provider so the audience can see which
brain is running.

## Deploy

### Streamlit Community Cloud (recommended)

1. Push the repo public on GitHub.
2. Sign in at https://share.streamlit.io and pick this repo.
3. Set the entry point to `app.py`.
4. In **Settings → Secrets**, paste the contents of
   `.streamlit/secrets.toml.example` and fill in your `GEMINI_API_KEY`.
5. Deploy. The free tier handles the demo load.

### Railway / Hugging Face Spaces (Docker)

```bash
docker build -t nr2dashboard .
docker run -p 8501:8501 -e GEMINI_API_KEY=... nr2dashboard
```

Both Railway and HF Spaces will pick up the included `Dockerfile`. Set
`GEMINI_API_KEY` (and optionally `OPENAI_API_KEY` + `LLM_PROVIDER`) as
platform secrets — never in the image.

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

### Phase 4 — Polish, deploy, demo prep
- **SQL result cache** in `mcp_tools.run_sql`: `(source, normalized_sql)`
  keyed, LRU bounded at 128 entries, returns `cached=True` on hit. Demo
  re-asks come back in ~1 ms instead of ~4 s.
- **OpenAI provider** in `llm_client.py` as drop-in insurance. Flip
  `LLM_PROVIDER=openai` to swap. Sidebar shows active provider.
- **Custom Streamlit theme** (`.streamlit/config.toml`) with the
  project's purple primary color matching the threshold-bar palette.
- **`scripts/preflight.py`** — 8-question pre-demo check (one per shape
  plus the brief's flagships) that exits non-zero on any failure.
- **Deploy artifacts**: `Dockerfile`, `.dockerignore`,
  `.streamlit/secrets.toml.example`, and a README section covering
  Streamlit Cloud, Railway, and HF Spaces.
- **`docs/DEMO.md`** — minute-by-minute 5-minute demo script.
- Cache hit/miss stats surfaced in the Streamlit sidebar with a
  "Clear cache" button next to "Clear chat".
- 5 new tests (cache hit / source-keyed cache / cache reset / provider
  factory / OpenAI message translation) — **31/31 passing**.

### Phase 3 — Conversation memory & multi-source bonus
- Orchestrator now caps replayed history at `MAX_HISTORY_TURNS = 12`
  entries (6 exchanges) so long sessions don't blow Gemini's context.
- Each model reply is replayed as a JSON contract so follow-up questions
  ("now break that down by language", "show that as a line chart",
  "only the last 7 days") let Gemini edit the prior SQL minimally.
- New system-prompt sections: **Follow-up handling** with a table of common
  refinements and **Anomaly-hunt mode** pointing at the embedded
  patterns (incident window, v2.2.1→v2.3.0 step-change, regional tilt).
- Two new few-shots: a follow-up chain demonstrating SQL reuse, and an
  anomaly hunt that surfaces the tool-success incident window.
- **Per-turn JSONL logger** (`turn_log.py`) — every `Orchestrator.run()`
  appends `{ts, source, user_message, sql, chart_spec, explanation,
  error, latency_ms, tool_calls}` to `logs/turns-YYYY-MM-DD.jsonl`.
- Streamlit shell: active-source banner above the chat (so a judge can
  see when the data source toggles mid-conversation), and a per-turn
  "Copy as Markdown" panel for pasting answers into Slack/PRs/tickets.
- Eval set grew 30 → 40 questions: 3 follow-up chains, 2 anomaly hunts,
  5 adversarial cases (typos, no-NPS clarification, ambiguous "lately",
  out-of-window comparison, Greek anomaly hunt).
- Static guard (`test_no_hardcoded_dispatch_in_source`) prevents the
  example repo's banned NL-to-SQL lookup pattern from sneaking back in.

## Hard rules honored

- The dataset is never modified (read-only DuckDB; helper views are `TEMP`).
- No hand-built "if query contains X return Y" lookup table.
- MIT-licensed.

## License

MIT — see [LICENSE](LICENSE).
