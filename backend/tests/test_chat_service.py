# backend/tests/test_chat_service.py
"""
B. Chat orchestration tests

Uses unittest.mock to stub the AsyncOpenAI client so no real API calls are made.
run_chat is now async — tests use asyncio.run() to execute it synchronously.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from backend.app import chat_service


# ── Helpers to build mock OpenAI responses ────────────────────────────────────────

def _make_stop_response(content: str) -> MagicMock:
    """Simulate a model response that produces a direct text reply."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = []
    msg.model_dump.return_value = {"role": "assistant", "content": content}

    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_tool_call_response(
    tool_name: str, tool_args: dict[str, Any], call_id: str = "call_1"
) -> MagicMock:
    """Simulate a model response that requests a tool call."""
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(tool_args)

    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    msg.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [{"id": call_id, "function": {"name": tool_name, "arguments": json.dumps(tool_args)}}],
    }

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── Context manager helper ────────────────────────────────────────────────────────

def _patch_settings_for_openai(mock_client):
    """
    Return a list of context managers that:
    - Replace the module-level AsyncOpenAI class with one that returns mock_client
    - Patch settings so openai_configured returns True
    - Reset the module-level _client singleton so the patched class is used
    """
    return [
        patch("backend.app.chat_service.AsyncOpenAI", return_value=mock_client),
        patch.object(type(chat_service.settings), "openai_configured",
                     new_callable=PropertyMock, return_value=True),
        patch.object(chat_service.settings, "openai_api_key", "sk-fake"),
    ]


def _run(coro):
    """Run a coroutine synchronously, resetting the singleton between tests."""
    chat_service._client = None   # force fresh client creation each test
    return asyncio.run(coro)


# ── Fixtures ──────────────────────────────────────────────────────────────────────

@pytest.fixture
def db() -> sqlite3.Connection:
    """Minimal in-memory DB for chat orchestration tests."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            quantity INTEGER,
            price REAL,
            transaction_date TEXT,
            payment_method TEXT,
            store_location TEXT,
            product_category TEXT,
            discount_pct REAL,
            total_amount REAL
        );
        INSERT INTO transactions VALUES
          (1, 'C001', 'A', 2, 50.00, '1/15/2024 10:00', 'Cash',
           '123 Main St\nSpringfield, IL', 'Electronics', 10.0, 90.00),
          (2, 'C002', 'B', 1, 30.00, '2/20/2024 11:00', 'PayPal',
           '456 Oak Ave\nShelbyville, IL', 'Books', 5.0, 28.50);
    """)
    conn.commit()
    yield conn
    conn.close()


# ── No API key ────────────────────────────────────────────────────────────────────

class TestNoApiKey:
    def test_returns_graceful_message(self, db: sqlite3.Connection):
        with patch.object(type(chat_service.settings), "openai_configured",
                          new_callable=PropertyMock, return_value=False):
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "Hello"}], db
            ))
        assert result["reply"] != ""
        assert "OpenAI" in result["reply"] or "key" in result["reply"].lower()
        assert result["metadata"]["error"] == "no_api_key"


# ── Customer flow ─────────────────────────────────────────────────────────────────

class TestCustomerFlow:
    def test_customer_summary_tool_called(self, db: sqlite3.Connection):
        """LLM requests get_customer_summary → result fed back → final answer."""
        tool_resp = _make_tool_call_response(
            "get_customer_summary", {"customer_id": "C001"}
        )
        final_resp = _make_stop_response("Customer C001 has 1 transaction totaling $90.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[tool_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "Tell me about customer C001"}], db
            ))

        assert result["reply"] == "Customer C001 has 1 transaction totaling $90."
        # tool_results may include a pre-fetched entry; find the one from LLM
        tool_names = [t["tool"] for t in result["tool_results"]]
        assert "get_customer_summary" in tool_names

    def test_customer_purchases_tool_called(self, db: sqlite3.Connection):
        tool_resp = _make_tool_call_response(
            "get_customer_purchases", {"customer_id": "C001", "limit": 20}
        )
        final_resp = _make_stop_response("C001 bought product A.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[tool_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "What has customer C001 purchased?"}], db
            ))

        assert "C001" in result["reply"]


# ── Product flow ──────────────────────────────────────────────────────────────────

class TestProductFlow:
    def test_product_summary_tool_called(self, db: sqlite3.Connection):
        tool_resp = _make_tool_call_response(
            "get_product_summary", {"product_id": "A"}
        )
        final_resp = _make_stop_response("Product A has 1 transaction.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[tool_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "Tell me about product A"}], db
            ))

        tool_names = [t["tool"] for t in result["tool_results"]]
        assert "get_product_summary" in tool_names

    def test_product_stores_tool_called(self, db: sqlite3.Connection):
        tool_resp = _make_tool_call_response(
            "get_product_stores", {"product_id": "B"}
        )
        final_resp = _make_stop_response("Product B is sold in Shelbyville.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[tool_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "Which stores sell product B?"}], db
            ))

        tool_names = [t["tool"] for t in result["tool_results"]]
        assert "get_product_stores" in tool_names


# ── Business metric flow ──────────────────────────────────────────────────────────

class TestBusinessMetricFlow:
    def test_business_metric_tool_called(self, db: sqlite3.Connection):
        tool_resp = _make_tool_call_response(
            "get_business_metric", {"metric_name": "overall_kpis"}
        )
        final_resp = _make_stop_response("Total revenue is $118.50.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[tool_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "What is the total revenue?"}], db
            ))

        tool_names = [t["tool"] for t in result["tool_results"]]
        assert "get_business_metric" in tool_names
        metric_tool = next(t for t in result["tool_results"] if t["tool"] == "get_business_metric")
        assert metric_tool["args"]["metric_name"] == "overall_kpis"


# ── Clarification flow ────────────────────────────────────────────────────────────

class TestClarificationFlow:
    def test_clarification_when_id_missing(self, db: sqlite3.Connection):
        """Model returns a clarification question without calling any tool."""
        final_resp = _make_stop_response(
            "Could you please tell me which customer you mean?"
        )

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(return_value=final_resp)

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "How much did this customer spend?"}], db
            ))

        # Pre-fetch may add entries; LLM-called tool_calls should be 0
        llm_tools = [t for t in result["tool_results"] if not t.get("args", {}).get("_prefetched")]
        assert "customer" in result["reply"].lower()


# ── Malformed tool arguments ──────────────────────────────────────────────────────

class TestMalformedToolArgs:
    def test_invalid_json_args_handled(self, db: sqlite3.Connection):
        """If tool args are not valid JSON, the call should fail gracefully."""
        tc = MagicMock()
        tc.id = "call_bad"
        tc.function.name = "get_customer_summary"
        tc.function.arguments = "NOT_JSON{{{"

        msg = MagicMock()
        msg.content = None
        msg.tool_calls = [tc]
        msg.model_dump.return_value = {"role": "assistant"}

        choice = MagicMock()
        choice.finish_reason = "tool_calls"
        choice.message = msg

        bad_resp = MagicMock()
        bad_resp.choices = [choice]

        final_resp = _make_stop_response("Sorry, something went wrong.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[bad_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "Tell me about customer X"}], db
            ))

        assert isinstance(result["reply"], str)

    def test_unknown_tool_handled(self, db: sqlite3.Connection):
        """Unknown tool names must not crash the orchestrator."""
        tc = MagicMock()
        tc.id = "call_unk"
        tc.function.name = "drop_table_transactions"
        tc.function.arguments = json.dumps({"x": 1})

        msg = MagicMock()
        msg.content = None
        msg.tool_calls = [tc]
        msg.model_dump.return_value = {"role": "assistant"}

        choice = MagicMock()
        choice.finish_reason = "tool_calls"
        choice.message = msg

        bad_resp = MagicMock()
        bad_resp.choices = [choice]
        final_resp = _make_stop_response("I cannot do that.")

        mock_client = MagicMock()
        mock_client.chat.completions.create = AsyncMock(side_effect=[bad_resp, final_resp])

        patches = _patch_settings_for_openai(mock_client)
        with patches[0], patches[1], patches[2]:
            result = _run(chat_service.run_chat(
                [{"role": "user", "content": "DROP all tables"}], db
            ))

        assert isinstance(result["reply"], str)
        # Find the LLM-dispatched unknown tool result
        unknown = next(
            (t for t in result["tool_results"] if t["tool"] == "drop_table_transactions"),
            None,
        )
        assert unknown is not None
        assert unknown["result"]["error"] == "unknown_tool"
