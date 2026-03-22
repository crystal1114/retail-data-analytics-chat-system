"""
backend/app/analysis/reporter.py

Assembles all completed step results into a structured report via a final
LLM call.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .schemas import AnalysisReport, AnalysisSection

logger = logging.getLogger(__name__)

SUMMARY_ROW_CAP = 20

_REPORTER_SYSTEM = """\
You are a senior data analyst writing an executive report based on analysis
results from a retail transactions database.

You will receive the results of several analysis steps. Each step has a title,
description, and result data (tables or summaries).

Produce a structured JSON report with:
{{
  "executive_summary": "2-4 sentence overview of key findings",
  "sections": [
    {{
      "title": "Section Title",
      "content": "Narrative paragraph explaining the finding. Reference actual numbers from the data.",
      "table": {{"columns": [...], "rows": [...]}},
      "chart_data": null
    }},
    ...
  ]
}}

Rules:
- Every number you cite MUST come from the step results — never invent data.
- Include 3-6 sections covering the most important findings.
- Each section should have explanatory content (2-4 sentences).
- Include a table only when it adds value (top-N rankings, comparisons).
- Cap tables at 10 rows.
- The executive summary should highlight the top 2-3 actionable insights.
- Respond with ONLY the JSON object, no markdown fences.
"""


def _summarize_step(
    step_id: str,
    title: str,
    description: str,
    result: dict[str, Any],
) -> str:
    """Compact summary of a step result for the reporter prompt."""
    parts = [f"### Step {step_id}: {title}", f"Description: {description}"]

    if result.get("summary"):
        parts.append(f"Summary: {result['summary']}")

    columns = result.get("columns", [])
    rows = result.get("rows", [])
    if columns and rows:
        parts.append(f"Columns: {columns}")
        display_rows = rows[:SUMMARY_ROW_CAP]
        for row in display_rows:
            parts.append(f"  {row}")
        if len(rows) > SUMMARY_ROW_CAP:
            parts.append(f"  ... ({len(rows) - SUMMARY_ROW_CAP} more rows)")

    return "\n".join(parts)


def generate_report(
    steps_info: list[dict[str, Any]],
    client: Any,
    model: str = "gpt-4o-mini",
) -> AnalysisReport:
    """
    Call the LLM to assemble step results into a structured report.

    Parameters
    ----------
    steps_info : list
        Each dict has keys: step_id, title, description, result.
    """
    step_summaries = []
    for s in steps_info:
        if s.get("result") and s["result"].get("ok", True):
            step_summaries.append(
                _summarize_step(s["step_id"], s["title"], s["description"], s["result"])
            )

    if not step_summaries:
        return AnalysisReport(
            executive_summary="No analysis steps completed successfully.",
            sections=[],
        )

    user_content = "\n\n".join(step_summaries)

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _REPORTER_SYSTEM},
            {"role": "user", "content": user_content},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    raw = (response.choices[0].message.content or "").strip()
    text = raw
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()

    data = json.loads(text)

    sections = []
    for sec in data.get("sections", []):
        sections.append(AnalysisSection(
            title=sec.get("title", ""),
            content=sec.get("content", ""),
            table=sec.get("table"),
            chart_data=sec.get("chart_data"),
        ))

    return AnalysisReport(
        executive_summary=data.get("executive_summary", ""),
        sections=sections,
    )
