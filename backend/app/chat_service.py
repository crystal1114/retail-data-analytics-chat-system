"""
backend/app/chat_service.py

NL → SQL → Answer pipeline.

Architecture:
  1. User message arrives.
  2. LLM is given the table schema and told to call execute_sql() with a
     SELECT statement that answers the question.
  3. The SQL is validated (SELECT-only guard) and run against SQLite.
  4. Query results are injected back into the conversation.
  5. LLM produces a final structured JSON response with a natural-language
     answer AND a visualization spec (chart_data).

Why LLM-generated SQL is appropriate here:
  - The dataset is public retail transaction data (owned by the operator).
  - The system is used for internal business analytics over known data.
  - The goal is maximum query flexibility — pre-canned repository functions
    cannot answer arbitrary slice-and-dice questions.
  - The SQL executor enforces a hard SELECT-only guard so no mutations
    can occur regardless of what the LLM generates.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

from .config import settings
from .sql_tool import TOOL_DEFINITIONS, SCHEMA, dispatch

logger = logging.getLogger(__name__)

# ── Singleton client ──────────────────────────────────────────────────────────

_client: Any = None

def _get_client() -> Any:
    global _client
    if _client is None and OpenAI is not None and settings.openai_configured:
        kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        _client = OpenAI(**kwargs)
    return _client


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""\
You are a retail analytics assistant with direct access to a SQLite database.

DATABASE SCHEMA:
{SCHEMA}

YOUR WORKFLOW:
1. Translate the user's question into a precise SQLite SELECT query.
2. Call the execute_sql tool with that query.
3. After receiving the results, produce a final JSON response (see format below).

══ CRITICAL SQL RULES ══

1. DATE FORMAT — transaction_date is 'M/D/YYYY H:MM' (NOT ISO). strftime() returns NULL on raw dates.
   You MUST always convert to ISO first:

   ISO date expression (copy exactly):
     printf('%04d-%02d-%02d',
       CAST(substr(transaction_date,
         instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1,4) AS INT),
       CAST(substr(transaction_date,1,instr(transaction_date,'/')-1) AS INT),
       CAST(substr(transaction_date,
         instr(transaction_date,'/')+1,
         instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT))

   Month grouping:  printf('%04d-%02d', YEAR_INT, MONTH_INT)
   Day of week:     strftime('%w', <ISO_EXPR>) → '0'=Sun, '1'=Mon … '6'=Sat
   Day name:        CASE strftime('%w',<ISO_EXPR>) WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday'
                    WHEN '2' THEN 'Tuesday' WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday'
                    WHEN '5' THEN 'Friday' WHEN '6' THEN 'Saturday' END

   ⚠️ COLUMN ALIAS ORDER — in SQLite the alias MUST come immediately after each expression.
   CORRECT day-of-week query (copy this pattern exactly, get ALL 7 days not just the max):
     SELECT
       CASE strftime('%w', printf('%04d-%02d-%02d',
           CAST(substr(transaction_date,instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1,4) AS INT),
           CAST(substr(transaction_date,1,instr(transaction_date,'/')-1) AS INT),
           CAST(substr(transaction_date,instr(transaction_date,'/')+1,instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)))
         WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
         WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday'
         WHEN '5' THEN 'Friday' WHEN '6' THEN 'Saturday' END AS day_name,
       COUNT(*) AS tx_count
     FROM transactions
     GROUP BY day_name
     ORDER BY tx_count DESC

2. STORE LOCATION — format is multi-line: 'Street\\nCity, STATE ZIP'
   STATE is always a 2-letter US abbreviation. NEVER filter by full state name.
   Examples:
     Hawaii     → WHERE store_location LIKE '%, HI %'
     California → WHERE store_location LIKE '%, CA %'
     New York   → WHERE store_location LIKE '%, NY %'
     Texas      → WHERE store_location LIKE '%, TX %'

   ⚠️ For "revenue by store in STATE" queries, ALWAYS aggregate (GROUP BY + SUM) in a single query.
   Do NOT first select raw rows and then aggregate in a second round.
   ALWAYS include the total in the same query using a subquery or window function, OR answer total + top stores in ONE query using UNION or a CTE.
   Correct pattern for "revenue for stores in Hawaii":
     SELECT store_location, ROUND(SUM(total_amount),2) AS revenue
     FROM transactions
     WHERE store_location LIKE '%, HI %'
     GROUP BY store_location
     ORDER BY revenue DESC
     LIMIT 20

3. GENERAL — Always ORDER results meaningfully. ROUND monetary values to 2dp.
   Use LIMIT for top-N queries. Only SELECT is allowed.
   ⚠️ EFFICIENCY — Write ONE query that answers the full question. Never run an exploratory
   query first and then a second aggregation query. If a question asks for "total + breakdown",
   compute both in a single SQL call using subqueries, CTEs (WITH), or SUM() OVER() windows.

FINAL RESPONSE FORMAT — after receiving SQL results, return ONLY this JSON (no markdown):
{{
  "intent": "<customer_query | product_query | trend_query | comparison_query | ranking_query | distribution_query | kpi_query | custom_query>",
  "viz_type": "<line_chart | bar_chart | horizontal_bar_chart | pie_chart | table | kpi_card | none>",
  "insight": "<1-2 sentence key finding grounded in the query results>",
  "chart_data": <structured data for the chosen viz, or null>,
  "answer": "<full natural-language answer with formatted numbers>"
}}

VIZ TYPE RULES:
- Time trend (monthly/yearly) → line_chart
- Category/product comparison (2–8 groups) → bar_chart
- Ranking / top-N → horizontal_bar_chart
- Share / distribution / % breakdown → pie_chart
- Customer purchase list, store detail, tabular results → table
- Single metric or overall KPIs → kpi_card
- Unclear or conversational → none

CHART DATA SHAPES:
line_chart / bar_chart / horizontal_bar_chart:
  {{"labels": ["2023-04",...], "datasets": [{{"label": "Revenue ($)", "data": [1234.56,...]}}]}}

pie_chart:
  {{"labels": ["Cash",...], "datasets": [{{"label": "Share", "data": [25.5,...]}}]}}

kpi_card:
  {{"kpis": [{{"label": "Total Revenue", "value": "$24,833,495", "icon": "💰"}}, ...]}}

table:
  {{"columns": ["Store","Revenue"], "rows": [["123 Main St, Springfield, IL","$12345.67"],...]}}

IMPORTANT: Your final reply must be the JSON object only — no prose before or after it.
"""


# ── Main entry point ──────────────────────────────────────────────────────────

def run_chat(
    messages: list[dict[str, str]],
    conn: sqlite3.Connection,
    max_tool_rounds: int = 6,
) -> dict[str, Any]:
    """
    Execute one conversational turn using the NL→SQL→Answer pipeline.

    Returns:
        {
            "reply":        str,
            "structured":   dict | None,
            "tool_results": list[dict],   # SQL queries + results for debug panel
            "metadata":     dict,
        }
    """
    if not settings.openai_configured:
        return {
            "reply": "OpenAI API key is not configured. Please set OPENAI_API_KEY in your .env file.",
            "structured": None,
            "tool_results": [],
            "metadata": {"error": "no_api_key"},
        }

    if OpenAI is None:
        return {
            "reply": "The openai package is not installed. Run: pip install openai",
            "structured": None,
            "tool_results": [],
            "metadata": {"error": "openai_not_installed"},
        }

    client = _get_client()

    full_messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *list(messages),
    ]

    tool_results_log: list[dict[str, Any]] = []
    rounds = 0

    while rounds < max_tool_rounds:
        rounds += 1
        try:
            response = client.chat.completions.create(
                model=settings.openai_model,
                messages=full_messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
            )
        except Exception as exc:
            logger.error("OpenAI API error: %s", exc)
            return {
                "reply": f"An error occurred while calling the AI service: {exc}",
                "structured": None,
                "tool_results": tool_results_log,
                "metadata": {"error": "openai_api_error", "detail": str(exc)},
            }

        choice = response.choices[0]
        finish_reason = choice.finish_reason
        assistant_msg = choice.message

        full_messages.append(assistant_msg.model_dump(exclude_none=True))

        # No tool calls → final structured answer
        if finish_reason == "stop" or not assistant_msg.tool_calls:
            raw_content = assistant_msg.content or ""
            structured = _parse_structured_response(raw_content)
            reply = _safe_reply(raw_content, structured)

            return {
                "reply": reply,
                "structured": structured,
                "tool_results": tool_results_log,
                "metadata": {
                    "model": settings.openai_model,
                    "tool_rounds": rounds,
                    "finish_reason": finish_reason,
                    "pipeline": "nl_to_sql",
                },
            }

        # Execute each tool call (execute_sql)
        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            # Log the SQL being run for the debug panel
            sql_preview = tool_args.get("sql", "")
            description = tool_args.get("description", tool_name)
            logger.info("SQL [round %d]: %s", rounds, sql_preview)

            tool_result = dispatch(tool_name, tool_args, conn)

            tool_results_log.append({
                "tool": tool_name,
                "args": {
                    "sql": sql_preview,
                    "description": description,
                },
                "result": tool_result,
            })

            full_messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result),
            })

    # Exceeded max rounds
    last_content = ""
    for msg in reversed(full_messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            last_content = msg.get("content") or ""
            break

    return {
        "reply": last_content or "I was unable to complete this request (too many tool rounds).",
        "structured": None,
        "tool_results": tool_results_log,
        "metadata": {
            "model": settings.openai_model,
            "tool_rounds": rounds,
            "warning": "max_tool_rounds_exceeded",
            "pipeline": "nl_to_sql",
        },
    }


# ── JSON parser ───────────────────────────────────────────────────────────────

def _parse_structured_response(raw: str) -> dict[str, Any] | None:
    """
    Extract and validate structured JSON from LLM output.

    Handles:
    - Pure JSON string
    - JSON wrapped in ```json ... ``` fences
    - JSON preceded/followed by prose (extracts the outermost {...} block)
    - Nested braces (finds the outermost balanced { ... } block)
    """
    if not raw:
        return None

    # 1. Strip markdown code fences
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        inner = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
        inner = inner.rsplit("```", 1)[0]
        text = inner.strip()

    # 2. Try parsing the whole text as JSON
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "answer" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # 3. Find the outermost balanced { ... } block using brace counting
    #    This correctly handles nested objects (chart_data rows etc.)
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escape_next = False
        for i, ch in enumerate(text[start:], start):
            if escape_next:
                escape_next = False
                continue
            if ch == "\\" and in_string:
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    candidate = text[start:i + 1]
                    try:
                        parsed = json.loads(candidate)
                        if isinstance(parsed, dict) and "answer" in parsed:
                            return parsed
                    except (json.JSONDecodeError, ValueError):
                        pass
                    break  # only try outermost block

    return None


def _safe_reply(raw: str, structured: dict[str, Any] | None) -> str:
    """
    Always return a clean natural-language reply string.
    If structured parse succeeded, use the 'answer' field.
    If it failed but raw looks like JSON, extract 'answer' from it.
    Otherwise return raw as-is.
    """
    if structured:
        return structured.get("answer", raw) or raw

    # Last-resort: if raw starts with '{', try to pull 'answer' key out
    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "answer" in obj:
                return str(obj["answer"])
        except (json.JSONDecodeError, ValueError):
            pass
        # Still looks like raw JSON but can't parse — return a fallback
        return "I couldn't format this response properly. Please try again."

    return raw
