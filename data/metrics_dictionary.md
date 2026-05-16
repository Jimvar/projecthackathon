# Metrics Dictionary

So that every team computes the same number for the same word. If your dashboard says "containment rate" and the judge says "containment rate", they should mean this.

---

## Volume

**Conversations**
Count of rows in `v_conversations`. Each row is one inbound call.

**Turns**
Count of rows in `v_turns`. A turn is a single utterance by either party.

**Tool calls**
Count of rows in `v_tool_calls`. A single agent turn can invoke multiple tools.

---

## Outcomes

**Resolution / Containment Rate**
Share of calls where the bot handled the request without human help.
```
containment_rate = COUNT(call_successful = 'success') / COUNT(*)
```
Equivalently, `outcome = 'resolved'` in `dynamic_variables` (these agree by construction).

**Escalation Rate**
Share of calls where a human agent took over.
```
escalation_rate = COUNT(call_successful = 'unknown') / COUNT(*)
                = COUNT(outcome = 'escalated') / COUNT(*)
```
By convention `call_successful = 'unknown'` means "transferred" — the bot didn't fail, but it didn't succeed either.

**Abandonment Rate**
Share of calls where the caller hung up.
```
abandonment_rate = COUNT(termination_reason = 'caller_hung_up') / COUNT(*)
```

**Deflection Rate (informal)**
Share of inbound volume that did NOT escalate. Useful as a north-star number.
```
deflection_rate = 1 - escalation_rate
```
Note: deflection ≠ containment. A call that was abandoned didn't escalate, but wasn't resolved either.

---

## Quality

**CSAT (Customer Satisfaction Score)**
Mean of `csat_score` over calls where it was collected. `csat_score` is 1.0–5.0; ignore nulls.
```
csat = AVG(csat_score)  -- among rows where csat_score IS NOT NULL
csat_response_rate = COUNT(csat_score IS NOT NULL) / COUNT(*)  -- typically ~30%
```

**Sentiment-Negative Rate**
Share of turns flagged `negative` in `v_turns`.

**Fallback Count**
A "fallback" is an agent turn that asks the user to repeat / clarify ("Sorry, I didn't catch that"). Quality criterion `fallback_count_acceptable` passes when ≤2 fallbacks occurred.

**First Call Resolution (FCR)**
Share of users whose **first** call in a 24-hour window resolved without a follow-up call within that window. A more nuanced version of containment that accounts for repeat callers.

---

## Time / efficiency

**Average Handle Time (AHT)**
Mean of `call_duration_secs`. Often reported in seconds, sometimes in MM:SS.
```
aht_secs = AVG(call_duration_secs)
```

**Median Handle Time**
Use `MEDIAN` or `quantile_cont(0.5)`. Often more useful than AHT because the long-tail (escalations) inflates the mean.

**Time-to-First-Intent**
Seconds from call start until the first turn with a non-null `detected_intent`. Lower is better. Available by querying `v_turns` for the minimum `time_in_call_secs` per conversation where `detected_intent IS NOT NULL`.

---

## Tool reliability

**Tool Success Rate**
Per tool, share of `success = true` invocations.
```
SELECT tool_name, AVG(CAST(success AS DOUBLE)) FROM v_tool_calls GROUP BY 1
```

**Tool Latency**
Median and p95 of `latency_ms` per tool. Latency spikes during incident windows.

---

## Evaluation criteria pass rates

Each `criterion_id` in `v_evaluations` has a pass rate:
```
SELECT criterion_id,
       AVG(CASE WHEN result = 'success' THEN 1.0
                WHEN result = 'failure' THEN 0.0
                ELSE NULL END) AS pass_rate
FROM v_evaluations
WHERE result IN ('success', 'failure')   -- exclude 'unknown'
GROUP BY 1
```
Note that for `escalation_triggered`, `success` means escalation happened — interpret accordingly.

---

## Cost

**Cost per Call**
`AVG(cost_amount)` in EUR.

**Cost per Resolved Call**
Total cost / count of resolved calls. A more honest "what does each successful outcome cost us" number.
```
cost_per_resolution = SUM(cost_amount) / COUNT(call_successful = 'success')
```

---

## Cohort / behavioral

**Repeat Caller Rate**
Share of `user_id`s with more than one call in the window.
```
SELECT COUNT(*) FILTER (WHERE n > 1)::DOUBLE / COUNT(*)
FROM (SELECT user_id, COUNT(*) AS n FROM v_conversations GROUP BY 1)
```

**Auth-Failure Cascade**
For users with at least one `escalation_triggered = success` in an auth-category call, what's the resolution rate of their next call within 24h? Compare against the global resolution rate.

---

## Slicing dimensions worth knowing

The dataset is engineered so the following splits are interesting:

| Dimension | Source | What you'll see |
| --- | --- | --- |
| `main_language` (el/en) | `v_conversations` | ~85/15 split, English share rises in summer in tourist regions |
| `region` | `v_conversations` | Volume concentration in Attica, intent mix differs internationally |
| `segment` | `v_conversations` | Premium has higher resolution + CSAT; new has the worst |
| `bot_version` | `v_conversations` | v2.3.0 outperforms v2.2.1 on auth-category metrics |
| `start_date` / `start_dow` / `start_hour` | `v_conversations` | Seasonality; weekend volume ~30% of weekday |
| `intent` (`detected_intent`) | `v_turns` (filter to user role + non-null intent) | Pain-points cluster at bottom of CSAT rankings |
| `criterion_id` × `start_date` | `v_evaluations` | Tool-failure spike during incident window |

If a metric in your dashboard doesn't match this dictionary, that's an inconsistency you should fix — it's also one of the criteria a judge might check.
