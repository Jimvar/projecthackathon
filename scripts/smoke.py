"""End-to-end smoke against Gemini.

Requires `GEMINI_API_KEY` to be set (or in a .env file). Runs a handful
of representative questions through the orchestrator and prints a
pass/fail grid. Useful as the "is it actually working" check before
demos.

Usage:
    uv run python scripts/smoke.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Allow running as `python scripts/smoke.py`.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv()

if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
    print("GEMINI_API_KEY not set. Copy .env.example to .env and add your key.")
    sys.exit(2)

from orchestrator import Orchestrator  # noqa: E402

SMOKE_QUESTIONS = [
    "Show me a pie chart of Greek vs English users.",
    "Break down conversations by outcome.",
    "Top 5 regions by call volume, sorted descending.",
    "What's our containment rate?",
    "Δείξε μου τον μέσο χρόνο κλήσης ανά περιοχή.",
]


def main() -> int:
    orch = Orchestrator()
    failures = 0
    print(f"Smoke-running {len(SMOKE_QUESTIONS)} questions against {orch.client.model}...\n")
    for i, q in enumerate(SMOKE_QUESTIONS, 1):
        log = orch.run(q)
        ok = bool(log.sql and log.chart_spec and not log.error)
        marker = "OK  " if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"{marker} [{i}/{len(SMOKE_QUESTIONS)}] ({log.latency_ms} ms)  {q}")
        if log.error:
            print(f"      error: {log.error}")
        if log.sql:
            print(f"      sql:   {log.sql[:100]}{'…' if len(log.sql) > 100 else ''}")
        if log.chart_spec:
            print(f"      chart: type={log.chart_spec.get('type')!r} title={log.chart_spec.get('title')!r}")
        if log.explanation:
            print(f"      reply: {log.explanation[:120]}{'…' if len(log.explanation) > 120 else ''}")
        print()
    print(f"\n{len(SMOKE_QUESTIONS) - failures}/{len(SMOKE_QUESTIONS)} passed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
