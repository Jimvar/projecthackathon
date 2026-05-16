# 5-minute demo script

Rehearse twice before showtime. Run `scripts/preflight.py` once before
opening Streamlit to confirm every flagship question still passes.

---

## 0:00 — Problem framing (30 s)

> "Ops shouldn't need SQL to know how the bot is doing. We built a chat
> where a manager types a question in English or Greek and gets a chart
> back. Underneath: Gemini drives a read-only DuckDB through an MCP
> server — same dataset every team got, no regeneration."

Show the Streamlit URL. Point at the **Source** / **Provider** / **Model**
strip above the chat. Mention that the source toggle is real.

## 0:30 — Three English queries (90 s)

Type these in order. Each takes ~5 s.

1. **Pie chart (verbatim from brief):**
   *"Show me a pie chart of Greek vs English users."*
   - Highlight: chart appears, plus the SQL is one click away in
     **Show SQL**. Greek ≈85% / English ≈15%.

2. **Threshold-coloring (PPTX example):**
   *"Show me containment rate by intent type this week."*
   - Highlight: bars are color-coded by the threshold rule (purple ≥85%,
     orange below). Underline that the model picked up "this week" and
     anchored it to `MAX(start_date)`, not real-world today (the
     dataset is from 2026).

3. **Style override (PDF brief tests this):**
   *"Break down segments as a donut chart with blue colors."*
   - Highlight: the `chart.type` is "donut" and the palette is "blue" —
     style words mapped into spec fields, no hardcoded routing.

## 2:00 — Greek query, Greek answer (60 s)

Type *"Δείξε μου τον μέσο χρόνο κλήσης ανά περιοχή."*

- Greek question in → Greek answer out, SQL still English column names.
- Mention that 7+ Greek questions are in the eval set.

## 3:00 — Follow-up chain (60 s)

Three turns in a row:

1. *"Top 5 regions by call volume, sorted descending."*
2. *"Now break that down by language."*
3. *"Show as a donut chart."*

Highlight: each follow-up reuses the prior SQL and only edits what
changed. Open `prompts/system.md` and point at the
**Follow-up handling** table to prove there's no hand-coded mapping —
it's just the prompt.

## 4:00 — Multi-source toggle (30 s)

Click the **Data source** radio from `duckdb` → `jsonl` in the sidebar.
The **Source** badge above the chat updates. Re-run the pie-chart
question.

> "Same SQL, same answer, different physical source — that's the
> multi-source bonus from the PDF brief."

## 4:30 — Anomaly hunt (30 s)

Type *"Anything weird about tool success in the last 90 days?"*

- Highlight: the model uses STDDEV + mean to surface outliers, and the
  chart visually shows a dip in the embedded **incident window**.
- Open `data/metrics_dictionary.md` on screen and point at the
  `tool_call_success_rate` formula. The model honored it.

## 5:00 — Close (10 s)

> "Eval suite is 40 questions, 26 unit tests. MIT-licensed. SQL safety
> layer rejects anything that isn't a SELECT. Provider abstraction can
> swap Gemini for OpenAI in one env var if Google's API goes down."

---

## Pre-demo checklist

```bash
# Sanity (no API call needed)
uv run pytest eval/ -q

# Verify every flagship question still passes (~60 s)
uv run python scripts/preflight.py

# Boot
uv run streamlit run app.py
```

If preflight is red, fix it. Don't demo a broken question.
