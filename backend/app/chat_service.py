"""
backend/app/chat_service.py

Orchestrates the LLM tool-calling loop.

Flow:
  1. Build message list with system prompt + conversation history.
  2. Send to OpenAI with tool definitions.
  3. If the model requests tool calls, dispatch each to the repository layer.
  4. Feed tool results back to the model for final answer generation.
  5. Return the final answer, tool results, and metadata.

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
    from openai import OpenAI  # noqa: F401 – imported here so tests can patch it
except ImportError:
    OpenAI = None  # type: ignore[assignment,misc]

from .config import settings
from .tools import TOOL_DEFINITIONS, dispatch_tool

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
You are a retail analytics assistant for a retail transaction database.
Your job is to answer questions about customers, products, and business metrics.

IMPORTANT RULES:
- Use the provided tools to fetch data. Never invent, guess, or fabricate numbers, IDs, or names.
- If a required customer ID or product ID is not provided in the conversation, ask a clarifying question instead of guessing.
- Answer only using data returned by the tools.
- If data is not found, say so clearly and politely.
- When the user says "they", "it", "that customer", or "that product", refer back to the most recently mentioned entity in the conversation.
- Be concise but complete. Format numbers clearly (e.g. $1,234.56 for currency, commas for large integers).
- For business metric questions (revenue, trends, top products etc.), use get_business_metric with the correct metric name.

DATASET CONTEXT:
- Products have IDs: A, B, C, D
- Customer IDs are numeric strings (e.g., "109318", "579675")
- Categories: Books, Clothing, Electronics, Home Decor
- Payment methods: Cash, Credit Card, Debit Card, PayPal
- Data covers transactions from 2023 to 2024
"""


def run_chat(
    messages: list[dict[str, str]],
    conn: sqlite3.Connection,
    max_tool_rounds: int = 5,
) -> dict[str, Any]:
    """
    Execute one conversational turn with tool-calling support.

    Args:
        messages:        List of {"role": ..., "content": ...} dicts
                         (full conversation history ending with user message).
        conn:            Open SQLite connection.
        max_tool_rounds: Safety cap on tool-call iterations.

    Returns:
        {
            "reply":        str,            # natural-language answer
            "tool_results": list[dict],     # raw tool outputs (debug)
            "metadata":     dict,           # model, intent hints, etc.
        }
    """
    if not settings.openai_configured:
        return {
            "reply": (
                "OpenAI API key is not configured. "
                "Please set OPENAI_API_KEY in your .env file."
            ),
            "tool_results": [],
            "metadata": {"error": "no_api_key"},
        }

    if OpenAI is None:
        return {
            "reply": "The openai package is not installed. Run: pip install openai",
            "tool_results": [],
            "metadata": {"error": "openai_not_installed"},
        }

    import backend.app.chat_service as _self_module
    _OpenAI = getattr(_self_module, 'OpenAI')

    # Build client kwargs — include base_url only when explicitly configured
    client_kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        client_kwargs["base_url"] = settings.openai_base_url

    client = _OpenAI(**client_kwargs)

    # Build full message list: system + history
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
                "tool_results": tool_results_log,
                "metadata": {"error": "openai_api_error", "detail": str(exc)},
            }

        choice = response.choices[0]
        finish_reason = choice.finish_reason
        assistant_msg = choice.message

        # Add assistant message to history
        full_messages.append(assistant_msg.model_dump(exclude_none=True))

        # No tool calls → model produced its final answer
        if finish_reason == "stop" or not assistant_msg.tool_calls:
            reply = assistant_msg.content or ""
            return {
                "reply": reply,
                "tool_results": tool_results_log,
                "metadata": {
                    "model": settings.openai_model,
                    "tool_rounds": rounds,
                    "finish_reason": finish_reason,
                },
            }

        # Process each tool call in this round
        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            raw_args = tool_call.function.arguments

            # Safely parse JSON arguments
            try:
                tool_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning("Failed to parse tool args for %s: %s", tool_name, exc)
                tool_args = {}

            # Dispatch to repository
            tool_result = dispatch_tool(tool_name, tool_args, conn)
            tool_results_log.append(
                {"tool": tool_name, "args": tool_args, "result": tool_result}
            )

            # Feed result back to OpenAI
            full_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(tool_result),
                }
            )

    # Exceeded max rounds – return partial content if available
    last_content = ""
    for msg in reversed(full_messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            last_content = msg.get("content") or ""
            break

    return {
        "reply": last_content or "I was unable to complete this request (too many tool rounds).",
        "tool_results": tool_results_log,
        "metadata": {
            "model": settings.openai_model,
            "tool_rounds": rounds,
            "warning": "max_tool_rounds_exceeded",
        },
    }
