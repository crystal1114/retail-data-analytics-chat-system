"""
backend/evals/judge.py

LLM-as-judge: scores a model response on four dimensions using a second
(cheap, fast) LLM call.

Dimensions (each scored 1-5):
  correctness   – Does the answer accurately reflect the SQL results?
                  No invented numbers or hallucinated facts?
  completeness  – Does it address all parts of the question?
  clarity       – Is the answer well-structured and easy to understand?
  grounding     – Does it stick strictly to the data returned,
                  or add unrequested context / speculation?

Usage
─────
    from backend.evals.judge import judge_response

    scores = judge_response(
        question="What is the total revenue?",
        tool_results=[...],      # raw tool_results list from run_chat()
        reply="The total revenue is $1,845.",
        reference_answer="...",  # optional
        model="gpt-4o-mini",
    )
    # scores = {
    #   "correctness": 5, "completeness": 5, "clarity": 5, "grounding": 5,
    #   "overall": 5.0, "justification": "...", "error": None
    # }
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_JUDGE_PROMPT = """\
You are an impartial evaluator judging the quality of an AI retail analytics assistant's answer.

──────────────────────────────────────────────────────
QUESTION ASKED BY THE USER
{question}

──────────────────────────────────────────────────────
SQL RESULT DATA (ground truth from the database)
{sql_data}

──────────────────────────────────────────────────────
ASSISTANT'S ANSWER
{reply}
{reference_block}
──────────────────────────────────────────────────────

Score the assistant's answer on each dimension from 1 (poor) to 5 (excellent):

  correctness   – All numbers, facts, and entities match the SQL results exactly.
                  Score 1 if numbers are invented or wrong; 5 if perfectly accurate.
  completeness  – The answer addresses every part of the question.
                  Score 1 if major parts are missing; 5 if fully answered.
  clarity       – The answer is well-structured, concise, and easy to understand.
                  Score 1 if confusing or cluttered; 5 if exceptionally clear.
  grounding     – The answer is grounded only in the data returned; no speculation,
                  no extra context the data does not support.
                  Score 1 if heavily speculative; 5 if strictly data-grounded.

Respond with ONLY this JSON object (no prose, no markdown fences):
{{"correctness": N, "completeness": N, "clarity": N, "grounding": N, "justification": "one sentence"}}
"""

_REFERENCE_BLOCK_TEMPLATE = """\

REFERENCE ANSWER (for correctness comparison)
{reference_answer}
"""


def _format_sql_data(tool_results: list[dict[str, Any]]) -> str:
    """Summarise tool results into a compact text block for the judge prompt."""
    if not tool_results:
        return "(no SQL was executed)"
    parts: list[str] = []
    for i, tr in enumerate(tool_results, 1):
        result = tr.get("result", {})
        if not result.get("ok"):
            parts.append(f"[Query {i}] Error: {result.get('error', 'unknown')}")
            continue
        columns = result.get("columns", [])
        rows = result.get("rows", [])
        row_count = result.get("row_count", len(rows))
        truncated = result.get("truncated", False)
        header = " | ".join(columns)
        data_lines = [" | ".join(str(cell) for cell in row) for row in rows[:20]]
        summary = f"[Query {i}] {row_count} row(s)"
        if truncated:
            summary += " (truncated)"
        parts.append(f"{summary}\n{header}\n" + "\n".join(data_lines))
    return "\n\n".join(parts) if parts else "(no data returned)"


def judge_response(
    question: str,
    tool_results: list[dict[str, Any]],
    reply: str,
    reference_answer: str | None = None,
    model: str = "gpt-4o-mini",
    client: Any = None,
) -> dict[str, Any]:
    """
    Score a model response using a second LLM call.

    Returns a dict with keys:
      correctness, completeness, clarity, grounding  (int 1-5 each)
      overall      (float: average of the four dimensions)
      justification (str: one-sentence explanation)
      error        (str | None: set if the judge call itself failed)
    """
    fallback = {
        "correctness": 0,
        "completeness": 0,
        "clarity": 0,
        "grounding": 0,
        "overall": 0.0,
        "justification": "",
        "error": None,
    }

    # ── Build prompt ─────────────────────────────────────────────────────────
    sql_data = _format_sql_data(tool_results)
    reference_block = (
        _REFERENCE_BLOCK_TEMPLATE.format(reference_answer=reference_answer)
        if reference_answer
        else ""
    )
    prompt = _JUDGE_PROMPT.format(
        question=question,
        sql_data=sql_data,
        reply=reply,
        reference_block=reference_block,
    )

    # ── Call OpenAI ──────────────────────────────────────────────────────────
    if client is None:
        try:
            from openai import OpenAI
        except ImportError:
            fallback["error"] = "openai package not installed"
            return fallback

        from backend.app.config import settings as _settings
        if not _settings.openai_configured:
            fallback["error"] = "OPENAI_API_KEY not configured"
            return fallback
        kwargs: dict[str, Any] = {"api_key": _settings.openai_api_key}
        if _settings.openai_base_url:
            kwargs["base_url"] = _settings.openai_base_url
        client = OpenAI(**kwargs)

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
        )
    except Exception as exc:
        logger.error("Judge LLM call failed: %s", exc)
        fallback["error"] = f"api_error: {exc}"
        return fallback

    raw = (response.choices[0].message.content or "").strip()

    # ── Parse scores ─────────────────────────────────────────────────────────
    try:
        # Strip optional code fences
        text = raw
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()
        scores = json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("Could not parse judge response: %s | raw: %s", exc, raw[:200])
        fallback["error"] = f"parse_error: {exc}"
        fallback["justification"] = raw[:200]
        return fallback

    dims = ["correctness", "completeness", "clarity", "grounding"]
    result: dict[str, Any] = {dim: int(scores.get(dim, 0)) for dim in dims}
    valid_dims = [result[d] for d in dims if result[d] > 0]
    result["overall"] = round(sum(valid_dims) / len(valid_dims), 2) if valid_dims else 0.0
    result["justification"] = str(scores.get("justification", ""))
    result["error"] = None
    return result
