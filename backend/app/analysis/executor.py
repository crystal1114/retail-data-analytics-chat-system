"""
backend/app/analysis/executor.py

Runs each planned analysis step — generates SQL or Python code via the LLM,
then executes it through the existing sql_tool or the sandbox.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

from ..sql_tool import SCHEMA, run_sql
from .sandbox import run_code
from .schemas import AnalysisStep

logger = logging.getLogger(__name__)

_SQL_START = re.compile(r"^\s*(SELECT|WITH)\b", re.IGNORECASE | re.MULTILINE)

TABLE_ROW_CAP = 200

_SQL_CODEGEN_SYSTEM = """\
You are a SQL expert. Given a step description, produce a single SQLite SELECT
query that retrieves the requested data.

DATABASE SCHEMA:
{schema}

Rules:
- Output ONLY the raw SQL query — no explanation, no markdown fences, no prose.
- Start your response directly with SELECT or WITH (for CTEs).
- CTEs are encouraged for complex multi-step logic: WITH cte AS (...) SELECT ...
- Use aggregates (COUNT, SUM, AVG, GROUP BY) when appropriate.
- store_location uses 2-letter state codes: LIKE '%, HI %' for Hawaii.
- LIMIT results to 200 rows max for raw-row queries.
- ROUND monetary values to 2 decimal places.

══ DATE HANDLING (CRITICAL — copy these expressions exactly) ══

transaction_date is stored as 'M/D/YYYY H:MM' (e.g. '3/11/2024 18:51').
strftime() ONLY works on ISO dates, so you MUST convert first.

Year (integer):
  CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT)

Month (integer):
  CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT)

Day (integer):
  CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)

Month label for GROUP BY (e.g. '2024-03'):
  printf('%04d-%02d',
    CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),
    CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT))

ISO date string (required before any strftime call):
  printf('%04d-%02d-%02d',
    CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),
    CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT),
    CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT))

Day of week (0=Sunday … 6=Saturday):
  strftime('%w', <ISO_DATE_STRING_FROM_ABOVE>)

Day name:
  CASE strftime('%w', <ISO_DATE_STRING>)
    WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
    WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday'
    WHEN '5' THEN 'Friday' WHEN '6' THEN 'Saturday' END
"""

MAX_SQL_RETRIES = 2

_PYTHON_CODEGEN_SYSTEM = """\
You are a Python data analyst. Write pandas code to analyze DataFrames from
prior SQL steps.

Available variables (pandas DataFrames):
{available_vars}

Rules:
- Output ONLY Python code — no explanation, no markdown fences, no prose.
- You have: pd (pandas), json, math.
- ⚠️ CRITICAL: Your code MUST end by assigning to a variable called `result`.
  Examples:
    result = df_summary                    # a DataFrame
    result = "Revenue grew 12% YoY..."     # a text summary string
    result = {{"columns": [...], "rows": [...]}}  # a dict with tabular data
  If you forget `result = ...`, the step WILL FAIL.
- Keep it concise — no plotting, no file I/O, no network calls.
"""


def execute_step(
    step: AnalysisStep,
    conn: sqlite3.Connection,
    client: Any,
    model: str,
    completed_results: dict[str, dict[str, Any]],
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """
    Generate code for *step* and execute it.

    Returns a result dict with at least {"ok": bool, ...}.
    """
    if step.type == "sql":
        return _execute_sql_step(step, conn, client, model, reasoning_effort)
    elif step.type == "python":
        return _execute_python_step(step, client, model, completed_results, reasoning_effort)
    else:
        return {"ok": False, "error": f"Unknown step type: {step.type}"}


def _extract_sql(raw: str) -> str:
    """
    Extract a SQL query from LLM output that may contain markdown fences,
    reasoning prose, or mixed content.
    """
    text = raw.strip()

    # 1. Extract from ```sql ... ``` or ``` ... ``` fenced blocks
    fence_match = re.search(r"```(?:sql)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        candidate = fence_match.group(1).strip()
        if _SQL_START.match(candidate):
            return candidate

    # 2. Strip leading ``` fence if the whole response is one block
    if text.startswith("```"):
        lines = text.split("\n")
        inner = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()
        if _SQL_START.match(inner):
            return inner

    # 3. Already clean SQL
    if _SQL_START.match(text):
        return text.rstrip(";").strip() + ";" if not text.rstrip().endswith(";") else text

    # 4. Find the first SELECT/WITH in the text (skip reasoning prose)
    m = _SQL_START.search(text)
    if m:
        return text[m.start():].strip()

    return text


def _execute_sql_step(
    step: AnalysisStep,
    conn: sqlite3.Connection,
    client: Any,
    model: str,
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Generate a SQL query for the step, then run it. Retries on error."""
    system = _SQL_CODEGEN_SYSTEM.format(schema=SCHEMA)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": step.description},
    ]

    for attempt in range(1 + MAX_SQL_RETRIES):
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": 1024,
        }
        if reasoning_effort:
            request_kwargs["reasoning_effort"] = reasoning_effort

        response = client.chat.completions.create(**request_kwargs)

        raw = (response.choices[0].message.content or "").strip()
        sql = _extract_sql(raw)
        logger.info("Step %s attempt %d SQL:\n%s", step.step_id, attempt + 1, sql[:300])

        step.sql = sql
        result = run_sql(sql, conn, limit=TABLE_ROW_CAP)

        if result.get("ok"):
            if result.get("rows"):
                result["rows"] = result["rows"][:TABLE_ROW_CAP]
            return result

        if attempt < MAX_SQL_RETRIES:
            error_msg = result.get("message") or result.get("error", "unknown error")
            logger.info("Step %s attempt %d failed: %s — retrying", step.step_id, attempt + 1, error_msg)
            messages.append({"role": "assistant", "content": sql})
            messages.append({
                "role": "user",
                "content": (
                    f"That query failed with error: {error_msg}\n"
                    "Please fix the SQL and output ONLY the corrected query."
                ),
            })
        else:
            return result

    return result


def _extract_code(raw: str) -> str:
    """
    Extract Python code from LLM output that may contain markdown fences or prose.
    """
    text = raw.strip()

    fence_match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence_match:
        return fence_match.group(1).strip()

    if text.startswith("```"):
        lines = text.split("\n")
        return "\n".join(lines[1:]).rsplit("```", 1)[0].strip()

    # If there's prose before the code, look for first import/assignment/keyword
    code_start = re.search(r"^(import |from |[a-zA-Z_]\w*\s*=)", text, re.MULTILINE)
    if code_start:
        return text[code_start.start():].strip()

    return text


def _execute_python_step(
    step: AnalysisStep,
    client: Any,
    model: str,
    completed_results: dict[str, dict[str, Any]],
    reasoning_effort: str | None = None,
) -> dict[str, Any]:
    """Generate Python code, then execute it in the sandbox. Retries on error."""
    dep_data: dict[str, dict[str, Any]] = {}
    var_descriptions: list[str] = []

    for dep_id in step.depends_on:
        if dep_id in completed_results:
            r = completed_results[dep_id]
            dep_data[dep_id] = r
            cols = r.get("columns", [])
            n = len(r.get("rows", []))
            var_descriptions.append(
                f"step_{dep_id}: DataFrame with columns {cols} ({n} rows)"
            )

    available_vars = "\n".join(var_descriptions) if var_descriptions else "(none)"
    system = _PYTHON_CODEGEN_SYSTEM.format(available_vars=available_vars)

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": step.description},
    ]

    for attempt in range(1 + MAX_SQL_RETRIES):
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_completion_tokens": 2048,
        }
        if reasoning_effort:
            request_kwargs["reasoning_effort"] = reasoning_effort

        response = client.chat.completions.create(**request_kwargs)

        raw = (response.choices[0].message.content or "").strip()
        code = _extract_code(raw)

        if not code.strip():
            if raw.strip():
                return {"ok": True, "summary": raw[:2000]}
            if attempt < MAX_SQL_RETRIES:
                messages.append({"role": "assistant", "content": raw})
                messages.append({
                    "role": "user",
                    "content": "You returned no code. Write Python code that assigns to `result`.",
                })
                continue
            return {"ok": False, "error": "LLM returned empty code"}

        step.code = code
        result = run_code(code, dep_data)

        if result.get("ok"):
            return result

        if attempt < MAX_SQL_RETRIES:
            error_msg = result.get("error", "unknown error")
            logger.info("Step %s Python attempt %d failed: %s — retrying", step.step_id, attempt + 1, error_msg)
            messages.append({"role": "assistant", "content": code})
            messages.append({
                "role": "user",
                "content": (
                    f"That code failed with error: {error_msg}\n"
                    "Please fix the code. Remember: you MUST assign your final "
                    "output to a variable called `result` (a string, DataFrame, or dict).\n"
                    "Output ONLY the corrected Python code."
                ),
            })
        else:
            return result

    return result
