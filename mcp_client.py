"""Sync wrapper around an MCP stdio session.

Spawns `mcp_server.py` as a subprocess and exposes a `call_tool` method
with the same shape as `mcp_tools.call_tool`, so the orchestrator can
swap one for the other via dependency injection. The Streamlit app
holds a process-wide singleton under `@st.cache_resource` — the
subprocess stays alive for the lifetime of the app.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import sys
import threading
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

SERVER_PATH = Path(__file__).parent / "mcp_server.py"

# FastMCP names tools after the wrapper function. Every tool in
# mcp_server.py is declared as `def t_<name>(...)`, so the client adds
# the prefix transparently — callers stay on the bare names that the
# in-process TOOL_REGISTRY uses.
_TOOL_PREFIX = "t_"


class _AsyncRunner:
    """Background thread running an asyncio loop. Lets synchronous
    Streamlit code submit coroutines via `run_coroutine_threadsafe`
    and block on their result."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(
            target=self._serve, name="mcp-asyncio-loop", daemon=True
        )
        self._thread.start()
        self._ready.wait()

    def _serve(self) -> None:
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def run(self, coro, timeout: float = 120.0):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)

    def stop(self) -> None:
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self._thread.join(timeout=2)
        except Exception:
            pass


class MCPClient:
    """Long-lived MCP stdio session against a spawned mcp_server.py.

    `call_tool` is synchronous — it dispatches to the background loop
    and blocks until the result is ready. Return values are unwrapped
    into the same dict shape `mcp_tools.call_tool` produces, so the
    orchestrator can use either interchangeably.
    """

    def __init__(self, server_path: Path | None = None, *, init_timeout: float = 30.0) -> None:
        self._runner = _AsyncRunner()
        self._session: ClientSession | None = None
        self._stdio_cm = None
        self._session_cm = None
        self._server_path = server_path or SERVER_PATH
        try:
            self._runner.run(self._setup(), timeout=init_timeout)
        except Exception:
            self._runner.stop()
            raise
        atexit.register(self.close)

    async def _setup(self) -> None:
        params = StdioServerParameters(
            command=sys.executable,
            args=[str(self._server_path)],
            env=os.environ.copy(),
        )
        self._stdio_cm = stdio_client(params)
        read, write = await self._stdio_cm.__aenter__()
        self._session_cm = ClientSession(read, write)
        self._session = await self._session_cm.__aenter__()
        await self._session.initialize()

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        if self._session is None:
            return {"error": "MCP session not initialised"}
        tool_name = name if name.startswith(_TOOL_PREFIX) else f"{_TOOL_PREFIX}{name}"
        try:
            return self._runner.run(self._call(tool_name, arguments or {}))
        except Exception as e:
            return {"error": f"MCP call failed: {e}"}

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self._session.call_tool(tool_name, arguments=arguments)
        # FastMCP returns the tool's JSON-serialised dict as a TextContent
        # part. Parse the first text part; downstream callers expect a dict.
        out: dict[str, Any] = {}
        for content in (result.content or []):
            text = getattr(content, "text", None)
            if text is None:
                continue
            try:
                parsed = json.loads(text)
            except (TypeError, json.JSONDecodeError):
                out = {"text": text}
                break
            out = parsed if isinstance(parsed, dict) else {"value": parsed}
            break
        if result.isError and "error" not in out:
            out["error"] = "MCP tool returned error"
        return out

    def close(self) -> None:
        if self._session is None:
            return

        async def _teardown():
            for cm in (self._session_cm, self._stdio_cm):
                if cm is None:
                    continue
                try:
                    await cm.__aexit__(None, None, None)
                except Exception:
                    pass

        try:
            self._runner.run(_teardown(), timeout=5)
        except Exception:
            pass
        self._session = None
        self._runner.stop()


_CLIENT: MCPClient | None = None
_CLIENT_LOCK = threading.Lock()


def get_mcp_client() -> MCPClient:
    """Process-wide singleton, lazily created on first call."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = MCPClient()
    return _CLIENT


def reset_mcp_client() -> None:
    """Tear down the singleton (used by tests). Safe to call repeatedly."""
    global _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is not None:
            _CLIENT.close()
            _CLIENT = None
