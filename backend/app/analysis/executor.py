"""
backend/app/analysis/executor.py

Runs each planned analysis step — generates SQL or Python code via the LLM,
then executes it through the existing sql_tool or the sandbox.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..sql_tool import SCHEMA, run_sql
from .sandbox import run_code
from .schemas import AnalysisStep

logger = logging.getLogger(__name__)

TABLE_ROW_CAP = 200

_SQL_CODEGEN_SYSTEM = """\
You are a SQL expert. Given a step description, produce a single SQLite SELECT
query that retrieves the requested data.

DATABASE SCHEMA:
{schema}

Rules:
- Output ONLY the SQL query, no explanation, no markdown fences.
- Use aggregates (COUNT, SUM, AVG, GROUP BY) when appropriate.
- transaction_date is 'M/D/YYYY H:MM' — convert to ISO before strftime().
  ISO expr: printf('%04d-%02d-%02d',
    CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),
    CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT),
    CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT))
- store_location uses 2-letter state codes: LIKE '%, HI %' for Hawaii.
- LIMIT results to 200 rows max for raw-row queries.
"""

_PYTHON_CODEGEN_SYSTEM = """\
You are a Python data analyst. Write pandas code to analyze DataFrames from
prior SQL steps.

Available variables (pandas DataFrames):
{available_vars}

Rules:
- Output ONLY Python code, no explanation, no markdown fences.
- You have: pd (pandas), json, math.
- Assign your final output to a variable called `result`.
  - For tabular output: result = {{"columns": [...], "rows": [...]}}
  - For a text summary: result = "Your summary string"
  - Or: result = a_dataframe  (it will be serialized automatically)
- Keep it concise — no plotting, no file I/O, no network calls.
"""


def execute_step(
    step: AnalysisStep,
    conn: sqlite3.Connection,
    client: Any,
    model: str,
    completed_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Generate code for *step* and execute it.

    Returns a result dict with at least {"ok": bool, ...}.
    """
    if step.type == "sql":
        return _execute_sql_step(step, conn, client, model)
    elif step.type == "python":
        return _execute_python_step(step, client, model, completed_results)
    else:
        return {"ok": False, "error": f"Unknown step type: {step.type}"}


def _execute_sql_step(
    step: AnalysisStep,
    conn: sqlite3.Connection,
    client: Any,
    model: str,
) -> dict[str, Any]:
    """Generate a SQL query for the step, then run it."""
    system = _SQL_CODEGEN_SYSTEM.format(schema=SCHEMA)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": step.description},
        ],
        temperature=0.0,
        max_tokens=1024,
    )

    sql = (response.choices[0].message.content or "").strip()
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()

    step.sql = sql
    result = run_sql(sql, conn, limit=TABLE_ROW_CAP)

    if result.get("ok") and result.get("rows"):
        result["rows"] = result["rows"][:TABLE_ROW_CAP]

    return result


def _execute_python_step(
    step: AnalysisStep,
    client: Any,
    model: str,
    completed_results: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Generate Python code, then execute it in the sandbox."""
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

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": step.description},
        ],
        temperature=0.0,
        max_tokens=2048,
    )

    code = (response.choices[0].message.content or "").strip()
    if code.startswith("```"):
        lines = code.split("\n")
        code = "\n".join(lines[1:]).rsplit("```", 1)[0].strip()

    step.code = code
    return run_code(code, dep_data)
