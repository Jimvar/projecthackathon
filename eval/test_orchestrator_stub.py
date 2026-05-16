"""Stub-LLM test that exercises the orchestrator wiring without a real key.

We swap in a hand-scripted LLMClient that emits a `run_sql` tool call
followed by the JSON contract. This confirms:

* The orchestrator passes tool specs to the LLM.
* It dispatches tool calls back through `mcp_tools.call_tool`.
* It feeds tool results back as `role=tool` messages.
* It parses the final JSON contract correctly.

This is the seam where the Streamlit app meets Gemini in production.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from llm_client import LLMResponse, ToolCall  # noqa: E402
from orchestrator import Orchestrator  # noqa: E402


@dataclass
class _Script:
    """Drives an Orchestrator by replaying a canned sequence of responses."""

    responses: list[LLMResponse]
    seen: list[list[dict]]
    model: str = "stub"

    def generate(self, messages, force_text=False):  # noqa: D401, ARG002
        # Snapshot — the orchestrator mutates `messages` after we return.
        self.seen.append([dict(m) for m in messages])
        return self.responses.pop(0)


def test_orchestrator_runs_tool_then_returns_contract():
    sql = "SELECT main_language, COUNT(*) AS n FROM v_conversations GROUP BY 1 ORDER BY 2 DESC"
    contract = {
        "sql": sql,
        "chart": {
            "type": "pie",
            "x": "main_language",
            "y": "n",
            "series": None,
            "sort": "desc",
            "top_n": None,
            "title": "Calls by main language",
            "style": {"palette": "default", "thresholds": None},
        },
        "explanation": "Greek dominates the mix.",
    }

    script = _Script(
        seen=[],
        responses=[
            LLMResponse(
                text=None,
                tool_calls=[ToolCall(name="run_sql", arguments={"query": sql})],
                raw=None,
            ),
            LLMResponse(text=json.dumps(contract), tool_calls=[], raw=None),
        ],
    )

    orch = Orchestrator(client=script)  # type: ignore[arg-type]
    log = orch.run("Show me a pie chart of Greek vs English users.")

    assert log.error == ""
    assert log.sql == sql
    assert log.chart_spec["type"] == "pie"
    assert log.explanation.startswith("Greek")
    assert log.tool_calls and log.tool_calls[0]["name"] == "run_sql"
    # Two LLM calls happened — the second one saw the tool result.
    assert len(script.seen) == 2
    second_round = script.seen[1]
    assert any(m["role"] == "tool" for m in second_round)


def test_orchestrator_handles_fenced_json():
    sql = "SELECT COUNT(*) AS n FROM v_conversations"
    contract = {"sql": sql, "chart": {"type": "kpi", "y": "n", "title": "Total calls"}, "explanation": "10000 calls."}
    fenced = f"```json\n{json.dumps(contract)}\n```"

    script = _Script(seen=[], responses=[LLMResponse(text=fenced, tool_calls=[], raw=None)])

    orch = Orchestrator(client=script)  # type: ignore[arg-type]
    log = orch.run("How many calls are in the dataset?")
    assert log.error == ""
    assert log.chart_spec["type"] == "kpi"
    assert log.sql == sql


def test_orchestrator_auto_injects_metric_hint():
    """When the user mentions 'containment', the orchestrator should slip the
    metric definition into the user message before the model sees it."""
    contract = {
        "sql": "SELECT AVG(CASE WHEN call_successful = 'success' THEN 1.0 ELSE 0.0 END) AS containment FROM v_conversations",
        "chart": {"type": "kpi", "y": "containment", "title": "Containment rate"},
        "explanation": "Containment is X.",
    }
    script = _Script(seen=[], responses=[LLMResponse(text=json.dumps(contract), tool_calls=[], raw=None)])
    orch = Orchestrator(client=script, write_log=False)  # type: ignore[arg-type]
    orch.run("What's our containment rate?")

    sent_user_text = next(m["text"] for m in script.seen[0] if m["role"] == "user")
    assert "metric reference" in sent_user_text
    assert "containment_rate" in sent_user_text


def test_orchestrator_replays_prior_contract_for_followups():
    """Follow-up turns must see the prior model contract so the LLM can
    edit it minimally (Phase 3 conversation memory)."""
    prior_sql = "SELECT segment, COUNT(*) AS n FROM v_conversations GROUP BY 1 ORDER BY 2 DESC"
    prior_chart = {"type": "bar", "x": "segment", "y": "n"}

    followup_contract = {
        "sql": "SELECT segment, main_language, COUNT(*) AS n FROM v_conversations GROUP BY 1, 2 ORDER BY 1, 2",
        "chart": {"type": "bar", "x": "segment", "y": "n", "series": "main_language"},
        "explanation": "Now split by language.",
    }
    script = _Script(seen=[], responses=[LLMResponse(text=json.dumps(followup_contract), tool_calls=[], raw=None)])
    orch = Orchestrator(client=script, write_log=False)  # type: ignore[arg-type]

    history = [
        {"role": "user", "text": "Break down conversations by segment."},
        {"role": "assistant", "sql": prior_sql, "chart": prior_chart, "explanation": "Premium etc."},
    ]
    orch.run("Now break that down by language.", history=history)

    # The model should have seen the prior assistant contract verbatim
    # (so it knows what SQL to edit) and the new user turn last.
    sent = script.seen[0]
    assert any(m["role"] == "model" and prior_sql in m["text"] for m in sent), (
        "prior model contract was not replayed to the LLM"
    )
    assert sent[-1]["role"] == "user"
    assert "break that down by language" in sent[-1]["text"].lower()


def test_orchestrator_trims_history_to_max_turns():
    """A 30-turn session should only forward the last MAX_HISTORY_TURNS entries."""
    from orchestrator import MAX_HISTORY_TURNS

    script = _Script(
        seen=[],
        responses=[
            LLMResponse(
                text=json.dumps({
                    "sql": "SELECT 1 AS n",
                    "chart": {"type": "kpi", "y": "n", "title": "x"},
                    "explanation": "ok",
                }),
                tool_calls=[],
                raw=None,
            )
        ],
    )
    orch = Orchestrator(client=script, write_log=False)  # type: ignore[arg-type]

    history: list[dict] = []
    for i in range(30):
        history.append({"role": "user", "text": f"q{i}"})
        history.append({
            "role": "assistant",
            "sql": f"SELECT {i}",
            "chart": {"type": "kpi", "y": "n"},
            "explanation": f"a{i}",
        })

    orch.run("latest question", history=history)
    sent = script.seen[0]
    # +1 for the new user turn that we just added.
    assert len(sent) == MAX_HISTORY_TURNS + 1, (
        f"expected {MAX_HISTORY_TURNS + 1} messages, got {len(sent)}"
    )


def test_make_llm_client_factory_selects_provider(monkeypatch):
    """The factory should pick Gemini by default and OpenAI when LLM_PROVIDER=openai."""
    from llm_client import LLMClient, OpenAILLMClient, make_llm_client

    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")

    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert isinstance(make_llm_client(), LLMClient)

    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    assert isinstance(make_llm_client(), LLMClient)

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    client = make_llm_client()
    assert isinstance(client, OpenAILLMClient)
    assert client.provider == "openai"


def test_openai_message_translation_roundtrip():
    """Our internal message dicts should map cleanly into OpenAI's shape."""
    from llm_client import _to_openai_messages

    messages = [
        {"role": "user", "text": "Hello"},
        {"role": "model", "tool_calls": [{"name": "run_sql", "arguments": {"query": "SELECT 1"}}]},
        {"role": "tool", "name": "run_sql", "response": {"columns": ["n"], "rows": [[1]]}},
    ]
    out = _to_openai_messages(messages, system_instruction="You are a co-pilot.")

    assert out[0] == {"role": "system", "content": "You are a co-pilot."}
    assert out[1] == {"role": "user", "content": "Hello"}
    assert out[2]["role"] == "assistant"
    assert out[2]["tool_calls"][0]["function"]["name"] == "run_sql"
    assert out[3]["role"] == "tool"
    assert "rows" in out[3]["content"]


def test_orchestrator_writes_turn_log(tmp_path, monkeypatch):
    """Each run should append one JSON line to today's logs/ file."""
    import turn_log

    monkeypatch.setattr(turn_log, "LOG_DIR", tmp_path)

    script = _Script(
        seen=[],
        responses=[
            LLMResponse(
                text=json.dumps({
                    "sql": "SELECT 1 AS n",
                    "chart": {"type": "kpi", "y": "n", "title": "x"},
                    "explanation": "ok",
                }),
                tool_calls=[],
                raw=None,
            )
        ],
    )
    orch = Orchestrator(client=script)  # type: ignore[arg-type]
    orch.run("hello world")

    files = list(tmp_path.glob("turns-*.jsonl"))
    assert len(files) == 1, files
    lines = files[0].read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["user_message"] == "hello world"
    assert rec["sql"] == "SELECT 1 AS n"
    assert rec["chart_spec"]["type"] == "kpi"
    assert rec["explanation"] == "ok"
    assert rec["error"] == ""
    assert isinstance(rec["latency_ms"], int)
    assert "ts" in rec
    assert "source" in rec


def test_openai_tool_call_id_round_trips():
    """If an assistant message has a tool_call with id 'X', the matching
    tool response must carry tool_call_id 'X' — OpenAI rejects the
    payload otherwise. Previous bug: the assistant got id 'call_0' and
    the tool got id 'call_<name>', which didn't match."""
    from llm_client import _to_openai_messages

    messages = [
        {"role": "user", "text": "count rows"},
        {
            "role": "model",
            "tool_calls": [
                {"name": "run_sql", "arguments": {"query": "SELECT 1"}, "id": "tc_abc"}
            ],
        },
        {"role": "tool", "name": "run_sql", "tool_call_id": "tc_abc",
         "response": {"rows": [[1]]}},
    ]
    out = _to_openai_messages(messages, system_instruction="sys")

    asst = next(m for m in out if m["role"] == "assistant")
    tool = next(m for m in out if m["role"] == "tool")
    assert asst["tool_calls"][0]["id"] == tool["tool_call_id"] == "tc_abc"


def test_openai_tool_call_id_synthesized_when_missing():
    """Even with no id supplied upstream (e.g. tests that hand-build
    messages), the assistant and tool messages must agree on the id."""
    from llm_client import _to_openai_messages

    messages = [
        {"role": "user", "text": "count rows"},
        {"role": "model", "tool_calls": [{"name": "run_sql", "arguments": {}}]},
        {"role": "tool", "name": "run_sql", "response": {"rows": []}},
    ]
    out = _to_openai_messages(messages, system_instruction=None)
    asst = next(m for m in out if m["role"] == "assistant")
    tool = next(m for m in out if m["role"] == "tool")
    assert asst["tool_calls"][0]["id"] == tool["tool_call_id"]


def test_orchestrator_history_trim_starts_on_user():
    """When the trim slice lands on a `model` entry, the orchestrator
    must drop it — an orphaned assistant contract at the head confuses
    Gemini. Verifies the alternation guard added in the post-review
    cleanup."""
    from orchestrator import MAX_HISTORY_TURNS

    contract = {"sql": "SELECT 1 AS n",
                "chart": {"type": "kpi", "y": "n", "title": "x"},
                "explanation": "ok"}
    script = _Script(
        seen=[],
        responses=[LLMResponse(text=json.dumps(contract), tool_calls=[], raw=None)],
    )
    orch = Orchestrator(client=script, write_log=False)  # type: ignore[arg-type]

    # Build a history where the last MAX_HISTORY_TURNS entries start with
    # a `model` turn (which would happen naturally if MAX_HISTORY_TURNS
    # is even and there are an odd number of user/asst pairs prior).
    history: list[dict] = []
    # 13 user/asst pairs = 26 entries. history[-12:] starts on a `model`.
    for i in range(13):
        history.append({"role": "user", "text": f"q{i}"})
        history.append({
            "role": "assistant",
            "sql": f"SELECT {i}",
            "chart": {"type": "kpi", "y": "n"},
            "explanation": f"a{i}",
        })

    orch.run("latest", history=history)
    sent = script.seen[0]
    # The first non-system entry the model sees must be a user message.
    first_non_system = sent[0]
    assert first_non_system["role"] == "user", (
        f"first message should be user, got {first_non_system!r}"
    )


def test_gemini_preserves_thought_signature_on_round_trip():
    """Gemini 3 rejects a function-call round-trip whose Part is missing
    a thought_signature. When the orchestrator builds the next request,
    the assistant turn must echo the original Parts (with signatures)
    verbatim — not freshly-constructed Parts that strip them."""
    from google.genai import types

    from llm_client import LLMResponse, ToolCall, _to_gemini_contents

    signed_part = types.Part(
        function_call=types.FunctionCall(name="time_range", args={"days": 7}),
        thought_signature=b"opaque-bytes-from-model",
    )
    asst_msg = Orchestrator._assistant_message_from(
        LLMResponse(
            text=None,
            tool_calls=[ToolCall(name="time_range", arguments={"days": 7}, id="tc1")],
            raw=None,
            raw_parts=[signed_part],
        )
    )
    assert asst_msg["_gemini_parts"] == [signed_part]

    contents = _to_gemini_contents([
        {"role": "user", "text": "last week?"},
        asst_msg,
        {"role": "tool", "name": "time_range", "tool_call_id": "tc1",
         "response": {"start": "2024-01-01", "end": "2024-01-07"}},
    ])
    model_turn = next(c for c in contents if c.role == "model")
    assert model_turn.parts[0].thought_signature == b"opaque-bytes-from-model"
    assert model_turn.parts[0].function_call.name == "time_range"


def test_gemini_coalesces_parallel_tool_responses():
    """When the model emits N parallel function_calls in one turn,
    the next user Content must hold N function_response parts. Gemini
    rejects mismatched counts with INVALID_ARGUMENT."""
    from google.genai import types

    from llm_client import _to_gemini_contents

    contents = _to_gemini_contents([
        {"role": "user", "text": "stats by language and segment"},
        {"role": "model", "tool_calls": [
            {"name": "run_sql", "arguments": {"query": "SELECT 1"}, "id": "a"},
            {"name": "run_sql", "arguments": {"query": "SELECT 2"}, "id": "b"},
        ]},
        {"role": "tool", "name": "run_sql", "tool_call_id": "a",
         "response": {"rows": [[1]]}},
        {"role": "tool", "name": "run_sql", "tool_call_id": "b",
         "response": {"rows": [[2]]}},
    ])
    model_turn = next(c for c in contents if c.role == "model")
    tool_turn = contents[-1]
    assert tool_turn.role == "user"
    fc_count = sum(1 for p in model_turn.parts if p.function_call)
    fr_count = sum(1 for p in tool_turn.parts if p.function_response)
    assert fc_count == 2
    assert fr_count == 2


def test_gemini_reconstruct_when_no_raw_parts_present():
    """Test stubs (and the OpenAI provider's outputs) don't supply
    `_gemini_parts`. In that case we still need to round-trip a usable
    function_call Part, just without a signature."""
    from llm_client import _to_gemini_contents

    contents = _to_gemini_contents([
        {"role": "user", "text": "hi"},
        {"role": "model", "tool_calls": [{"name": "foo", "arguments": {"a": 1}}]},
        {"role": "tool", "name": "foo", "response": {"ok": True}},
    ])
    model_turn = next(c for c in contents if c.role == "model")
    assert model_turn.parts[0].function_call.name == "foo"
    assert model_turn.parts[0].thought_signature is None


def test_gemini_parse_skips_thought_summary_text():
    """A Part with thought=True is the model's internal monologue and
    must not be included in the response text — otherwise the
    orchestrator tries to parse the thought summary as the JSON
    contract."""
    from google.genai import types

    from llm_client import _parse_gemini_response

    response = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Let me think about this...", thought=True,
                                   thought_signature=b"sig"),
                        types.Part(text='{"sql": "SELECT 1", "chart": {}, "explanation": "ok"}'),
                    ],
                )
            )
        ]
    )
    text, tool_calls, raw_parts = _parse_gemini_response(response)
    assert tool_calls == []
    assert text and text.startswith('{"sql"')
    assert "Let me think" not in text
    assert len(raw_parts) == 2  # both preserved for round-trip


def test_orchestrator_threads_tool_call_id():
    """When the LLM emits a tool call with id 'X', the orchestrator must
    send the matching tool response back with tool_call_id 'X'."""
    from llm_client import ToolCall

    contract = {"sql": "SELECT 1 AS n",
                "chart": {"type": "kpi", "y": "n", "title": "x"},
                "explanation": "ok"}
    script = _Script(
        seen=[],
        responses=[
            # First turn: model wants to call run_sql with id 'xyz'.
            LLMResponse(
                text=None,
                tool_calls=[
                    ToolCall(name="run_sql", arguments={"query": "SELECT 1"}, id="xyz")
                ],
                raw=None,
            ),
            # Second turn: model returns the final contract.
            LLMResponse(text=json.dumps(contract), tool_calls=[], raw=None),
        ],
    )
    orch = Orchestrator(client=script, write_log=False)  # type: ignore[arg-type]
    orch.run("count")

    # The second LLM call must include a tool-role message with the
    # threaded id.
    second_round = script.seen[1]
    tool_msgs = [m for m in second_round if m.get("role") == "tool"]
    assert tool_msgs, "expected tool result in second round"
    assert tool_msgs[0]["tool_call_id"] == "xyz"
