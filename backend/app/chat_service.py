"""
backend/app/chat_service.py

NL → SQL → Answer pipeline.

Architecture:
  1. User message arrives.
  2. Broad-query guard: if the message matches "show all data / all transactions"
     patterns, bypass the LLM and return a summary+sample directly.
  3. LLM is given the table schema and told to call execute_sql() with a
     SELECT statement that answers the question.
  4. The SQL is validated (SELECT-only guard), auto-limited, and run against SQLite.
  5. If SQL times out, a friendly narrowing suggestion is returned immediately.
  6. Query results (with truncation metadata) are injected back into conversation.
  7. LLM produces a final structured JSON response.

Why LLM-generated SQL is appropriate here:
  - The dataset is public retail transaction data (owned by the operator).
  - The system is used for internal business analytics over known data.
  - The goal is maximum query flexibility.
  - The SQL executor enforces a hard SELECT-only guard; no mutations can occur.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

from .config import settings
from .sql_tool import (
    TOOL_DEFINITIONS,
    SCHEMA,
    PREVIEW_ROWS,
    dispatch,
    is_broad_query,
    broad_query_summary,
)

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
You MUST call execute_sql for EVERY data question. NEVER answer from memory or guess numbers.

DATABASE SCHEMA:
{SCHEMA}

WORKFLOW: translate user question → SQLite SELECT → call execute_sql → return JSON.

SQL RULES:
1. DATE — transaction_date is stored as 'M/D/YYYY H:MM' (NOT ISO-8601).
   ⚠ NEVER use strftime() on transaction_date — it only works on ISO dates.
   ⚠ NEVER compare transaction_date with ISO strings like '2024-01-01'.
   Extract parts with substr/instr:
     MONTH: CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT)
     DAY:   CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)
     YEAR:  CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT)
   Month label: printf('%04d-%02d', YEAR_EXPR, MONTH_EXPR)
   Example — Q1 2024 revenue:
     SELECT SUM(total_amount) FROM transactions
     WHERE CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1,4) AS INT) = 2024
       AND CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT) BETWEEN 1 AND 3

2. STORE — store_location format: 'Street\\nCity, ST ZIP'. Filter by 2-letter state: LIKE '%, HI %'

3. GENERAL — ORDER meaningfully. ROUND money to 2dp. LIMIT top-N. Only SELECT/WITH allowed.
   Write ONE query per question (use CTEs/subqueries, not multiple tool calls).
   100K rows — raw queries auto-capped at {PREVIEW_ROWS}; suggest narrowing if truncated.

RESPONSE FORMAT — after SQL results, return ONLY this JSON:
{{
  "intent": "<customer_query|product_query|trend_query|comparison_query|ranking_query|distribution_query|kpi_query|custom_query>",
  "viz_type": "<line_chart|bar_chart|horizontal_bar_chart|pie_chart|table|kpi_card|none>",
  "insight": "<1-2 sentence finding>",
  "chart_data": <see shapes below, or null>,
  "answer": "<natural-language answer with formatted numbers>"
}}

viz_type: time trend→line_chart, category comparison→bar_chart, ranking→horizontal_bar_chart, share→pie_chart, list→table, single metric→kpi_card
chart_data shapes:
  line/bar/horizontal_bar: {{"labels":[...],"datasets":[{{"label":"...","data":[...]}}]}}
  pie: {{"labels":[...],"datasets":[{{"label":"Share","data":[...]}}]}}
  kpi_card: {{"kpis":[{{"label":"...","value":"...","icon":"..."}}]}}
  table: {{"columns":[...],"rows":[[...]]}}
"""


# ── Broad-query fallback ──────────────────────────────────────────────────────

def _make_broad_query_response(summary_result: dict[str, Any]) -> dict[str, Any]:
    """Convert the broad_query_summary dict into a full ChatResponse."""
    s = summary_result.get("summary", {})
    total = s.get("total_transactions", 0)
    revenue = s.get("total_revenue", 0)
    customers = s.get("unique_customers", 0)

    answer = (
        f"The transactions table contains **{total:,} rows** — too large to display in full. "
        f"Here's a quick overview:\n\n"
        f"• **Total revenue**: ${revenue:,.2f}\n"
        f"• **Unique customers**: {customers:,}\n"
        f"• **Date range**: {s.get('earliest_date', '?')} → {s.get('latest_date', '?')}\n\n"
        f"Below is a 5-row sample. Try asking a more specific question like:\n"
        f"  — *\"Show monthly revenue trend\"*\n"
        f"  — *\"Which product category earns the most?\"*\n"
        f"  — *\"Top 10 customers by spend\"*"
    )

    chart_data = {
        "columns": summary_result.get("columns", []),
        "rows": summary_result.get("rows", []),
    }

    structured = {
        "intent": "kpi_query",
        "viz_type": "table",
        "insight": f"Dataset has {total:,} transactions. Showing a 5-row sample.",
        "chart_data": chart_data,
        "answer": answer,
    }

    return {
        "reply": answer,
        "structured": structured,
        "tool_results": [],
        "metadata": {
            "pipeline": "nl_to_sql",
            "fallback_mode": "broad_query",
            "truncated": True,
            "total_rows": total,
            "has_more": True,
            "warning": "broad_query_redirected",
        },
    }


# ── Timeout fallback ──────────────────────────────────────────────────────────

_TIMEOUT_SUGGESTIONS = [
    "Add a date range filter (e.g. year 2024 or a specific month)",
    "Filter by a product category (Books, Electronics, Clothing, Home Decor)",
    "Filter by payment method (Cash, Credit Card, Debit Card, PayPal)",
    "Filter by state abbreviation (e.g. WHERE store_location LIKE '%, CA %')",
    "Ask for an aggregate (total revenue, average order value) instead of raw rows",
]

def _make_timeout_response(
    tool_results_log: list[dict],
    model: str,
    rounds: int,
) -> dict[str, Any]:
    suggestions = "\n".join(f"  • {s}" for s in _TIMEOUT_SUGGESTIONS)
    reply = (
        "The query took too long and was stopped to protect performance. "
        "This usually happens when scanning all 100,000 rows without a filter.\n\n"
        f"**Try narrowing your question:**\n{suggestions}"
    )
    return {
        "reply": reply,
        "structured": {
            "intent": "custom_query",
            "viz_type": "none",
            "insight": "Query timed out — too broad without filters.",
            "chart_data": None,
            "answer": reply,
        },
        "tool_results": tool_results_log,
        "metadata": {
            "model": model,
            "tool_rounds": rounds,
            "warning": "query_timeout",
            "fallback_mode": "timeout",
            "pipeline": "nl_to_sql",
        },
    }


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
            "tool_results": list[dict],
            "metadata":     dict,          # includes truncated, total_rows, has_more, fallback_mode
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

    # ── Broad-query interception (before LLM call) ────────────────────────────
    user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            user_text = m.get("content", "")
            break

    if is_broad_query(user_text):
        logger.info("Broad query detected — returning summary+sample: %r", user_text[:80])
        summary_result = broad_query_summary(conn)
        return _make_broad_query_response(summary_result)

    # ── LLM tool-calling loop ─────────────────────────────────────────────────
    client = _get_client()

    full_messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *list(messages),
    ]

    tool_results_log: list[dict[str, Any]] = []
    rounds = 0
    # Collect metadata from tool calls for pass-through
    result_meta: dict[str, Any] = {}

    t_start = time.monotonic()

    while rounds < max_tool_rounds:
        rounds += 1
        try:
            t_llm = time.monotonic()
            request_kwargs: dict[str, Any] = {
                "model": settings.resolved_chat_model,
                "messages": full_messages,
                "tools": TOOL_DEFINITIONS,
                "tool_choice": "required" if rounds == 1 else "auto",
            }
            if settings.openai_chat_reasoning_effort:
                request_kwargs["reasoning_effort"] = settings.openai_chat_reasoning_effort
            response = client.chat.completions.create(**request_kwargs)
            logger.info("LLM round %d: %.2fs", rounds, time.monotonic() - t_llm)
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
                    "model": settings.resolved_chat_model,
                    "tool_rounds": rounds,
                    "finish_reason": finish_reason,
                    "pipeline": "nl_to_sql",
                    **result_meta,
                },
            }

        # Execute each tool call (execute_sql)
        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            try:
                tool_args = json.loads(tool_call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            sql_preview = tool_args.get("sql", "")
            description = tool_args.get("description", tool_name)
            logger.info("SQL [round %d]: %s", rounds, sql_preview)

            tool_result = dispatch(tool_name, tool_args, conn)

            # ── Timeout: abort immediately with friendly message ──────────────
            if tool_result.get("error") == "timeout":
                logger.warning("SQL timeout at round %d: %s", rounds, sql_preview[:100])
                tool_results_log.append({
                    "tool": tool_name,
                    "args": {"sql": sql_preview, "description": description},
                    "result": tool_result,
                })
                return _make_timeout_response(tool_results_log, settings.resolved_chat_model, rounds)

            # ── Collect truncation metadata for pass-through ─────────────────
            if tool_result.get("ok"):
                if tool_result.get("truncated"):
                    result_meta["truncated"] = True
                if tool_result.get("has_more"):
                    result_meta["has_more"] = True
                if tool_result.get("total_rows") is not None:
                    result_meta["total_rows"] = tool_result["total_rows"]
                if tool_result.get("limit_injected"):
                    result_meta["limit_injected"] = True

            tool_results_log.append({
                "tool": tool_name,
                "args": {"sql": sql_preview, "description": description},
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
            "model": settings.resolved_chat_model,
            "tool_rounds": rounds,
            "warning": "max_tool_rounds_exceeded",
            "pipeline": "nl_to_sql",
            **result_meta,
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
                    break

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

    stripped = raw.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "answer" in obj:
                return str(obj["answer"])
        except (json.JSONDecodeError, ValueError):
            pass
        return "I couldn't format this response properly. Please try again."

    return raw
