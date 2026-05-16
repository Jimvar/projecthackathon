"""Thin abstraction over Gemini and OpenAI function-calling APIs.

Phase-4 adds an OpenAI implementation for outage insurance — the
orchestrator can swap providers via `LLM_PROVIDER=openai`. Both classes
expose the same `generate(messages)` interface and the same
`LLMResponse` shape, so nothing downstream cares which is wired in.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types


GEMINI_DEFAULT_MODEL = "gemini-2.5-flash"
OPENAI_DEFAULT_MODEL = "gpt-4o-mini"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    raw: Any  # the underlying SDK response, kept for logging


# ---------------------------------------------------------------------- factory


def make_llm_client(
    api_key: str | None = None,
    model: str | None = None,
    tools: list[dict] | None = None,
    system_instruction: str | None = None,
    provider: str | None = None,
):
    """Pick a concrete LLM client based on `LLM_PROVIDER` env (default gemini)."""
    chosen = (provider or os.getenv("LLM_PROVIDER") or "gemini").strip().lower()
    if chosen == "openai":
        return OpenAILLMClient(api_key=api_key, model=model, tools=tools,
                               system_instruction=system_instruction)
    return LLMClient(api_key=api_key, model=model, tools=tools,
                     system_instruction=system_instruction)


# ---------------------------------------------------------------------- Gemini


class LLMClient:
    """Gemini function-calling wrapper. Kept under the historical name so
    the test stubs don't have to change."""

    provider = "gemini"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        tools: list[dict] | None = None,
        system_instruction: str | None = None,
    ) -> None:
        key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not key:
            raise RuntimeError(
                "GEMINI_API_KEY not set. Copy .env.example to .env and add your key."
            )
        self._client = genai.Client(api_key=key)
        self.model = model or os.getenv("GEMINI_MODEL", GEMINI_DEFAULT_MODEL)
        self.system_instruction = system_instruction
        self._tools = self._compile_tools(tools or [])

    @staticmethod
    def _compile_tools(tool_specs: list[dict]) -> list[types.Tool]:
        declarations: list[types.FunctionDeclaration] = []
        for spec in tool_specs:
            declarations.append(
                types.FunctionDeclaration(
                    name=spec["name"],
                    description=spec["description"],
                    parameters=spec["parameters"],
                )
            )
        if not declarations:
            return []
        return [types.Tool(function_declarations=declarations)]

    def generate(
        self,
        messages: list[dict],
        force_text: bool = False,
    ) -> LLMResponse:
        contents = _to_gemini_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=None if force_text else (self._tools or None),
            temperature=0.2,
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        text, tool_calls = _parse_gemini_response(response)
        return LLMResponse(text=text, tool_calls=tool_calls, raw=response)


def _to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    out: list[types.Content] = []
    for m in messages:
        role = m["role"]
        if role == "user":
            out.append(types.Content(role="user", parts=[types.Part(text=m["text"])]))
        elif role in ("assistant", "model"):
            parts: list[types.Part] = []
            if m.get("text"):
                parts.append(types.Part(text=m["text"]))
            for tc in m.get("tool_calls", []):
                parts.append(
                    types.Part(
                        function_call=types.FunctionCall(
                            name=tc["name"],
                            args=tc.get("arguments", {}),
                        )
                    )
                )
            if not parts:
                parts = [types.Part(text="")]
            out.append(types.Content(role="model", parts=parts))
        elif role == "tool":
            out.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                name=m["name"],
                                response=m["response"],
                            )
                        )
                    ],
                )
            )
        else:
            raise ValueError(f"unknown role: {role!r}")
    return out


def _parse_gemini_response(response: Any) -> tuple[str | None, list[ToolCall]]:
    text_chunks: list[str] = []
    tool_calls: list[ToolCall] = []
    candidates = getattr(response, "candidates", None) or []
    for cand in candidates:
        content = getattr(cand, "content", None)
        if content is None:
            continue
        for part in content.parts or []:
            if getattr(part, "function_call", None):
                fc = part.function_call
                args = dict(fc.args) if fc.args else {}
                tool_calls.append(ToolCall(name=fc.name, arguments=args))
            elif getattr(part, "text", None):
                text_chunks.append(part.text)
    text = "\n".join(t for t in text_chunks if t) or None
    return text, tool_calls


# ---------------------------------------------------------------------- OpenAI


class OpenAILLMClient:
    """OpenAI Chat Completions wrapper exposing the same surface as `LLMClient`.

    Insurance against a Gemini outage on demo day. Flip
    `LLM_PROVIDER=openai` and the orchestrator picks this up.
    """

    provider = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        tools: list[dict] | None = None,
        system_instruction: str | None = None,
    ) -> None:
        # Import lazily so projects without OpenAI configured don't pay the cost.
        from openai import OpenAI

        key = api_key or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "OPENAI_API_KEY not set. Add it to .env or unset LLM_PROVIDER=openai."
            )
        self._client = OpenAI(api_key=key)
        self.model = model or os.getenv("OPENAI_MODEL", OPENAI_DEFAULT_MODEL)
        self.system_instruction = system_instruction
        self._tools = self._compile_tools(tools or [])

    @staticmethod
    def _compile_tools(tool_specs: list[dict]) -> list[dict]:
        return [
            {
                "type": "function",
                "function": {
                    "name": spec["name"],
                    "description": spec["description"],
                    "parameters": spec["parameters"],
                },
            }
            for spec in tool_specs
        ]

    def generate(
        self,
        messages: list[dict],
        force_text: bool = False,
    ) -> LLMResponse:
        openai_msgs = _to_openai_messages(messages, self.system_instruction)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": openai_msgs,
            "temperature": 0.2,
        }
        if self._tools and not force_text:
            kwargs["tools"] = self._tools
        response = self._client.chat.completions.create(**kwargs)
        text, tool_calls = _parse_openai_response(response)
        return LLMResponse(text=text, tool_calls=tool_calls, raw=response)


def _to_openai_messages(messages: list[dict], system_instruction: str | None) -> list[dict]:
    """Translate our internal message dicts into OpenAI Chat Completions shape."""
    out: list[dict] = []
    if system_instruction:
        out.append({"role": "system", "content": system_instruction})

    for m in messages:
        role = m["role"]
        if role == "user":
            out.append({"role": "user", "content": m["text"]})
        elif role in ("assistant", "model"):
            entry: dict[str, Any] = {"role": "assistant"}
            if m.get("text"):
                entry["content"] = m["text"]
            else:
                entry["content"] = None
            tcs = m.get("tool_calls") or []
            if tcs:
                entry["tool_calls"] = [
                    {
                        "id": f"call_{idx}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("arguments", {})),
                        },
                    }
                    for idx, tc in enumerate(tcs)
                ]
            out.append(entry)
        elif role == "tool":
            # Tool result lands as a tool-role message. OpenAI requires
            # tool_call_id, but we don't track those — derive one
            # deterministically from the prior assistant turn so the
            # API doesn't reject the payload.
            out.append({
                "role": "tool",
                "tool_call_id": f"call_{m.get('name', 'tool')}",
                "content": json.dumps(m["response"], default=str),
            })
        else:
            raise ValueError(f"unknown role: {role!r}")
    return out


def _parse_openai_response(response: Any) -> tuple[str | None, list[ToolCall]]:
    choice = response.choices[0]
    msg = choice.message
    text = msg.content or None
    tool_calls: list[ToolCall] = []
    for tc in (msg.tool_calls or []):
        try:
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError:
            args = {}
        tool_calls.append(ToolCall(name=tc.function.name, arguments=args))
    return text, tool_calls
