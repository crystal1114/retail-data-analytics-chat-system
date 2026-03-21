"""
backend/evals/test_eval.py

Pytest wrapper for the evaluation suite.

Each golden case becomes a separate pytest item, making failures easy to
spot in CI output and enabling --lf / -k filtering.

Running
───────
    # All eval cases (requires OPENAI_API_KEY):
    pytest -m eval -v

    # Deterministic assertions only (skips judge):
    pytest -m eval -v

    # With LLM-as-judge (pass env var to activate):
    EVAL_JUDGE=1 pytest -m eval -v

    # Single category:
    pytest -m eval -k "customer" -v

    # One specific case:
    pytest -m eval -k "total_revenue" -v

Environment
───────────
    OPENAI_API_KEY   Required — eval cases are skipped if absent or placeholder.
    EVAL_JUDGE       Optional — set to "1" to enable judge scoring per case.
    EVAL_JUDGE_MODEL Optional — judge model override (default: gpt-4o-mini).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# ── Fixtures ──────────────────────────────────────────────────────────────────

_GOLDEN_PATH = Path(__file__).parent / "golden.json"


def _load_cases() -> list[dict]:
    with open(_GOLDEN_PATH) as f:
        raw = json.load(f)
    return [c for c in raw if "id" in c]


@pytest.fixture(scope="module")
def eval_db():
    """Shared in-memory seed database for all eval tests in this module."""
    from backend.evals.seed import build_db
    conn = build_db()
    yield conn
    conn.close()


@pytest.fixture(autouse=True, scope="module")
def require_openai_key():
    """Skip the whole module when no real API key is present."""
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key in ("sk-...", "sk-placeholder") or key.startswith("sk-placeholder"):
        pytest.skip("OPENAI_API_KEY not configured — skipping eval tests")


# ── Parametrized test ─────────────────────────────────────────────────────────

_CASES = _load_cases()


@pytest.mark.eval
@pytest.mark.parametrize(
    "case",
    _CASES,
    ids=[c["id"] for c in _CASES],
)
def test_eval_case(case: dict, eval_db) -> None:
    """
    Run a single golden eval case through the full pipeline and check all
    deterministic assertions. Optionally score with the LLM judge.
    """
    from backend.app.chat_service import run_chat
    from backend.evals.run_eval import check_assertions

    messages = case.get("messages", [{"role": "user", "content": case.get("question", "")}])
    assertions = case.get("assertions", {})

    result = run_chat(messages=messages, conn=eval_db)

    # ── Deterministic assertions ──────────────────────────────────────────────
    checks = check_assertions(result, assertions)
    failures = [c for c in checks if not c["pass"]]

    # ── Optional LLM judge ────────────────────────────────────────────────────
    judge_result = None
    if os.getenv("EVAL_JUDGE", "") == "1":
        from backend.evals.judge import judge_response

        judge_model = os.getenv("EVAL_JUDGE_MODEL", "gpt-4o-mini")
        judge_result = judge_response(
            question=case.get("question", messages[-1].get("content", "")),
            tool_results=result.get("tool_results", []),
            reply=result.get("reply", ""),
            reference_answer=case.get("reference_answer"),
            model=judge_model,
        )

    # ── Build failure message ─────────────────────────────────────────────────
    if failures or judge_result:
        lines = [
            f"Case: {case['id']} ({case.get('category', '')})",
            f"Reply: {result.get('reply', '')[:300]}",
        ]
        for f in failures:
            name = f["name"]
            if name == "answer_contains":
                lines.append(f"  FAIL answer_contains — missing: {f.get('missing')}")
            elif name == "answer_excludes":
                lines.append(f"  FAIL answer_excludes — found: {f.get('found')}")
            else:
                lines.append(f"  FAIL {name} — expected {f.get('expected')!r}, got {f.get('got')!r}")
        if judge_result and not judge_result.get("error"):
            lines.append(
                f"  Judge: correctness={judge_result['correctness']} "
                f"completeness={judge_result['completeness']} "
                f"clarity={judge_result['clarity']} "
                f"grounding={judge_result['grounding']} "
                f"overall={judge_result['overall']}/5.0"
            )
            lines.append(f"  Justification: {judge_result.get('justification', '')}")
        elif judge_result and judge_result.get("error"):
            lines.append(f"  Judge error: {judge_result['error']}")

        assert not failures, "\n".join(lines)
