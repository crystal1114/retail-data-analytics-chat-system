"""
backend/app/chat_service.py

Orchestrates the LLM tool-calling loop.

NEW in v2: The LLM returns structured JSON responses with visualization metadata:
  {
    "intent": "trend_query | comparison_query | ranking_query | ...",
    "viz_type": "line_chart | bar_chart | horizontal_bar_chart | pie_chart | table | kpi_card",
    "insight": "A short textual summary of the key finding",
    "chart_data": { ... },   // structured for the chosen viz type
    "answer": "Full natural-language answer"
  }

Hard constraints:
  - The LLM never generates or executes SQL.
  - All data access goes through repository functions.
  - Final answers use only retrieved tool data.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import sqlite3

try:
    from openai import OpenAI  # noqa: F401
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

from .config import settings
from .tools import TOOL_DEFINITIONS, dispatch_tool

logger = logging.getLogger(__name__)

# ── System prompt ────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a retail analytics assistant. Your job is to answer questions about customers,
products, and business metrics using ONLY the data returned by tools.

CRITICAL RULES:
1. Use provided tools to fetch data. NEVER invent, guess, or fabricate numbers.
2. After receiving tool results, you MUST respond with ONLY a valid JSON object (no markdown, no extra text).
3. If customer/product ID is unknown, ask for clarification.

RESPONSE FORMAT — always return exactly this JSON structure:
{
  "intent": "<one of: customer_query | product_query | trend_query | comparison_query | ranking_query | distribution_query | kpi_query | unsupported_query>",
  "viz_type": "<one of: line_chart | bar_chart | horizontal_bar_chart | pie_chart | table | kpi_card | none>",
  "insight": "<1-2 sentence key finding or summary, grounded in the data>",
  "chart_data": <see chart_data format below, or null if viz_type is none>,
  "answer": "<full natural-language answer with formatted numbers>"
}

CHART DATA FORMATS:

For line_chart (time-based trends):
{
  "labels": ["2023-04", "2023-05", ...],
  "datasets": [
    {"label": "Series Name", "data": [1234.56, 2345.67, ...]}
  ]
}

For bar_chart (comparison between categories/products):
{
  "labels": ["Electronics", "Books", ...],
  "datasets": [
    {"label": "Revenue ($)", "data": [123456, 234567, ...]}
  ]
}

For horizontal_bar_chart (ranking):
{
  "labels": ["Product A", "Product B", ...],
  "datasets": [
    {"label": "Revenue ($)", "data": [123456, 234567, ...]}
  ]
}

For pie_chart (distribution/share):
{
  "labels": ["Cash", "Credit Card", ...],
  "datasets": [
    {"label": "Transactions", "data": [25.5, 30.2, ...]}
  ]
}

For kpi_card (single metric or overall KPIs):
{
  "kpis": [
    {"label": "Total Revenue", "value": "$24,833,495.51", "icon": "💰"},
    {"label": "Transactions", "value": "100,000", "icon": "🛍️"}
  ]
}

For table (detail lookup — customer purchases, store lists):
{
  "columns": ["Date", "Product", "Category", "Amount"],
  "rows": [["2024-01-15", "A", "Electronics", "$250.00"], ...]
}

VIZ TYPE SELECTION RULES:
- Time-based trend questions → line_chart
- Comparison between 2-6 categories/products → bar_chart
- Ranking (top N, most, highest) → horizontal_bar_chart
- Composition/share/percentage/distribution → pie_chart
- Customer purchases or store detail lists → table
- Single customer/product stats, overall KPIs → kpi_card
- Unsupported or unclear → none

DATASET CONTEXT:
- Products: A, B, C, D
- Categories: Books, Clothing, Electronics, Home Decor
- Payment methods: Cash, Credit Card, Debit Card, PayPal
- Data: 2023-2024 transactions
"""


def run_chat(
    messages: list[dict[str, str]],
    conn: sqlite3.Connection,
    max_tool_rounds: int = 5,
) -> dict[str, Any]:
    """
    Execute one conversational turn with tool-calling support.

    Returns:
        {
            "reply":        str,            # natural-language answer
            "structured":   dict | None,    # parsed structured response (viz_type, chart_data, etc.)
            "tool_results": list[dict],     # raw tool outputs (debug)
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

    import backend.app.chat_service as _self_module
    _OpenAI = getattr(_self_module, "OpenAI")

    client_kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        client_kwargs["base_url"] = settings.openai_base_url

    client = _OpenAI(**client_kwargs)

    full_messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ] + list(messages)

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

        # No tool calls → model produced its final answer
        if finish_reason == "stop" or not assistant_msg.tool_calls:
            raw_content = assistant_msg.content or ""

            # Try to parse structured JSON from the response
            structured = _parse_structured_response(raw_content)

            # Use "answer" field as reply if parsed, otherwise use raw
            reply = structured.get("answer", raw_content) if structured else raw_content

            return {
                "reply": reply,
                "structured": structured,
                "tool_results": tool_results_log,
                "metadata": {
                    "model": settings.openai_model,
                    "tool_rounds": rounds,
                    "finish_reason": finish_reason,
                },
            }

        # Process tool calls
        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            raw_args = tool_call.function.arguments

            try:
                tool_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Failed to parse tool args for %s: %s", tool_name, exc)
                tool_args = {}

            tool_result = dispatch_tool(tool_name, tool_args, conn)
            tool_results_log.append(
                {"tool": tool_name, "args": tool_args, "result": tool_result}
            )

            full_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result),
                }
            )

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
        },
    }


def _parse_structured_response(raw: str) -> dict[str, Any] | None:
    """
    Try to extract and validate a structured JSON response from LLM output.
    Returns dict if valid, None otherwise.
    """
    if not raw:
        return None

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last ``` lines
        inner = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
        inner = inner.rsplit("```", 1)[0]
        text = inner.strip()

    try:
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            return None

        # Validate required fields
        if "answer" not in parsed:
            # Might be a plain text response, wrap it
            return None

        return parsed

    except (json.JSONDecodeError, ValueError):
        # Try to extract JSON from within text
        start = raw.find("{")
        end = raw.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start:end])
                if isinstance(parsed, dict) and "answer" in parsed:
                    return parsed
            except (json.JSONDecodeError, ValueError):
                pass

        return None
