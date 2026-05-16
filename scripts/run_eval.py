"""Run the full eval suite against Gemini and print a pass/fail grid.

Loads `eval/questions.yaml`, runs each question through the orchestrator,
and tags it pass / partial / fail based on lightweight heuristics:

  * pass   — model returned non-empty SQL, a chart_spec with a known type,
             a non-empty explanation, and the SQL executed without error.
  * partial— it ran but is missing one of the above (e.g. no chart spec,
             or SQL came back empty rows).
  * fail   — orchestrator error, model didn't return a JSON contract, or
             the SQL was rejected by the safety layer.

Optionally pass `--ids q01_pie_languages q02_health_this_week` to limit
to specific questions, or `--shape ranking` to filter by shape.

Usage:
    uv run python scripts/run_eval.py
    uv run python scripts/run_eval.py --shape distribution
    uv run python scripts/run_eval.py --ids q01_pie_languages q11_greek_aht_by_region
    uv run python scripts/run_eval.py --out logs/eval_run.jsonl
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from eval_grading import tag_turn  # noqa: E402

QUESTIONS_PATH = ROOT / "eval" / "questions.yaml"

STATUS_GLYPH = {"pass": "OK  ", "partial": "PART", "fail": "FAIL"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the eval suite against Gemini.")
    parser.add_argument("--ids", nargs="*", help="Only run questions with these IDs.")
    parser.add_argument("--shape", help="Filter by shape (distribution/trend/...).")
    parser.add_argument("--language", help="Filter by language (en/el).")
    parser.add_argument(
        "--out",
        type=Path,
        default=ROOT / "logs" / "eval_latest.jsonl",
        help="Where to write the per-question JSONL log.",
    )
    args = parser.parse_args()

    load_dotenv()
    if not os.getenv("GEMINI_API_KEY") and not os.getenv("GOOGLE_API_KEY"):
        print("GEMINI_API_KEY not set. Copy .env.example to .env and add your key.")
        return 2

    from orchestrator import Orchestrator  # noqa: E402

    questions = yaml.safe_load(QUESTIONS_PATH.read_text())
    if args.ids:
        questions = [q for q in questions if q["id"] in set(args.ids)]
    if args.shape:
        questions = [q for q in questions if q.get("shape") == args.shape]
    if args.language:
        questions = [q for q in questions if q.get("language") == args.language]

    if not questions:
        print("no questions matched the filter")
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    orch = Orchestrator()
    print(f"Running {len(questions)} questions against {orch.client.model}\n")

    counts = {"pass": 0, "partial": 0, "fail": 0}
    by_shape: dict[str, dict[str, int]] = {}
    started = datetime.utcnow()

    # Cache prior turn logs by id so follow-up questions can see them.
    prior_turn: dict[str, object] = {}

    with args.out.open("w") as f:
        for i, q in enumerate(questions, 1):
            qid = q["id"]
            text = q["q"]
            shape = q.get("shape", "?")
            lang = q.get("language", "?")

            # Wire follow-up history if this question references a prior id.
            history = None
            parent_id = q.get("followup_to")
            if parent_id:
                parent = prior_turn.get(parent_id)
                if parent is not None:
                    parent_q = next(
                        (pq["q"] for pq in questions if pq["id"] == parent_id),
                        "",
                    )
                    history = [
                        {"role": "user", "text": parent_q},
                        {
                            "role": "assistant",
                            "sql": parent.sql,
                            "chart": parent.chart_spec or {},
                            "explanation": parent.explanation,
                        },
                    ]

            try:
                turnlog = orch.run(text, history=history)
                status, reason = tag_turn(turnlog)
                prior_turn[qid] = turnlog
            except Exception as e:
                turnlog = None
                status, reason = "fail", f"exception: {e}"

            counts[status] += 1
            by_shape.setdefault(shape, {"pass": 0, "partial": 0, "fail": 0})
            by_shape[shape][status] += 1

            chart_type = (
                (turnlog.chart_spec or {}).get("type") if turnlog else None
            ) or "-"
            latency = turnlog.latency_ms if turnlog else 0
            print(
                f"{STATUS_GLYPH[status]} [{i:2d}/{len(questions)}] "
                f"{qid:35s} {shape:13s} {lang} {chart_type:8s} "
                f"{latency:>5d} ms  — {reason}"
            )

            f.write(json.dumps({
                "id": qid,
                "q": text,
                "shape": shape,
                "language": lang,
                "status": status,
                "reason": reason,
                "sql": turnlog.sql if turnlog else "",
                "chart": turnlog.chart_spec if turnlog else None,
                "explanation": turnlog.explanation if turnlog else "",
                "latency_ms": latency,
            }, default=str) + "\n")

    elapsed = (datetime.utcnow() - started).total_seconds()
    total = sum(counts.values())
    print()
    print(
        f"Total: {total} | pass {counts['pass']} | partial {counts['partial']} "
        f"| fail {counts['fail']} | {elapsed:.1f}s"
    )
    print()
    print("By shape:")
    for shape, c in sorted(by_shape.items()):
        t = sum(c.values())
        print(
            f"  {shape:13s} pass {c['pass']:>2d}/{t:<2d}  "
            f"partial {c['partial']:>2d}  fail {c['fail']:>2d}"
        )
    print()
    print(f"Detailed log: {args.out}")

    return 0 if counts["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
