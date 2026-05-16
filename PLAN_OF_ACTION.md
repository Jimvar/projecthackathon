# NR2Dashboard — Plan of Action (v3)

**Challenge:** SmartRep "Natural Language → Dashboard" + "Speak with Your Data" (both briefs apply)
**Tier:** Ambitious (4+ people, ~40 hours)
**Stack:** Streamlit + SQL-generation MCP server + Plotly, **Gemini or OpenAI** (function-calling on either is fine)
**Dataset:** SmartRep's provided `conversations.duckdb` / `conversations.jsonl` (from the example repo's `data/` folder)
**Python:** 3.11+ recommended, manage with `uv` or `pip` — your call

> The SmartRep example repo (`Makeathon-repo`) is **not** the starting code. It's an illustrative example, and its `starter/` and `python-sample/` folders are reference material, not scaffolding to extend. We build our own project; we only **consume** the dataset files (`data/conversations.duckdb`, `data/conversations.jsonl`, `data/schema.md`, `data/metrics_dictionary.md`) and the rules from both briefs.

> Scoring rubric, deliverables, and submission instructions are not in either brief — watch the organizer channel.

---

## 1. What we use from the example repo (dataset + docs only)

We only consume four files from `makeathon-NR2Dashboard-main/data/`:

| File | Why it matters |
|---|---|
| `conversations.duckdb` | The dataset (~25 MB). Nested raw table + five pre-built flat views. Open with `read_only=True`. |
| `conversations.jsonl` | Same data as raw nested JSON (~50 MB). Useful for the multi-source bonus via `read_json_auto`. |
| `schema.md` | ⭐ Column dictionary for the raw table and the five views. Read first; the system prompt depends on it. |
| `metrics_dictionary.md` | ⭐ Exact formulas for containment, CSAT, AHT, escalation, etc. The LLM must honor these. |

The repo's `starter/` (a hardcoded-router demo) and `python-sample/` (a Gemini hello-world) are reference material — useful to skim, but we're not building on top of them.

### The five flat views (use these — they exist already)

| View | Grain | Key columns we'll lean on |
|---|---|---|
| `v_conversations` | one row per call | `conversation_id`, `user_id`, `start_time`, `start_date`, `start_dow`, `start_hour`, `call_duration_secs`, `main_language` (`el`/`en`), `segment`, `region`, `csat_score`, `outcome` (`resolved`/`escalated`/`abandoned`/`timeout`), `bot_version` (`2.2.1`/`2.3.0`), `call_successful` (`success`/`failure`/`unknown`), `termination_reason`, `cost_amount` |
| `v_turns` | one row per turn | `conversation_id`, `turn_number`, `role` (`agent`/`user`), `time_in_call_secs`, `message`, `detected_intent`, `intent_confidence`, `sentiment`, plus parent `agent_id`, `start_time`, `main_language` |
| `v_evaluations` | (conversation × criterion) | `criterion_id` (8 values, see schema), `result` (`success`/`failure`/`unknown`), `rationale` |
| `v_data_collection` | (conversation × extracted field) | `field_id` (14 values), `value` (string, cast as needed), `rationale` |
| `v_tool_calls` | one row per tool invocation | `tool_name`, `success`, `latency_ms` |

### The metric formulas the system MUST honor

Lifted verbatim from `data/metrics_dictionary.md` — bake these into the LLM system prompt so it never invents its own definitions:

```
containment_rate   = COUNT(call_successful = 'success') / COUNT(*)
                     -- equivalent: outcome = 'resolved'
escalation_rate    = COUNT(call_successful = 'unknown') / COUNT(*)
                     -- 'unknown' encodes "transferred to human"
abandonment_rate   = COUNT(termination_reason = 'caller_hung_up') / COUNT(*)
deflection_rate    = 1 - escalation_rate       -- ≠ containment
csat               = AVG(csat_score) WHERE csat_score IS NOT NULL
csat_response_rate = COUNT(csat_score IS NOT NULL) / COUNT(*)   -- ~30%
aht_secs           = AVG(call_duration_secs)
median_handle_time = quantile_cont(call_duration_secs, 0.5)
cost_per_call      = AVG(cost_amount)
cost_per_resolved  = SUM(cost_amount) / COUNT(call_successful = 'success')
```

For criterion pass rates: `AVG(CASE result WHEN 'success' THEN 1.0 WHEN 'failure' THEN 0.0 ELSE NULL END)` excluding `unknown`. Note `escalation_triggered=success` means "escalation happened" — *state, not goodness*.

### Patterns the dataset deliberately contains (great anomaly-hunt fodder)

From `data/README.md` and `data/schema.md`:
- **Daily + weekly seasonality.** Weekend volume ~30% of weekday.
- **Release step-change** between `bot_version 2.2.1` and `2.3.0` — v2.3.0 outperforms on auth-category metrics; biometric auth share jumps post-release; PII handling failure rate is slightly higher on v2.2.1.
- **Named incident window** — `tool_call_success_rate` failures spike during this window for transfer-category intents; `promised_callback` jumps from ~6% baseline to ~25%.
- **Language tilt by region.** ~85% `el` / 15% `en` overall; English share rises in summer in tourist regions.
- **Segment effects.** `premium` has higher resolution + CSAT; `new` has the worst. Pain-point intents cluster at the bottom of CSAT rankings.
- **Behavioral cascades.** Users with an escalation in an auth-category call have different resolution rates on their next call within 24h.

Treat each of these as a target for the "anomaly hunt" and "open-ended check" question categories.

---

## 2. Definition of Done

### Must-haves (combined from both briefs)

From `BRIEF.md` (SmartRep) and `Speak_With_Your_Data_Challenge.pdf`:
- Natural-language Q in (English **or** Greek) → dashboard component(s) out.
- Five question shapes covered: **distributions, comparisons, trends, rankings, anomaly hunts, open-ended health checks**.
- Free-text questions can specify viz **type and style** (e.g., "…as a donut chart with blue colors", "sorted by revenue", "top 5 only"). PDF brief explicitly tests this.
- Conversation memory: follow-ups in the same thread work.
- Metric values match `metrics_dictionary.md` exactly.
- Live UI a judge can type into (both briefs call this out).
- **MCP server** required (PDF only — SmartRep brief is architecture-agnostic).
- **Bonus:** dataset switching mid-conversation. *Note:* SmartRep rules forbid outside data, so the "multi-source" bonus from the PDF can only mean switching between the provided `.duckdb` and `.jsonl` (same data, different access path), or between views.

### Hard rules

- ✅ Dataset as-is; no regeneration; no outside data.
- ✅ Permissive license (MIT / Apache / BSD).
- ✅ Pretrained / API LLMs allowed. Pre-trained NL-to-SQL adapters allowed.
- ❌ No hand-built lookup tables of "if query contains X return Y". (The starter's `hardcoded_dispatch` is *explicitly* labeled "REPLACE with your LLM logic" — keeping it as the final system would violate the rule.)
- ❌ No modifying or regenerating the dataset.

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Streamlit Chat UI  (chat history, charts, dataset switch)  │
└───────────────────────────┬─────────────────────────────────┘
                            │ user message + history
                            ▼
┌─────────────────────────────────────────────────────────────┐
│   LLM Orchestrator  (Gemini OR OpenAI — function calling)   │
│   Tool-use loop. System prompt = schema + metric defs +     │
│   chart rubric + Greek/English instruction.                 │
└───────┬──────────────────────────────────┬──────────────────┘
        │ MCP tool calls                   │ final answer + chart spec
        ▼                                  ▼
┌─────────────────────────┐    ┌────────────────────────────┐
│      MCP Server         │    │   Renderer (Plotly)        │
│  Tools:                 │    │   - bar / line / pie /     │
│   • list_tables         │    │     donut / heatmap /      │
│   • describe_schema     │    │     scatter / area / KPI / │
│   • run_sql(read-only)  │    │     table                  │
│   • get_metric_def      │    │   - style overrides        │
│   • sample_rows         │    │     (palette, sort, top-N) │
│   • switch_source       │    └────────────────────────────┘
│   (duckdb ↔ jsonl view) │
└─────────────────────────┘
        │ read-only
        ▼
   data/conversations.duckdb   /   data/conversations.jsonl
        (5 flat views ready)        (via DuckDB read_json_auto)
```

### LLM contract (orchestrator output each turn)

```jsonc
{
  "thought": "...",                 // log only, never shown
  "sql": "SELECT ...",              // must SELECT/WITH only; validated
  "chart": {
    "type": "bar | line | pie | donut | heatmap | scatter | area | kpi | table",
    "x": "column_name",
    "y": "column_name_or_metric",
    "series": "optional_grouping_col",
    "sort": "asc | desc | none",
    "top_n": 10,
    "title": "...",
    "style": { "palette": "blue", "annotations": [...] }
  },
  "explanation": "Plain-language answer in the user's language"
}
```

If you skim the repo's `starter/demo.py`, it returns dicts shaped `{chart_type, title, explanation, data, sql}`. That's a reasonable I/O shape but not a requirement — design the contract that fits the renderer you build.

### Why this shape

- **MCP server** — required by the PDF brief; cleanly isolates data access from the LLM and gives us a clean spot to enforce read-only SQL.
- **SQL generation** — listed in both briefs as an architecture option; pairs naturally with a DuckDB-native dataset.
- **Streamlit** — both briefs suggest it; highest live-UI velocity for a Python team.
- **Plotly** — both briefs list it; renders inside Streamlit via `st.plotly_chart`; covers every chart family named.
- **Gemini or OpenAI** — both support function calling, both are explicitly allowed by the briefs ("Claude API, OpenAI API, or open-source model"). Pick whichever your team already has keys for. Plan supports either.

---

## 4. Team — four workstreams

| Stream | Owner | Owns |
|---|---|---|
| **A. Data & MCP** | Data engineer type | Read-only DuckDB connection; SQL safety layer; MCP server with the six tools above; JSONL view registration |
| **B. LLM Orchestration** | Prompting/AI lead | System prompt, LLM tool-use loop, NL→SQL+chart spec, conversation memory, Greek/English handling |
| **C. UI & Visualization** | Frontend type | Streamlit chat, Plotly renderer, dataset switcher, style overrides, error states, "Show SQL" expander |
| **D. Quality & Demo** | PM / tech lead | Eval set of ~30 questions, metric-correctness tests against `metrics_dictionary.md`, deployment, demo script, README |

Sync points: end of Phase 1, end of Phase 3, end of Phase 4.

---

## 5. Phased schedule (40 hours)

### Phase 0 — Setup & ground truth (Hours 0–2, everyone)

1. Create a fresh project folder for our build. Copy the four files we need out of the example repo:
   ```bash
   mkdir nr2dashboard && cd nr2dashboard
   mkdir data
   cp ../makeathon-NR2Dashboard-main/data/conversations.duckdb data/
   cp ../makeathon-NR2Dashboard-main/data/conversations.jsonl  data/
   cp ../makeathon-NR2Dashboard-main/data/schema.md            data/
   cp ../makeathon-NR2Dashboard-main/data/metrics_dictionary.md data/
   git init && git add . && git commit -m "Import provided dataset"
   ```
   Do **not** modify any of these files — that's a hard rule.
2. **Read end-to-end as a team**, in this order: SmartRep `BRIEF.md` → `data/schema.md` → `data/metrics_dictionary.md` → the PDF brief again. The schema doc is the single most important reference and everyone needs it in their head. (Optionally skim the example repo's `starter/demo.py` for an I/O pattern — it's reference, not a base to extend.)
3. Smoke-test the dataset:
   ```python
   import duckdb
   con = duckdb.connect("data/conversations.duckdb", read_only=True)
   print(con.execute("SHOW TABLES").fetchall())
   print(con.execute("SELECT COUNT(*) FROM v_conversations").fetchone())
   for v in ["v_conversations","v_turns","v_evaluations","v_data_collection","v_tool_calls"]:
       print(v, con.execute(f"DESCRIBE {v}").fetchall())
   ```
4. Decide as a team: **Gemini or OpenAI?** Pick whichever the team already has working keys for. Then wire up a hello-world that confirms function-calling works:
   - **Gemini path** — `pip install google-genai python-dotenv`; one call with `client.models.generate_content(model="gemini-2.5-flash", ...)`.
   - **OpenAI path** — `pip install openai python-dotenv`; one call with `client.chat.completions.create(model="gpt-4o-mini", tools=[...])` (or Responses API).
5. Initialize the project environment. Either tool is fine:
   ```bash
   # Option A: uv
   uv init --package nr2dashboard
   uv add duckdb streamlit plotly python-dotenv pandas sqlglot mcp pytest pyyaml \
          google-genai     # or: openai
   # Option B: pip + venv
   python -m venv .venv && source .venv/bin/activate
   pip install duckdb streamlit plotly python-dotenv pandas sqlglot mcp pytest pyyaml google-genai
   ```
6. Add a `LICENSE` file (MIT). Commit it. Both briefs require permissive licensing.

**Exit criteria:** everyone can query the DuckDB, everyone has read the schema + metrics dict, the chosen LLM (Gemini or OpenAI) returns a hello-world reply.

---

### Phase 1 — Vertical slice end-to-end (Hours 2–10)

Goal: one question types in via Streamlit, one chart comes out. Crappy is fine — real is the point.

**Stream A — MCP server skeleton**
- Build `mcp_server.py` exposing six tools:
  - `list_tables()` → `['conversations_raw','v_conversations','v_turns','v_evaluations','v_data_collection','v_tool_calls']`.
  - `describe_schema(table)` → column names, types, plus the schema.md note for each column where one exists.
  - `run_sql(query)` → executes a read-only DuckDB query. Validate with `sqlglot`: accept only `SELECT`/`WITH`; reject DDL/DML; reject multi-statement; cap rows (e.g., 10,000) and bytes returned.
  - `get_metric_definition(name)` → returns the relevant chunk of `metrics_dictionary.md`.
  - `sample_rows(table, n=5)` → fast peek at data.
  - `switch_source(source)` → toggles between the DuckDB-native view set and a JSONL-backed view registered via `read_json_auto('data/conversations.jsonl')`.
- DuckDB connection opens with `read_only=True` (matches `data/README.md`).

**Stream B — Orchestrator v0 (Gemini or OpenAI)**
- System prompt assembled from: (a) a one-paragraph dataset summary, (b) the view list with key columns, (c) the metric formulas verbatim, (d) a chart-type rubric, (e) "respond in the user's language; Greek question → Greek answer."
- Tool-use loop using the chosen LLM's function-calling. Typical flow: model calls `describe_schema` once, then `run_sql`, then returns the JSON contract above.
- Keep the LLM client behind a thin `LLMClient` abstraction (`call(messages, tools) -> response`) so swapping Gemini ↔ OpenAI is one file's worth of change.
- Inject `metric_definition` automatically when the user message mentions a known metric name (containment, CSAT, AHT, escalation, deflection, abandonment).

**Stream C — Streamlit shell**
- `app.py` with `st.chat_input`, `st.chat_message`, a sidebar showing active source (`DuckDB` / `JSONL`), model name, and a "clear conversation" button.
- Plotly renderer consuming the chart spec from Stream B. Mirror the starter's output shape — the renderer should accept either `{chart_type, data: [{label,value}], …}` (starter shape) or `{chart, sql, …}` (new shape).
- Collapsible "Show SQL" expander rendering the LLM's query. *This single feature does a lot of the trust-building with judges.*
- Use `st.session_state` for chat history and `@st.cache_resource` for the MCP client + DuckDB connection.

**Stream D — Eval set + metric correctness tests**
- ~30 questions covering all five shapes. Include the two from the brief verbatim:
  - "Show me a pie chart of Greek vs English users."
  - "How is the bot doing this week?"
  - "Show me containment rate by intent type this week." (from PPTX example slide)
- At least 6 questions in Greek (use realistic phrasings — "Πόσα τηλεφωνήματα είχαμε αυτή την εβδομάδα;" etc.)
- For ~10 of these, hand-write the expected SQL **and** the expected numeric answer.
- `pytest` file that runs each metric formula directly against DuckDB and asserts the value matches the dictionary's formula. **This catches the dominant failure mode: the LLM inventing its own definition of containment / CSAT.**

**Exit criteria (Hour 10):**
- One end-to-end query works through the UI.
- 5+ of the eval questions return correct charts.
- MCP server is process-isolated from the Streamlit app and reachable.

---

### Phase 2 — Breadth of chart types & language coverage (Hours 10–20)

**Stream A**
- Add `value_counts(table, column)` and `time_range()` helper tools — fast schema-exploration primitives the LLM can call without writing SQL.
- Build materialized helper views for hot joins (commit them as `views.sql` the team can re-run):
  - `v_conv_with_intent` — `v_conversations` joined to each call's first user-turn detected_intent.
  - `v_conv_with_dc` — wide form of `v_data_collection` (one column per `field_id`).
  - `v_eval_pivot` — wide form of `v_evaluations` (one column per `criterion_id`).
  These keep the LLM's SQL short and reduce join errors.

**Stream B**
- Expand the chart rubric in the system prompt:
  - 1 categorical + 1 numeric → bar (top-N if many categories).
  - Time + numeric → line or area.
  - 2 numerics → scatter; 3rd categorical → color.
  - Parts-of-whole, ≤6 categories → pie/donut per user.
  - Two categoricals + numeric → heatmap.
  - Single number / status check → KPI card.
- **Style-override parsing.** If the user says "blue colors", "sorted by revenue", "as a donut", "top 5 only", these must appear in the `chart.style` / `chart.sort` / `chart.top_n` fields. The PDF brief explicitly tests for this.
- 3–5 Greek few-shot examples in the system prompt so the model (a) detects Greek, (b) answers in Greek, (c) still emits SQL with English column names.

**Stream C**
- Renderer support for every chart family above plus style overrides: palette name → Plotly color sequence; sort direction; top-N truncation; custom title; threshold-color rules (PPTX example: purple ≥85%, orange <85%).
- Time-range pills ("Last 7 days", "Last 30 days", "Full window") — clicking one injects a phrase into the next turn.
- Loading + error states ("I couldn't compute that — here's why").

**Stream D**
- Run the full eval suite. Tag pass/partial/fail. Triage failures into the right stream.
- Start the README: what it does, how to run, license, architecture diagram.

**Exit criteria (Hour 20):**
- All five PPTX question shapes have ≥1 passing example each.
- Greek and English both work on ≥3 questions each.
- All chart families render.

---

### Phase 3 — Conversation memory & the multi-source bonus (Hours 20–30)

**Stream B (primary)**
- Conversation memory: pass the last N turns to Gemini with user text **and** the SQL/chart spec the system produced. This is what makes follow-ups work.
- Follow-up patterns to support explicitly:
  - "Now break that down by language" → reuse last SQL, add `GROUP BY main_language`.
  - "Show that as a line chart instead" → reuse last SQL, change chart type.
  - "Only the last 7 days" → reuse last SQL, edit the date WHERE clause.
  - "Why is Tuesday so low?" → drill down, pull underlying rows.
- **Anomaly-hunt mode.** When the user asks "what's weird about X" or "where are the outliers", have Gemini compute mean/stddev or rolling stats and surface the outliers. Target the *known* embedded anomalies — incident window, v2.2.1 vs v2.3.0 step-change, regional language tilt — so the demo lands.

**Stream A**
- Register the JSONL as a DuckDB view so `switch_source('jsonl')` produces correct results on the same SQL:
  ```sql
  CREATE OR REPLACE VIEW v_conversations_from_jsonl AS
  SELECT ... FROM read_json_auto('data/conversations.jsonl');
  ```
  Keep view names parallel so SQL stays portable.
- Logging: every turn writes `{user_msg, sql, chart_spec, latency_ms, error}` to a JSONL file in `logs/`.

**Stream C**
- Dataset switcher UI ("Source: DuckDB ▾ / JSONL"). Show which is active above the chat.
- History pane with the ability to "fork" from a previous turn.
- Export buttons: "Copy SQL", "Download chart as PNG", "Copy answer as Markdown".

**Stream D**
- Adversarial / edge-case questions: typos, ambiguous timeframes, deliberately misleading ("show me revenue" when there is no revenue column — system should ask back).
- **Grep for hard-coded lookups** in the codebase to confirm zero remain — the rules forbid them. The starter's `hardcoded_dispatch` must not be the live router; it can stay in `starter/` since the brief says you can keep or delete the starter.

**Exit criteria (Hour 30):**
- Three consecutive follow-up turns produce coherent evolving charts.
- Dataset switch works mid-conversation without breaking history.
- ≥80% of the eval suite passes.

---

### Phase 4 — Polish, deploy, demo prep (Hours 30–36)

**Stream C + D**
- Visual polish: custom Streamlit theme, project header, team name.
- **Deploy** publicly. Sub-1-hour options:
  - **Streamlit Community Cloud** — free, push-to-deploy from a public repo. Best default. Set `GEMINI_API_KEY` as a secret.
  - **Hugging Face Spaces** (Streamlit template) — free, fast.
  - **Railway** — listed in the PDF; works via Dockerfile.
  - Whichever you pick, the API key goes in the platform's secret store, never in code.

**Stream B**
- Final prompt tuning against the eval set. Lock the system prompt.
- Cache: hash `(source, sql)` → result so repeated demo queries are instant.
- Confirm the `LLMClient` abstraction works against the *other* vendor too (smoke test: swap Gemini→OpenAI or vice versa, run 3 eval questions). This is cheap insurance against an outage on demo day.

**Stream A**
- Add a 60-second smoke-test script that runs all 30 eval questions and prints a pass/fail grid. Run it before the demo.

**Demo script (5 minutes, rehearse twice):**
1. 30 sec — problem framing: "Ops shouldn't need SQL to know how the bot is doing."
2. 90 sec — three queries in English, one per major chart family. Include a style override ("…as a donut chart") and the threshold-coloring example from the PPTX.
3. 60 sec — Greek query, same dataset, Greek answer back.
4. 60 sec — follow-up chain: ask, refine, drill down. Three turns minimum.
5. 30 sec — dataset switch DuckDB → JSONL, same question, same answer.
6. 30 sec — anomaly hunt: ask "anything weird about the last 90 days?" and let it surface the **incident window** or the **v2.2.1→v2.3.0 step-change**. Open `metrics_dictionary.md` on screen to prove the formula matches.

**Exit criteria (Hour 36):**
- Live URL works from a different network.
- README is judgeable.
- Demo rehearsed end-to-end.

---

### Phase 5 — Buffer, dry run, submission (Hours 36–40)

- Two full dry runs against the deployed URL, one team member acting as a skeptical judge.
- Critical bugs only. Resist scope creep.
- Submit per the organizer's instructions when they publish them.
- Tag a release in git.

---

## 6. Risk register

| Risk | Mitigation |
|---|---|
| Gemini hallucinates a metric formula | Inline the metric formulas verbatim in the system prompt; auto-inject `get_metric_definition` output whenever the user mentions a known metric; Stream D's correctness tests catch regressions. |
| Bad SQL crashes DuckDB or returns wrong joins | SELECT/WITH-only validator via `sqlglot`; pre-built helper views (`v_conv_with_intent`, etc.) so joins are mostly unnecessary; row cap; pass errors back to Gemini so it can self-correct. |
| Greek queries get answered in English (or vice versa) | Greek few-shot examples in system prompt; explicit "respond in the user's language" instruction; eval suite has ≥6 Greek questions. |
| LLM API rate-limit / 429 during demo | Add exponential backoff on the orchestrator's LLM call; cache `(source, sql)` results; have the alternate vendor (OpenAI if you chose Gemini, or vice versa) wired up through the `LLMClient` abstraction as a fallback. |
| Streamlit re-runs the whole script on every input | `st.session_state` for chat + active source; `@st.cache_resource` for MCP client + DuckDB connection. |
| Demo machine has no internet for the LLM API | Deploy publicly + bring a local laptop hotspot. |
| Hard-coded lookup creeps in under deadline pressure | Phase 3 `grep` check; code review at sync points. The repo's `starter/demo.py` uses an explicitly-banned hardcoded dispatcher — don't port that pattern into our build. |
| Four streams collide on `app.py` | Stream B and C agree on the chart-spec contract in Phase 1 hour 4 and don't change it after Phase 2. |
| The "multi-source bonus" tension between PDF and SmartRep rules | Implement it as DuckDB↔JSONL toggle on the *same* dataset. Document the interpretation in the README so a judge isn't confused. |

---

## 7. Target file layout (our own project, not the example repo)

```
nr2dashboard/                         # our project root
├── README.md                         # write — what it does + how to run
├── LICENSE                           # MIT
├── pyproject.toml                    # uv or pip; your call
├── app.py                            # Streamlit entry point
├── mcp_server.py                     # MCP server
├── orchestrator.py                   # LLM tool-use loop
├── llm_client.py                     # thin abstraction over Gemini/OpenAI
├── renderer.py                       # chart spec → Plotly figure
├── views.sql                         # helper views for hot joins
├── prompts/
│   ├── system.md                     # locked system prompt
│   └── few_shot/                     # English + Greek examples
├── eval/
│   ├── questions.yaml                # 30+ eval questions
│   ├── golden.yaml                   # expected SQL + values for 10
│   └── test_metrics.py               # metric-correctness pytest
├── logs/                             # per-turn JSONL logs
└── data/                             # copied from the example repo — DO NOT MODIFY
    ├── conversations.duckdb
    ├── conversations.jsonl
    ├── schema.md
    └── metrics_dictionary.md
```

---

## 8. Example queries the system must handle

From the briefs verbatim:
- "Show me a pie chart of Greek vs English users." → pie, `v_conversations` grouped by `main_language`.
- "How is the bot doing this week?" → open-ended; multiple KPI tiles (containment, CSAT, AHT) + a daily trend; filter `start_date >= current_date - 7`.
- "Show me containment rate by intent type this week." → bar with threshold coloring (purple ≥85%, orange <85%); join `v_conversations` to first-user-turn intent.

One per category (use these as smoke tests):

| Category | Example | View(s) | Chart |
|---|---|---|---|
| Distribution | "Break down conversations by outcome." | `v_conversations` | bar / pie |
| Trend | "Plot daily CSAT for the last 30 days." | `v_conversations` | line |
| Ranking | "Top 10 intents by AHT, slowest first." | `v_conv_with_intent` | bar desc |
| Anomaly | "Which day in the last 90 had the worst tool success rate, and which tools failed?" | `v_tool_calls` × `v_conversations` | line + annotation |
| Open-ended | "Did the v2.3.0 release help auth-category intents?" | `v_conversations` × `v_evaluations` filtered to auth criteria | grouped bar (pre/post) |

Style-override smoke tests:
- "…as a donut chart"
- "…with blue colors"
- "…sorted descending"
- "…top 5 only"

Greek smoke tests:
- "Δείξε μου τον μέσο χρόνο κλήσης ανά περιοχή." → AHT by region, bar.
- "Πώς πάει το CSAT αυτόν τον μήνα;" → CSAT trend, line.

---

## 9. What to do right now (next 30 minutes)

1. Create the project folder `nr2dashboard/` and copy the four data/doc files in from the example repo's `data/` directory. Initialize git. Don't touch the dataset files after this.
2. Read SmartRep `BRIEF.md`, `data/schema.md`, `data/metrics_dictionary.md`, and the PDF brief end-to-end as a team. Out loud if it helps.
3. Decide as a team: Gemini or OpenAI. Get the API key working in a one-line hello-world.
4. Open `data/conversations.duckdb` and run `DESCRIBE` on each of the five views.
5. Assign the four workstream owners.
6. Set up the project environment (`uv init` or `python -m venv`) with the dependencies in Phase 0 step 5.

Then start Phase 1.
