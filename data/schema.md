# Schema

Two layers. The **raw** table preserves the original nested shape of each conversation. The **views** flatten the most common access patterns so you don't need to UNNEST.

---

## `conversations_raw` (nested, one row per call)

| Column | Type | Notes |
| --- | --- | --- |
| `conversation_id` | STRING | Primary key. `conv_<hex>` |
| `agent_id` | STRING | `agt_bank_voicebot_v2_2_1` or `agt_bank_voicebot_v2_3_0` |
| `agent_name` | STRING | `bank_voicebot` |
| `user_id` | STRING | Stable per caller across calls |
| `status` | STRING | Always `done` in this dataset |
| `start_time` | TIMESTAMP | Call start in UTC |
| `call_duration_secs` | INT | End-to-end call length |
| `metadata` | STRUCT | See below |
| `analysis` | STRUCT | See below |
| `transcript` | ARRAY<STRUCT> | One element per turn — see below |
| `conversation_initiation_client_data` | STRUCT | Dynamic vars set when the call started |

### `metadata`
| Field | Type | Notes |
| --- | --- | --- |
| `cost.amount` | NUMERIC | EUR per call, computed from duration |
| `cost.currency` | STRING | Always `EUR` |
| `phone_call.direction` | STRING | `inbound` |
| `phone_call.from_number` | STRING | E.164, or `anonymous` |
| `phone_call.to_number` | STRING | The bank's number |
| `termination_reason` | STRING | `completed`, `transferred_to_human`, `caller_hung_up`, `silence_timeout` |
| `bot_version` | STRING | `2.2.1` (pre-release) or `2.3.0` (post-release) |

### `analysis`
| Field | Type | Notes |
| --- | --- | --- |
| `transcript_summary` | STRING | One-sentence summary (in the call's language) |
| `call_successful` | STRING | `success` / `failure` / `unknown` |
| `main_language` | STRING | `el` or `en` (detected — not always equal to declared) |
| `evaluation_criteria_results` | ARRAY<STRUCT> | See "Evaluation criteria" below |
| `data_collection_results` | ARRAY<STRUCT> | See "Data collection fields" below |

### `transcript[]`
| Field | Type | Notes |
| --- | --- | --- |
| `role` | STRING | `agent` or `user` |
| `time_in_call_secs` | INT | Offset from call start |
| `message` | STRING | Transcribed text |
| `detected_intent` | STRING | Populated on the user turn that surfaced the intent (often the first user message) |
| `intent_confidence` | FLOAT | 0.78–0.97 when populated, else null |
| `sentiment` | STRING | `positive` / `neutral` / `negative` |
| `tool_calls` | ARRAY<STRUCT> | Tool invocations on this turn — `tool_name`, `success`, `latency_ms` |

### `conversation_initiation_client_data.dynamic_variables`
| Field | Type | Notes |
| --- | --- | --- |
| `user_id` | STRING | Duplicate of top-level user_id |
| `segment` | STRING | `new` / `returning` / `premium` / `business` / `unknown` |
| `region` | STRING | `attica` / `thessaloniki` / `crete` / `patras` / `larissa` / `other_gr` / `international` |
| `preferred_language` | STRING | User profile preference (el/en) — independent of `declared_language` and `main_language` |
| `channel_origin` | STRING | Always `phone` in this dataset |
| `csat_score` | FLOAT or null | 1.0–5.0. Populated on ~30% of calls (the rest didn't survey). |
| `csat_collected` | BOOL | Whether the post-call survey was filled in |
| `outcome` | STRING | Synthetic ground-truth outcome label: `resolved` / `escalated` / `abandoned` / `timeout`. Validate against `analysis.call_successful` for fun. |

---

## Evaluation criteria

`analysis.evaluation_criteria_results` is an array of `{criterion_id, result, rationale}`. Eight criteria, every conversation. `result` ∈ `success` / `failure` / `unknown`.

| `criterion_id` | What it measures |
| --- | --- |
| `authentication_completed` | Did the user authenticate successfully when required? `unknown` for intents that don't need auth. |
| `intent_resolved` | Did the bot fully address the user's stated need without handoff? LLM-judge layer — usually agrees with `call_successful` but ~5% disagrees. |
| `escalation_triggered` | Was a handoff to a human agent triggered? This is a **state**, not a goodness signal — `success` just means "yes, escalation happened". |
| `compliance_disclaimer_given` | For loan/dispute intents: was the required regulatory disclaimer read? `unknown` for intents where no disclaimer is required. |
| `pii_handled_safely` | Did the bot avoid logging or echoing sensitive PII? Failure rate is slightly higher on bot v2.2.1 than v2.3.0. |
| `fallback_count_acceptable` | Were there ≤2 fallback turns? Tightly correlated with the `pain_point` flagged intents. |
| `language_consistency` | Did the bot respond in the user's language throughout? ~3% failure rate. |
| `tool_call_success_rate` | Did >80% of tool calls succeed? Failures spike during the incident window for transfer-category intents. |

---

## Data collection fields

`analysis.data_collection_results` is an array of `{field_id, value, rationale}`. Fourteen fields, every conversation. `value` is always a string — cast in SQL as needed. `unknown` denotes not-applicable.

| `field_id` | Values | Notes |
| --- | --- | --- |
| `customer_segment` | `new` / `returning` / `premium` / `business` / `unknown` | Same as `dynamic_variables.segment`, duplicated here for the analysis layer. |
| `region` | `attica` / `thessaloniki` / `crete` / `patras` / `larissa` / `other_gr` / `international` | |
| `declared_language` | `el` / `en` | What the caller picked in the IVR menu. |
| `caller_line_type` | `mobile` / `landline` / `international` / `withheld` | `withheld` correlates with higher escalation. |
| `account_type_referenced` | `savings` / `checking` / `credit_card` / `loan` / `unknown` | Populated when the intent touches accounts/cards/loans. |
| `transfer_amount_bucket` | `<100` / `100-500` / `500-2000` / `2000-10000` / `>10000` / `unknown` | Populated for transfer intents. `>10000` has elevated escalation. |
| `transfer_destination_country` | `gr` / `eu` / `non_eu` / `unknown` | Populated for transfer intents. |
| `card_type_referenced` | `debit` / `credit` / `prepaid` / `unknown` | Populated for card intents. |
| `loan_type_inquired` | `personal` / `mortgage` / `auto` / `business` / `unknown` | Populated for loan intents. |
| `auth_method_used` | `otp_sms` / `biometric` / `security_questions` / `none` / `failed` | Biometric share jumps post-v2.3. |
| `self_service_completed` | `true` / `false` / `not_applicable` | The KPI most ops teams care about. |
| `promised_callback` | `true` / `false` | ~6% baseline, ~25% during incident window. |
| `complaint_detected` | `true` / `false` | Distinct from `sentiment` — a complaint can be expressed calmly. |
| `topic_tags` | comma-separated string, 0–3 tags | Free-text-ish dimension. ~30 tag vocabulary. |

---

## Intent catalog

The synthetic `detected_intent` values surfaced in `transcript[].detected_intent` come from this set:

| `intent_id` | category | requires_auth |
| --- | --- | --- |
| `check_balance` | accounts | yes |
| `recent_transactions` | accounts | yes |
| `mini_statement` | accounts | yes |
| `report_lost_card` | cards | yes |
| `block_card` | cards | yes |
| `card_activation` | cards | yes |
| `pin_reset` | cards | yes |
| `replacement_card_status` | cards | yes |
| `transfer_money_iban` | transfers | yes |
| `transfer_status` | transfers | yes |
| `scheduled_transfer_setup` | transfers | yes |
| `loan_info` | loans | no |
| `loan_application_status` | loans | yes |
| `installment_inquiry` | loans | yes |
| `ebanking_login_issue` | auth | no |
| `password_reset` | auth | no |
| `dispute_transaction` | disputes | yes |
| `branch_locator` | general | no |
| `fx_rates` | general | no |
| `update_contact_info` | self_service | yes |

---

## Flat views

### `v_conversations`
One row per call. Top-level columns + a few date-derived helpers (`start_date`, `start_hour`, `start_dow`) + the most important `dynamic_variables` lifted to columns (`segment`, `region`, `csat_score`, `outcome`). Plus `bot_version` from metadata.

### `v_turns`
One row per turn, joined to the parent conversation's most-useful fields (`agent_id`, `start_time`, `main_language`). Includes `turn_number` derived via window function.

### `v_evaluations`
One row per (conversation × evaluation criterion). Use this for "X% of calls passed Y criterion" style queries.

### `v_data_collection`
One row per (conversation × extracted field). Use this for breakdowns by region/segment/transfer-amount/etc.

### `v_tool_calls`
One row per tool invocation. Use this for tool reliability / latency dashboards.
