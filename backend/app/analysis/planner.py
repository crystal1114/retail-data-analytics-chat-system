"""
backend/app/analysis/planner.py

Decomposes a broad user request into 3-8 concrete analysis steps via a single
LLM call. Each step is either a SQL query or a Python (pandas) analysis.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .schemas import AnalysisStep

logger = logging.getLogger(__name__)

_PLANNER_SYSTEM = """\
You are a data analyst planning an investigation of a retail transactions database.

DATABASE SCHEMA:
{schema}

The user will give you a broad analysis request. Break it into 3-8 concrete,
sequential steps. Each step must be one of:

  type: "sql"    — retrieves data from the database (a single SELECT query will
                    be generated and executed for you later)
  type: "python" — performs cross-step computation using pandas on results from
                    earlier SQL steps (code will be generated and executed later).
                    Use ONLY when you need cross-step calculations that SQL alone
                    cannot do (e.g. correlation, percentile ranking across
                    different result sets, pivot tables merging multiple queries).

Rules:
- Prefer SQL steps — they are more reliable and faster.
- Each step has a unique step_id (e.g. "s1", "s2", ...).
- Python steps must list which earlier step_ids they depend on in depends_on.
- SQL steps should use aggregates where possible (COUNT, SUM, AVG, GROUP BY)
  to avoid fetching too many rows.
- Cover diverse angles: revenue, customers, products, time trends, comparisons.
- Keep descriptions actionable — they will be used to generate code.
- Do NOT add a final "synthesis" or "summary" step — the report generator
  handles that automatically from all step results.

Respond with ONLY a JSON array (no prose, no markdown fences):
[
  {{"step_id": "s1", "title": "...", "type": "sql", "description": "...", "depends_on": []}},
  ...
]
"""


def plan_steps(
    prompt: str,
    schema: str,
    client: Any,
    model: str = "gpt-4o-mini",
    reasoning_effort: str | None = None,
) -> list[AnalysisStep]:
    """Call the LLM to decompose *prompt* into analysis steps."""

    system = _PLANNER_SYSTEM.format(schema=schema)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": 2048,
    }
    if reasoning_effort:
        request_kwargs["reasoning_effort"] = reasoning_effort

    response = client.chat.completions.create(
        **request_kwargs,
    )

    raw = (response.choices[0].message.content or "").strip()

    # Strip optional markdown fences
    text = raw
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()

    steps_data: list[dict[str, Any]] = json.loads(text)

    steps = [
        AnalysisStep(
            step_id=s["step_id"],
            title=s["title"],
            type=s["type"],
            description=s["description"],
            depends_on=s.get("depends_on", []),
        )
        for s in steps_data
    ]

    if not steps:
        raise ValueError("Planner returned zero steps")

    return steps
