"""
backend/app/chat_service.py

Orchestrates the LLM tool-calling loop with SSE streaming support.

Performance improvements:
  1. Singleton AsyncOpenAI client — created once, reused across requests.
  2. Intent pre-routing — common query patterns are detected by keyword and
     the relevant data fetched *before* the first LLM call, injected as a
     system context block.  This collapses most requests from 2 LLM round-trips
     to 1, saving ~2–3 s of latency.
  3. Async HTTP — the FastAPI endpoint is async, so the event loop is never
     blocked on network I/O.
  4. SSE Streaming — tokens are streamed to the client as they arrive,
     so the user sees output within ~1 s instead of waiting ~8 s.
  5. max_tool_rounds=3 hard limit (was 5).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from typing import Any, AsyncGenerator

try:
    from openai import AsyncOpenAI  # noqa: F401
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]

from .config import settings
from .tools import TOOL_DEFINITIONS, dispatch_tool

logger = logging.getLogger(__name__)

# ── Singleton client ─────────────────────────────────────────────────────────────

_client: Any = None   # AsyncOpenAI | None

def _get_client() -> Any:
    global _client
    if _client is None and AsyncOpenAI is not None and settings.openai_configured:
        kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        _client = AsyncOpenAI(**kwargs)
    return _client


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
{"labels": ["2023-04", ...], "datasets": [{"label": "Series Name", "data": [1234.56, ...]}]}

For bar_chart (comparison between categories/products):
{"labels": ["Electronics", ...], "datasets": [{"label": "Revenue ($)", "data": [123456, ...]}]}

For horizontal_bar_chart (ranking):
{"labels": ["Product A", ...], "datasets": [{"label": "Revenue ($)", "data": [123456, ...]}]}

For pie_chart (distribution/share):
{"labels": ["Cash", ...], "datasets": [{"label": "Transactions", "data": [25.5, ...]}]}

For kpi_card (single metric or overall KPIs):
{"kpis": [{"label": "Total Revenue", "value": "$24,833,495.51", "icon": "💰"}, ...]}

For table (detail lookup — customer purchases, store lists):
{"columns": ["Date", "Product", "Category", "Amount"], "rows": [["2024-01-15", "A", "Electronics", "$250.00"], ...]}

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


# ── Intent pre-router ────────────────────────────────────────────────────────────

_PREFETCH_RULES: list[tuple[re.Pattern[str], str, dict[str, Any]]] = [
    (re.compile(r"\bkpi|overall|summary\b", re.I),
     "get_business_metric", {"metric_name": "overall_kpis"}),
    (re.compile(r"\bmonthly.{0,20}(revenue|trend|category)\b", re.I),
     "get_business_metric", {"metric_name": "monthly_revenue_by_category"}),
    (re.compile(r"\bmonthly.{0,20}(product|by product)\b", re.I),
     "get_business_metric", {"metric_name": "monthly_revenue_by_product"}),
    (re.compile(r"\bmonthly.{0,20}(revenue|trend)\b", re.I),
     "get_business_metric", {"metric_name": "monthly_revenue"}),
    (re.compile(r"\b(payment|method|distribution|share)\b", re.I),
     "get_business_metric", {"metric_name": "payment_method_breakdown"}),
    (re.compile(r"\bcategor.{0,20}(compare|comparison|breakdown|revenue|all)\b", re.I),
     "get_business_metric", {"metric_name": "category_comparison"}),
    (re.compile(r"\bproduct.{0,20}(compare|comparison|rank|top|revenue|all)\b", re.I),
     "get_business_metric", {"metric_name": "product_comparison"}),
    (re.compile(r"\b(top|rank|highest).{0,20}(store|location)\b", re.I),
     "get_business_metric", {"metric_name": "revenue_by_store"}),
    (re.compile(r"\b(top|rank|highest).{0,20}(product|item)\b", re.I),
     "get_business_metric", {"metric_name": "top_products_by_revenue"}),
    (re.compile(r"\bdiscount\b", re.I),
     "get_business_metric", {"metric_name": "discount_by_category"}),
]


def _try_prefetch(last_user_text: str, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """
    Try to pre-fetch data based on the user's last message.
    Returns a list of tool result dicts to inject, or [] if no match.
    """
    for pattern, tool_name, args in _PREFETCH_RULES:
        if pattern.search(last_user_text):
            result = dispatch_tool(tool_name, args, conn)
            logger.debug("Pre-fetched %s %s", tool_name, args)
            return [{"tool": tool_name, "args": args, "result": result}]
    return []


# ── SSE streaming entry point ────────────────────────────────────────────────────

async def stream_chat(
    messages: list[dict[str, str]],
    conn: sqlite3.Connection,
    max_tool_rounds: int = 3,
) -> AsyncGenerator[str, None]:
    """
    Async generator that yields SSE-formatted strings.

    Event types emitted:
      data: {"type": "token",     "content": "..."}          — streaming token
      data: {"type": "tool_call", "tool": "...", "status": "running"}
      data: {"type": "tool_done", "tool": "...", "ok": true}
      data: {"type": "done",      "structured": {...},
                                  "tool_results": [...],
                                  "metadata": {...}}
      data: {"type": "error",     "message": "..."}
    """
    def sse(payload: dict[str, Any]) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    if not settings.openai_configured:
        yield sse({"type": "error", "message": "OpenAI API key is not configured."})
        return

    if AsyncOpenAI is None:
        yield sse({"type": "error", "message": "The openai package is not installed."})
        return

    client = _get_client()

    # ── Pre-fetch ────────────────────────────────────────────────────────────────
    last_user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_text = m.get("content", "")
            break

    prefetched = _try_prefetch(last_user_text, conn)
    tool_results_log: list[dict[str, Any]] = list(prefetched)

    # Emit prefetch events so the UI can show "fetching data..."
    for pf in prefetched:
        yield sse({"type": "tool_done", "tool": pf["tool"],
                   "args": pf["args"], "ok": pf["result"].get("ok", False)})

    # ── Build message list ───────────────────────────────────────────────────────
    full_messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if prefetched:
        ctx_parts = []
        for pf in prefetched:
            ctx_parts.append(
                f"[Pre-fetched data for tool={pf['tool']} args={json.dumps(pf['args'])}]\n"
                f"{json.dumps(pf['result'])}"
            )
        full_messages.append({
            "role": "system",
            "content": "DATA ALREADY FETCHED — use this data to answer directly:\n\n" + "\n\n".join(ctx_parts),
        })

    full_messages.extend(list(messages))

    rounds = 0
    accumulated_content = ""

    while rounds < max_tool_rounds:
        rounds += 1
        try:
            # ── Phase 1: check if we need tool calls first (non-streaming) ────
            # We use a non-streaming call only when tool calls are likely needed.
            # For the final answer round, we stream.
            # Strategy: stream always; if finish_reason=tool_calls, gather and loop.

            stream = await client.chat.completions.create(
                model=settings.openai_model,
                messages=full_messages,
                tools=TOOL_DEFINITIONS,
                tool_choice="auto",
                stream=True,
            )
        except Exception as exc:
            logger.error("OpenAI API error: %s", exc)
            yield sse({"type": "error", "message": str(exc)})
            return

        # Accumulate streaming response
        accumulated_content = ""
        tool_calls_acc: dict[int, dict[str, Any]] = {}  # index → {id, name, arguments}
        finish_reason = None

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            if choice is None:
                continue

            finish_reason = choice.finish_reason or finish_reason
            delta = choice.delta

            # Stream text tokens
            if delta.content:
                accumulated_content += delta.content
                yield sse({"type": "token", "content": delta.content})

            # Accumulate tool call deltas
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.id or "",
                            "name": tc_delta.function.name or "" if tc_delta.function else "",
                            "arguments": "",
                        }
                    if tc_delta.id:
                        tool_calls_acc[idx]["id"] = tc_delta.id
                    if tc_delta.function:
                        if tc_delta.function.name:
                            tool_calls_acc[idx]["name"] = tc_delta.function.name
                        if tc_delta.function.arguments:
                            tool_calls_acc[idx]["arguments"] += tc_delta.function.arguments

        # ── No tool calls → final answer ─────────────────────────────────────
        if not tool_calls_acc:
            structured = _parse_structured_response(accumulated_content)
            yield sse({
                "type": "done",
                "structured": structured,
                "tool_results": tool_results_log,
                "metadata": {
                    "model": settings.openai_model,
                    "tool_rounds": rounds,
                    "prefetched": len(prefetched) > 0,
                    "finish_reason": finish_reason,
                },
            })
            return

        # ── Process tool calls ────────────────────────────────────────────────
        # Reconstruct the assistant message with tool_calls for the next round
        tool_calls_list = []
        for idx in sorted(tool_calls_acc.keys()):
            tc = tool_calls_acc[idx]
            tool_calls_list.append({
                "id": tc["id"],
                "type": "function",
                "function": {
                    "name": tc["name"],
                    "arguments": tc["arguments"],
                },
            })

        # Add assistant message (may have empty content when only tool calls)
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": tool_calls_list,
        }
        if accumulated_content:
            assistant_msg["content"] = accumulated_content
        full_messages.append(assistant_msg)

        for tc in tool_calls_list:
            tool_name = tc["function"]["name"]
            raw_args = tc["function"]["arguments"]

            yield sse({"type": "tool_call", "tool": tool_name, "status": "running"})

            try:
                tool_args = json.loads(raw_args) if raw_args else {}
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            tool_result = dispatch_tool(tool_name, tool_args, conn)
            tool_results_log.append({"tool": tool_name, "args": tool_args, "result": tool_result})

            yield sse({"type": "tool_done", "tool": tool_name,
                       "args": tool_args, "ok": tool_result.get("ok", False)})

            full_messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(tool_result),
            })

    # Exceeded max rounds
    yield sse({
        "type": "done",
        "structured": None,
        "tool_results": tool_results_log,
        "metadata": {
            "model": settings.openai_model,
            "tool_rounds": rounds,
            "warning": "max_tool_rounds_exceeded",
        },
    })


# ── Non-streaming fallback (used by tests) ────────────────────────────────────────

async def run_chat(
    messages: list[dict[str, str]],
    conn: sqlite3.Connection,
    max_tool_rounds: int = 3,
) -> dict[str, Any]:
    """
    Non-streaming version. Collects all SSE events and returns final result dict.
    Kept for backward-compatibility with tests.
    """
    if not settings.openai_configured:
        return {
            "reply": "OpenAI API key is not configured. Please set OPENAI_API_KEY in your .env file.",
            "structured": None,
            "tool_results": [],
            "metadata": {"error": "no_api_key"},
        }

    if AsyncOpenAI is None:
        return {
            "reply": "The openai package is not installed. Run: pip install openai",
            "structured": None,
            "tool_results": [],
            "metadata": {"error": "openai_not_installed"},
        }

    client = _get_client()

    # ── Pre-fetch ────────────────────────────────────────────────────────────────
    last_user_text = ""
    for m in reversed(messages):
        if m.get("role") == "user":
            last_user_text = m.get("content", "")
            break

    prefetched = _try_prefetch(last_user_text, conn)
    tool_results_log: list[dict[str, Any]] = list(prefetched)

    full_messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    if prefetched:
        ctx_parts = []
        for pf in prefetched:
            ctx_parts.append(
                f"[Pre-fetched data for tool={pf['tool']} args={json.dumps(pf['args'])}]\n"
                f"{json.dumps(pf['result'])}"
            )
        full_messages.append({
            "role": "system",
            "content": "DATA ALREADY FETCHED — use this data to answer directly:\n\n" + "\n\n".join(ctx_parts),
        })

    full_messages.extend(list(messages))

    rounds = 0

    while rounds < max_tool_rounds:
        rounds += 1
        try:
            response = await client.chat.completions.create(
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

        if finish_reason == "stop" or not assistant_msg.tool_calls:
            raw_content = assistant_msg.content or ""
            structured = _parse_structured_response(raw_content)
            reply = structured.get("answer", raw_content) if structured else raw_content

            return {
                "reply": reply,
                "structured": structured,
                "tool_results": tool_results_log,
                "metadata": {
                    "model": settings.openai_model,
                    "tool_rounds": rounds,
                    "prefetched": len(prefetched) > 0,
                    "finish_reason": finish_reason,
                },
            }

        for tool_call in assistant_msg.tool_calls:
            tool_name = tool_call.function.name
            raw_args = tool_call.function.arguments

            try:
                tool_args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError):
                tool_args = {}

            tool_result = dispatch_tool(tool_name, tool_args, conn)
            tool_results_log.append({"tool": tool_name, "args": tool_args, "result": tool_result})

            full_messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": json.dumps(tool_result),
            })

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


# ── JSON parser ──────────────────────────────────────────────────────────────────

def _parse_structured_response(raw: str) -> dict[str, Any] | None:
    """Extract and validate a structured JSON response from LLM output."""
    if not raw:
        return None

    text = raw.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        inner = "\n".join(lines[1:] if lines[0].startswith("```") else lines)
        inner = inner.rsplit("```", 1)[0]
        text = inner.strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict) and "answer" in parsed:
            return parsed
    except (json.JSONDecodeError, ValueError):
        pass

    # Fallback: extract JSON substring
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
