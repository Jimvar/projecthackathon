"""Per-turn JSONL logger.

Every Orchestrator.run() invocation appends one record to a daily file
in ``logs/``. The schema is intentionally flat so a judge or an eval
script can grep it without any tooling.

Schema (one JSON object per line):

    {
      "ts":           "2026-05-16T01:23:45.678Z",
      "source":       "duckdb" | "jsonl",
      "user_message": "...",
      "sql":          "...",
      "chart_spec":   {...},
      "explanation":  "...",
      "error":        "",
      "latency_ms":   1234,
      "tool_calls":   [{"name": "...", "arguments": {...}, "result_keys": {...}}, ...]
    }
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

LOG_DIR = Path(__file__).parent / "logs"


def log_path_for(now: datetime | None = None) -> Path:
    """Return the JSONL file the next record will land in."""
    now = now or datetime.now(timezone.utc)
    return LOG_DIR / f"turns-{now.strftime('%Y-%m-%d')}.jsonl"


def log_turn(record: dict[str, Any]) -> Path:
    """Append `record` to today's JSONL log.

    Errors writing the log are intentionally swallowed and printed; we
    don't want a disk-full to take down the orchestrator.
    """
    record = dict(record)
    record.setdefault("ts", datetime.now(timezone.utc).isoformat())
    path = log_path_for()
    try:
        LOG_DIR.mkdir(exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError as e:  # pragma: no cover - best-effort logging
        print(f"[turn_log] could not write log: {e}")
    return path
