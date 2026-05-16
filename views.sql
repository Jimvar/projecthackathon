-- Helper views for hot joins. Registered in-memory on each connection
-- since the DuckDB file is opened read-only. Safe to run repeatedly.

-- v_conv_with_intent: every conversation joined to the first user turn's
-- detected_intent (so "by intent" queries don't need a GROUP-BY-FIRST trick).
CREATE OR REPLACE TEMP VIEW v_conv_with_intent AS
SELECT
  c.*,
  fi.detected_intent AS first_intent,
  fi.intent_confidence AS first_intent_confidence
FROM v_conversations_active c
LEFT JOIN (
  SELECT conversation_id,
         detected_intent,
         intent_confidence,
         ROW_NUMBER() OVER (
           PARTITION BY conversation_id
           ORDER BY time_in_call_secs ASC
         ) AS rn
  FROM v_turns
  WHERE role = 'user' AND detected_intent IS NOT NULL
) fi
  ON fi.conversation_id = c.conversation_id AND fi.rn = 1;

-- v_eval_pivot: one row per conversation with each criterion's result as a column.
-- DuckDB requires explicit IN-list for PIVOT inside a view.
CREATE OR REPLACE TEMP VIEW v_eval_pivot AS
PIVOT v_evaluations
ON criterion_id IN (
  'authentication_completed', 'intent_resolved', 'escalation_triggered',
  'compliance_disclaimer_given', 'pii_handled_safely', 'fallback_count_acceptable',
  'language_consistency', 'tool_call_success_rate'
)
USING any_value(result)
GROUP BY conversation_id, start_time, bot_version, main_language, segment, region;

-- v_conv_with_dc: one row per conversation with each data-collection field's value as a column.
CREATE OR REPLACE TEMP VIEW v_conv_with_dc AS
PIVOT v_data_collection
ON field_id IN (
  'customer_segment', 'region', 'declared_language', 'caller_line_type',
  'account_type_referenced', 'transfer_amount_bucket', 'transfer_destination_country',
  'card_type_referenced', 'loan_type_inquired', 'auth_method_used',
  'self_service_completed', 'promised_callback', 'complaint_detected', 'topic_tags'
)
USING any_value(value)
GROUP BY conversation_id, start_time, bot_version, main_language, segment, region;
