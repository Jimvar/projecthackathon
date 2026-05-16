"""LLM tool-use loop.

Stitches together:

* The system prompt (loaded from `prompts/system.md`),
* The MCP tools (called in-process via `mcp_tools.call_tool`),
* Conversation memory (caller-supplied list of past turns),
* The JSON output contract,
* Per-turn JSONL logging.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llm_client import LLMClient, make_llm_client
from mcp_tools import TOOL_REGISTRY, call_tool
from turn_log import log_turn

SYSTEM_PROMPT_PATH = Path(__file__).parent / "prompts" / "system.md"


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


# Tunables — env-overridable so a long demo session or a deeper-tool-use
# debug run doesn't require code changes.
MAX_TOOL_HOPS = _env_int("NR2_MAX_TOOL_HOPS", 6)            # safety stop for the tool-use loop
MAX_HISTORY_TURNS = _env_int("NR2_MAX_HISTORY_TURNS", 12)   # last N history entries forwarded to the LLM


# Keywords that should trigger auto-injection of the relevant metric definition.
METRIC_TRIGGERS = {
    "containment": "containment rate",
    "csat": "CSAT",
    "satisfaction": "CSAT",
    "aht": "average handle time",
    "handle time": "average handle time",
    "escalation": "escalation rate",
    "escalated": "escalation rate",
    "abandon": "abandonment rate",
    "deflection": "deflection rate",
    "cost per call": "cost per call",
    "cost per resolved": "cost per resolved call",
    "tool success": "tool success rate",
    # Greek terms
    "ικανοποίηση": "CSAT",
    "csat ": "CSAT",
    "ολοκλήρωσ": "containment rate",
    "μεταβίβασ": "escalation rate",
    "εγκατάλειψ": "abandonment rate",
}


@dataclass
class TurnLog:
    user_message: str
    sql: str = ""
    chart_spec: dict | None = None
    explanation: str = ""
    error: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    latency_ms: int = 0


def _system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text()


def _tool_specs_for_gemini() -> list[dict]:
    """Hand the LLM the same six tools that mcp_server.py exposes."""
    return [
        {"name": name, "description": meta["description"], "parameters": meta["parameters"]}
        for name, meta in TOOL_REGISTRY.items()
    ]


def _auto_inject_metric_hint(user_message: str) -> str | None:
    """If the user mentioned a known metric name, return the dictionary chunk
    to prepend as a hidden system note. Saves the LLM a tool round-trip.

    Note on the no-hardcoded-lookups rule:
    This is **not** a banned NL→answer dispatch. The user's question still
    flows to the LLM unchanged; we only attach the relevant page of the
    metrics dictionary to the prompt. The LLM still chooses what SQL to
    write and which chart to produce. Compare to the example repo's
    `hardcoded_dispatch`, which mapped substrings directly to predetermined
    SQL strings — that *would* be banned.
    """
    text = user_message.lower()
    for trigger, metric in METRIC_TRIGGERS.items():
        if trigger in text:
            res = call_tool("get_metric_definition", {"name": metric})
            if isinstance(res, dict) and res.get("text"):
                return f"(metric reference for '{metric}'):\n{res['text']}"
    return None


# ---------------------------------------------------------------------- main entry


class Orchestrator:
    """One-shot question runner.

    Holds an `LLMClient` configured with the project's system prompt + tools.
    """

    def __init__(self, client: LLMClient | None = None, *, write_log: bool = True) -> None:
        # The factory picks gemini vs openai from LLM_PROVIDER env var.
        # Tests pass an explicit stub client; production passes nothing.
        self.client = client or make_llm_client(
            tools=_tool_specs_for_gemini(),
            system_instruction=_system_prompt(),
        )
        self.write_log = write_log

    # ------------------------------------------------------------------ turn

    def run(self, user_message: str, history: list[dict] | None = None) -> TurnLog:
        """Run a single user turn end-to-end.

        `history` is a list of prior {"role": "user"|"model", "text": ...,
        "tool_calls": [...]?, "sql": ..., "chart": ..., "explanation": ...}
        dicts. The orchestrator picks the bits Gemini needs.
        """
        t_start = time.monotonic()
        log = TurnLog(user_message=user_message)

        messages = self._build_messages(user_message, history or [])

        for hop in range(MAX_TOOL_HOPS):
            resp = self.client.generate(messages)
            messages.append(self._assistant_message_from(resp))

            if not resp.tool_calls:
                # Final answer — parse the JSON contract.
                parsed = _parse_contract(resp.text or "")
                if parsed is None:
                    log.error = "model did not return a valid JSON contract"
                    log.explanation = (resp.text or "").strip()[:500]
                else:
                    log.sql = parsed.get("sql", "")
                    log.chart_spec = parsed.get("chart", {}) or {}
                    log.explanation = parsed.get("explanation", "")
                break

            for tc in resp.tool_calls:
                result = call_tool(tc.name, tc.arguments)
                log.tool_calls.append(
                    {"name": tc.name, "arguments": tc.arguments, "result_keys": _keys_only(result)}
                )
                # Thread the tool_call id through so the OpenAI provider's
                # tool_call_id can match its prior assistant tool_calls[*].id.
                # Gemini ignores ids; the round-trip is harmless there.
                messages.append({
                    "role": "tool",
                    "name": tc.name,
                    "tool_call_id": tc.id,
                    "response": result,
                })
                if tc.name == "run_sql" and isinstance(result, dict) and not result.get("error"):
                    log.sql = result.get("sql", "")
        else:
            log.error = f"tool-use loop exceeded {MAX_TOOL_HOPS} hops"

        log.latency_ms = int((time.monotonic() - t_start) * 1000)

        if self.write_log:
            try:
                from db import get_db  # local import — avoids circular at module load
                source = get_db().source
            except Exception:
                source = "unknown"
            log_turn({
                "source": source,
                "user_message": user_message,
                "sql": log.sql,
                "chart_spec": log.chart_spec,
                "explanation": log.explanation,
                "error": log.error,
                "latency_ms": log.latency_ms,
                "tool_calls": log.tool_calls,
            })

        return log

    # ------------------------------------------------------------------ message building

    def _build_messages(self, user_message: str, history: list[dict]) -> list[dict]:
        msgs: list[dict] = []
        # Trim history to the last MAX_HISTORY_TURNS entries (keeps Gemini's
        # context bounded over long sessions while still letting it reuse the
        # most recent SQL / chart spec when the user says "now break that down…").
        # The trim must start on a "user" entry — an orphaned assistant
        # contract at the head confuses Gemini, which then sometimes treats
        # the next user turn as a fresh question and re-does work.
        recent = history[-MAX_HISTORY_TURNS:] if history else []
        while recent and recent[0].get("role") != "user":
            recent = recent[1:]
        for h in recent:
            role = h.get("role")
            if role == "user":
                msgs.append({"role": "user", "text": h["text"]})
            elif role in ("assistant", "model"):
                # Replay the final contract so the model can see the last
                # SQL it ran and the chart it produced. Follow-up questions
                # like "show that as a line" or "now by language" only work
                # if the prior contract is in the context.
                contract = {
                    "sql": h.get("sql", ""),
                    "chart": h.get("chart", {}),
                    "explanation": h.get("explanation", ""),
                }
                msgs.append({"role": "model", "text": json.dumps(contract)})
        hint = _auto_inject_metric_hint(user_message)
        text = user_message
        if hint:
            text = f"{user_message}\n\n[system note] {hint}"
        msgs.append({"role": "user", "text": text})
        return msgs

    @staticmethod
    def _assistant_message_from(resp) -> dict:
        msg: dict[str, Any] = {"role": "model"}
        if resp.text:
            msg["text"] = resp.text
        if resp.tool_calls:
            msg["tool_calls"] = [
                {"name": tc.name, "arguments": tc.arguments, "id": tc.id}
                for tc in resp.tool_calls
            ]
        return msg


# ---------------------------------------------------------------------- helpers


_JSON_FENCE = re.compile(r"```(?:json|jsonc)?\s*(\{.*?\})\s*```", re.DOTALL)


def _parse_contract(text: str) -> dict | None:
    """Pull the JSON contract out of the model's final message.

    The system prompt tells the model to emit raw JSON, but in practice it
    sometimes wraps it in a ```json fence; tolerate that.
    """
    text = (text or "").strip()
    if not text:
        return None
    # Try fenced first.
    m = _JSON_FENCE.search(text)
    candidate = m.group(1) if m else text
    candidate = candidate.strip()
    # Heuristic: find the outermost {...}
    if not candidate.startswith("{"):
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end == -1:
            return None
        candidate = candidate[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None


def _keys_only(result: Any) -> Any:
    """Compact representation of a tool result for logging."""
    if isinstance(result, dict):
        out = {}
        for k, v in result.items():
            if k == "rows" and isinstance(v, list):
                out[k] = f"<{len(v)} rows>"
            elif isinstance(v, (dict, list)):
                out[k] = f"<{type(v).__name__} len={len(v)}>"
            else:
                out[k] = v
        return out
    return result
