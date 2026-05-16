"""Thin abstraction over Gemini's function-calling API.

Keeps every Gemini-specific call inside one file so a future swap to
OpenAI/Anthropic is a contained change.

Phase-1 surface area is intentionally minimal: build a tool list once,
hand it a list of (role, text) messages, get back either a tool-call or
a final text response.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types


DEFAULT_MODEL = "gemini-2.5-flash"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    text: str | None
    tool_calls: list[ToolCall]
    raw: Any  # the underlying SDK response, kept for logging


class LLMClient:
    """Wraps a Gemini client + persistent tool list."""

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
        self.model = model or os.getenv("GEMINI_MODEL", DEFAULT_MODEL)
        self.system_instruction = system_instruction
        self._tools = self._compile_tools(tools or [])

    # ------------------------------------------------------------------ schemas

    @staticmethod
    def _compile_tools(tool_specs: list[dict]) -> list[types.Tool]:
        """Convert our `{name, description, parameters}` specs into Gemini Tools."""
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

    # ------------------------------------------------------------------ chat

    def generate(
        self,
        messages: list[dict],
        force_text: bool = False,
    ) -> LLMResponse:
        """Send a turn.

        `messages` is our internal shape: a list of
            {"role": "user"|"model"|"tool", "text": "...", ...}

        Tool results are passed as role='tool' with a 'name' and 'response'.
        """
        contents = _to_gemini_contents(messages)
        config = types.GenerateContentConfig(
            system_instruction=self.system_instruction,
            tools=None if force_text else (self._tools or None),
            temperature=0.2,
            # If tools are present and we still want to allow free text, leave
            # tool_config unset so the model picks. force_text disables tools.
        )
        response = self._client.models.generate_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        text, tool_calls = _parse_response(response)
        return LLMResponse(text=text, tool_calls=tool_calls, raw=response)


# ---------------------------------------------------------------------- mapping


def _to_gemini_contents(messages: list[dict]) -> list[types.Content]:
    """Translate our message dicts into Gemini's `Content` objects."""
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


def _parse_response(response: Any) -> tuple[str | None, list[ToolCall]]:
    """Pull text + function_call parts out of a Gemini response."""
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
