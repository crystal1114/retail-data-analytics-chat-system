#!/usr/bin/env python3
"""
backend/evals/run_eval.py

Standalone evaluation runner for the NL→SQL→Answer pipeline.

Usage
─────
    # Deterministic assertions only (fast, no extra API cost):
    python backend/evals/run_eval.py

    # With LLM-as-judge scoring (one extra API call per case):
    python backend/evals/run_eval.py --judge

    # Run a single category:
    python backend/evals/run_eval.py --category customer

    # Choose a specific golden file or output path:
    python backend/evals/run_eval.py --golden backend/evals/golden.json \\
                                     --output backend/evals/results.json

    # Use a different judge model:
    python backend/evals/run_eval.py --judge --judge-model gpt-4o

Output
──────
    Printed to stdout and written to backend/evals/results.json.

    PASS  total_revenue     [sql:OK answer:OK intent:OK viz:OK] judge: 4.8/5.0
    FAIL  monthly_trend     [sql:OK answer:FAIL viz:FAIL]
      - answer missing: "2024-01"
      - viz_type: expected line_chart, got bar_chart
    ...
    ──────────────────────────────
    Eval Results: 32/35 passed (91.4%)
    Judge overall: 4.2 / 5.0 avg across 35 cases
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Ensure repo root is on sys.path ──────────────────────────────────────────
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from backend.app.chat_service import run_chat
from backend.evals.judge import judge_response
from backend.evals.seed import build_db

_GOLDEN_PATH = Path(__file__).parent / "golden.json"
_RESULTS_PATH = Path(__file__).parent / "results.json"


# ── Assertion checker ─────────────────────────────────────────────────────────

def _value_in_results(tool_results: list[dict], expected: float) -> bool:
    """Return True if expected numeric value appears in any SQL result rows."""
    for tr in tool_results:
        rows = tr.get("result", {}).get("rows", [])
        for row in rows:
            cells = row if isinstance(row, (list, tuple)) else [row]
            for cell in cells:
                if isinstance(cell, (int, float)):
                    if math.isclose(float(cell), float(expected), rel_tol=1e-3, abs_tol=0.01):
                        return True
                elif isinstance(cell, str):
                    try:
                        if math.isclose(float(cell), float(expected), rel_tol=1e-3, abs_tol=0.01):
                            return True
                    except ValueError:
                        pass
    return False


def check_assertions(
    result: dict[str, Any],
    assertions: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Evaluate all assertions against a run_chat() result.

    Returns a list of check dicts, each with at least:
      {"name": str, "pass": bool, ...detail fields}
    """
    tool_results = result.get("tool_results", [])
    reply = result.get("reply", "")
    structured = result.get("structured") or {}
    metadata = result.get("metadata", {})
    checks: list[dict[str, Any]] = []

    for key, expected in assertions.items():
        if key == "sql_executes":
            ok = any(tr.get("result", {}).get("ok") for tr in tool_results)
            checks.append({"name": "sql_executes", "pass": ok == expected,
                           "expected": expected, "got": ok})

        elif key == "result_contains_value":
            found = _value_in_results(tool_results, expected)
            checks.append({"name": "result_contains_value", "pass": found,
                           "expected": expected})

        elif key == "answer_contains":
            missing = [s for s in expected if s.lower() not in reply.lower()]
            checks.append({"name": "answer_contains", "pass": not missing,
                           "missing": missing})

        elif key == "answer_excludes":
            found_bad = [s for s in expected if s.lower() in reply.lower()]
            checks.append({"name": "answer_excludes", "pass": not found_bad,
                           "found": found_bad})

        elif key == "intent":
            got = structured.get("intent", "")
            checks.append({"name": "intent", "pass": got == expected,
                           "expected": expected, "got": got})

        elif key == "viz_type":
            got = structured.get("viz_type", "")
            checks.append({"name": "viz_type", "pass": got == expected,
                           "expected": expected, "got": got})

        elif key == "no_error":
            has_error = bool(metadata.get("error"))
            checks.append({"name": "no_error", "pass": (not has_error) == expected})

        elif key == "min_result_rows":
            max_rows = max(
                (len(tr.get("result", {}).get("rows", [])) for tr in tool_results),
                default=0,
            )
            checks.append({"name": "min_result_rows", "pass": max_rows >= expected,
                           "expected": expected, "got": max_rows})

    return checks


# ── Reporting helpers ─────────────────────────────────────────────────────────

def _check_summary(checks: list[dict]) -> str:
    """Build a compact tag string like [sql:OK answer:FAIL intent:OK]."""
    name_map = {
        "sql_executes": "sql",
        "result_contains_value": "value",
        "answer_contains": "answer",
        "answer_excludes": "no-halluc",
        "intent": "intent",
        "viz_type": "viz",
        "no_error": "err",
        "min_result_rows": "rows",
    }
    tags = []
    for c in checks:
        label = name_map.get(c["name"], c["name"])
        status = "OK" if c["pass"] else "FAIL"
        tags.append(f"{label}:{status}")
    return "[" + " ".join(tags) + "]"


def _failure_lines(checks: list[dict]) -> list[str]:
    """Return human-readable failure descriptions for failed checks."""
    lines = []
    for c in checks:
        if c["pass"]:
            continue
        name = c["name"]
        if name == "answer_contains":
            lines.append(f"  - answer missing: {c.get('missing')}")
        elif name == "answer_excludes":
            lines.append(f"  - answer should not contain: {c.get('found')}")
        elif name in ("intent", "viz_type", "sql_executes", "min_result_rows"):
            lines.append(f"  - {name}: expected {c.get('expected')!r}, got {c.get('got')!r}")
        elif name == "result_contains_value":
            lines.append(f"  - result missing expected value: {c.get('expected')}")
        else:
            lines.append(f"  - {name} failed")
    return lines


# ── Main eval loop ────────────────────────────────────────────────────────────

def run_eval(
    golden_path: Path = _GOLDEN_PATH,
    results_path: Path = _RESULTS_PATH,
    category_filter: str | None = None,
    use_judge: bool = False,
    judge_model: str = "gpt-4o-mini",
) -> dict[str, Any]:
    """
    Run the full evaluation suite and return a summary dict.
    Also prints a human-readable report to stdout.
    """
    with open(golden_path) as f:
        raw_cases = json.load(f)

    # Strip comment-only objects
    cases = [c for c in raw_cases if "id" in c]
    if category_filter:
        cases = [c for c in cases if c.get("category") == category_filter]

    conn = build_db()
    results: list[dict[str, Any]] = []
    total_pass = 0
    judge_scores_all: list[float] = []

    print(f"\nRunning {len(cases)} evaluation case(s)"
          + (f" [category={category_filter}]" if category_filter else "")
          + (" + LLM judge" if use_judge else "")
          + "\n")

    for case in cases:
        case_id = case["id"]
        messages = case.get("messages", [{"role": "user", "content": case.get("question", "")}])
        assertions = case.get("assertions", {})
        reference_answer = case.get("reference_answer")
        question = case.get("question", messages[-1].get("content", ""))

        # ── Run pipeline ──────────────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            result = run_chat(messages=messages, conn=conn)
        except Exception as exc:
            result = {
                "reply": "",
                "structured": None,
                "tool_results": [],
                "metadata": {"error": str(exc)},
            }
        elapsed = round(time.monotonic() - t0, 2)

        # ── Deterministic assertions ──────────────────────────────────────────
        checks = check_assertions(result, assertions)
        case_pass = all(c["pass"] for c in checks)
        if case_pass:
            total_pass += 1

        tag_str = _check_summary(checks)
        status_label = "PASS" if case_pass else "FAIL"
        line = f"{status_label:<4}  {case_id:<35} {tag_str}  ({elapsed}s)"

        # ── LLM judge (optional) ──────────────────────────────────────────────
        judge_result: dict[str, Any] | None = None
        if use_judge:
            judge_result = judge_response(
                question=question,
                tool_results=result.get("tool_results", []),
                reply=result.get("reply", ""),
                reference_answer=reference_answer,
                model=judge_model,
            )
            if judge_result["error"]:
                line += f"  judge:ERROR({judge_result['error'][:40]})"
            else:
                score = judge_result["overall"]
                judge_scores_all.append(score)
                line += f"  judge:{score:.1f}/5.0"

        print(line)
        for fail_line in _failure_lines(checks):
            print(fail_line)

        # ── Collect case result ───────────────────────────────────────────────
        results.append({
            "id": case_id,
            "category": case.get("category", ""),
            "question": question,
            "pass": case_pass,
            "elapsed_s": elapsed,
            "checks": checks,
            "reply": result.get("reply", ""),
            "structured": result.get("structured"),
            "metadata": result.get("metadata", {}),
            "judge_scores": judge_result,
        })

    conn.close()

    # ── Summary ───────────────────────────────────────────────────────────────
    total = len(cases)
    pct = round(100 * total_pass / total, 1) if total else 0
    judge_avg = round(sum(judge_scores_all) / len(judge_scores_all), 2) if judge_scores_all else None

    print(f"\n{'─' * 60}")
    print(f"Eval Results: {total_pass}/{total} passed ({pct}%)")
    if judge_avg is not None:
        print(f"Judge overall: {judge_avg} / 5.0 (avg across {len(judge_scores_all)} scored cases)")

    # Per-category breakdown
    categories: dict[str, list[bool]] = {}
    for r in results:
        cat = r["category"] or "uncategorised"
        categories.setdefault(cat, []).append(r["pass"])
    if len(categories) > 1:
        print("\nBy category:")
        for cat, passed_list in sorted(categories.items()):
            n = len(passed_list)
            p = sum(passed_list)
            print(f"  {cat:<20} {p}/{n}")

    # ── Write results.json ────────────────────────────────────────────────────
    output = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "total": total,
        "passed": total_pass,
        "pass_rate": pct,
        "judge_model": judge_model if use_judge else None,
        "overall_judge_avg": judge_avg,
        "cases": results,
    }
    results_path.write_text(json.dumps(output, indent=2))
    print(f"\nResults written to {results_path}")

    return output


# ── CLI entry point ───────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run model response evaluation against golden.json"
    )
    parser.add_argument(
        "--golden",
        type=Path,
        default=_GOLDEN_PATH,
        help="Path to the golden eval cases JSON file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=_RESULTS_PATH,
        help="Path to write results JSON",
    )
    parser.add_argument(
        "--category",
        type=str,
        default=None,
        help="Filter to a single category (e.g. customer, product, kpi, trend)",
    )
    parser.add_argument(
        "--judge",
        action="store_true",
        default=False,
        help="Enable LLM-as-judge scoring (costs one extra API call per case)",
    )
    parser.add_argument(
        "--judge-model",
        type=str,
        default="gpt-4o-mini",
        help="OpenAI model to use for judging (default: gpt-4o-mini)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    summary = run_eval(
        golden_path=args.golden,
        results_path=args.output,
        category_filter=args.category,
        use_judge=args.judge,
        judge_model=args.judge_model,
    )
    sys.exit(0 if summary["passed"] == summary["total"] else 1)
