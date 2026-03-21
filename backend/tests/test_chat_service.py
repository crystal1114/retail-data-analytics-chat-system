"""
backend/tests/test_chat_service.py

Unit tests for the NL→SQL chat service.
All OpenAI calls are mocked — no live API needed.
"""

from __future__ import annotations

import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import backend.app.chat_service as chat_service
from backend.app.chat_service import _parse_structured_response, run_chat


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    """In-memory SQLite with minimal transactions data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id TEXT, product_id TEXT, quantity INTEGER,
            price REAL, transaction_date TEXT, payment_method TEXT,
            store_location TEXT, product_category TEXT,
            discount_pct REAL, total_amount REAL
        )
    """)
    conn.executemany(
        "INSERT INTO transactions VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
        [
            ("C001", "A", 2, 50.0, "1/15/2024 10:00", "Cash",
             "123 Main St", "Electronics", 10.0, 90.00),
            ("C002", "B", 1, 30.0, "2/20/2024 11:00", "PayPal",
             "456 Oak Ave", "Books", 5.0, 28.50),
            ("C001", "B", 3, 20.0, "3/5/2024 9:00", "Cash",
             "789 Pine Rd", "Books", 0.0, 60.00),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


def _make_stop_response(content: str) -> MagicMock:
    """Build a mock OpenAI completion response with finish_reason='stop'."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    msg.model_dump.return_value = {"role": "assistant", "content": content}

    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _make_tool_call_response(tool_name: str, args: dict) -> MagicMock:
    """Build a mock response that triggers a tool call."""
    func = MagicMock()
    func.name = tool_name
    func.arguments = json.dumps(args)

    tc = MagicMock()
    tc.id = "tc_test_001"
    tc.function = func

    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]
    msg.model_dump.return_value = {
        "role": "assistant",
        "tool_calls": [
            {"id": "tc_test_001", "type": "function",
             "function": {"name": tool_name, "arguments": json.dumps(args)}}
        ],
    }

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


# ── SQL safety tests ──────────────────────────────────────────────────────────

class TestSqlSafety:
    def test_select_allowed(self, db):
        from backend.app.sql_tool import run_sql
        result = run_sql("SELECT COUNT(*) AS n FROM transactions", db)
        assert result["ok"] is True
        assert result["rows"][0][0] == 3

    def test_insert_blocked(self, db):
        from backend.app.sql_tool import run_sql
        result = run_sql("INSERT INTO transactions (customer_id) VALUES ('X')", db)
        assert result["ok"] is False
        assert result["error"] == "unsafe_sql"

    def test_drop_blocked(self, db):
        from backend.app.sql_tool import run_sql
        result = run_sql("DROP TABLE transactions", db)
        assert result["ok"] is False

    def test_update_blocked(self, db):
        from backend.app.sql_tool import run_sql
        result = run_sql("UPDATE transactions SET total_amount=0", db)
        assert result["ok"] is False

    def test_multiple_statements_blocked(self, db):
        from backend.app.sql_tool import run_sql
        result = run_sql("SELECT 1; DROP TABLE transactions", db)
        assert result["ok"] is False

    def test_sql_error_returns_ok_false(self, db):
        from backend.app.sql_tool import run_sql
        result = run_sql("SELECT * FROM nonexistent_table", db)
        assert result["ok"] is False
        assert result["error"] == "sql_error"


# ── chat_service unit tests ───────────────────────────────────────────────────

class TestRunChat:

    def test_no_api_key_returns_error(self, db, monkeypatch):
        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "")
        chat_service._client = None
        result = run_chat([{"role": "user", "content": "hello"}], db)
        assert result["metadata"]["error"] == "no_api_key"
        assert "API key" in result["reply"]

    def test_openai_not_installed_returns_error(self, db, monkeypatch):
        monkeypatch.setattr("backend.app.chat_service.OpenAI", None)
        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test-key")
        chat_service._client = None
        result = run_chat([{"role": "user", "content": "hello"}], db)
        assert result["metadata"]["error"] == "openai_not_installed"

    def test_sql_tool_flow(self, db, monkeypatch):
        """LLM calls execute_sql, gets results, returns structured answer."""
        sql = "SELECT SUM(total_amount) AS total FROM transactions"
        structured_reply = json.dumps({
            "intent": "kpi_query",
            "viz_type": "kpi_card",
            "insight": "Total revenue is $178.50.",
            "chart_data": {"kpis": [{"label": "Total Revenue", "value": "$178.50", "icon": "💰"}]},
            "answer": "The total revenue across all transactions is $178.50.",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _make_tool_call_response("execute_sql", {"sql": sql, "description": "total revenue"}),
            _make_stop_response(structured_reply),
        ]

        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test-key")
        chat_service._client = mock_client

        result = run_chat([{"role": "user", "content": "What is the total revenue?"}], db)

        assert result["reply"] == "The total revenue across all transactions is $178.50."
        assert result["structured"]["viz_type"] == "kpi_card"
        assert len(result["tool_results"]) == 1
        assert result["tool_results"][0]["tool"] == "execute_sql"
        assert result["tool_results"][0]["result"]["ok"] is True
        assert result["metadata"]["tool_rounds"] == 2

    def test_direct_answer_no_tool(self, db, monkeypatch):
        """LLM answers directly without calling a tool (e.g. clarification)."""
        direct_reply = json.dumps({
            "intent": "unsupported_query",
            "viz_type": "none",
            "insight": "Question is unclear.",
            "chart_data": None,
            "answer": "Could you clarify which customer you mean?",
        })

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = _make_stop_response(direct_reply)

        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test-key")
        chat_service._client = mock_client

        result = run_chat([{"role": "user", "content": "Tell me about the customer"}], db)
        assert "clarify" in result["reply"].lower()
        assert result["metadata"]["tool_rounds"] == 1

    def test_unsafe_sql_blocked_and_error_returned(self, db, monkeypatch):
        """If LLM generates a write statement, it's blocked and error fed back."""
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            _make_tool_call_response("execute_sql", {
                "sql": "DELETE FROM transactions",
                "description": "delete all"
            }),
            _make_stop_response(json.dumps({
                "intent": "unsupported_query", "viz_type": "none",
                "insight": "Cannot execute.", "chart_data": None,
                "answer": "I cannot delete data from the database.",
            })),
        ]

        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test-key")
        chat_service._client = mock_client

        result = run_chat([{"role": "user", "content": "Delete all transactions"}], db)
        # Tool result should show ok=False
        assert result["tool_results"][0]["result"]["ok"] is False
        assert result["tool_results"][0]["result"]["error"] == "unsafe_sql"

    def test_openai_error_handled_gracefully(self, db, monkeypatch):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = Exception("Connection error")

        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test-key")
        chat_service._client = mock_client

        result = run_chat([{"role": "user", "content": "Show revenue"}], db)
        assert result["metadata"]["error"] == "openai_api_error"
        assert "Connection error" in result["reply"]


# ── _parse_structured_response ────────────────────────────────────────────────

class TestParseStructuredResponse:
    def test_valid_json(self):
        raw = json.dumps({
            "intent": "kpi_query", "viz_type": "kpi_card",
            "insight": "ok", "chart_data": None,
            "answer": "Revenue is $100."
        })
        result = _parse_structured_response(raw)
        assert result is not None
        assert result["answer"] == "Revenue is $100."

    def test_json_in_code_fence(self):
        raw = '```json\n{"intent":"kpi_query","viz_type":"none","insight":"","chart_data":null,"answer":"Hello"}\n```'
        result = _parse_structured_response(raw)
        assert result is not None
        assert result["answer"] == "Hello"

    def test_missing_answer_returns_none(self):
        raw = json.dumps({"intent": "kpi_query", "viz_type": "none"})
        assert _parse_structured_response(raw) is None

    def test_empty_string_returns_none(self):
        assert _parse_structured_response("") is None

    def test_plain_text_returns_none(self):
        assert _parse_structured_response("Just a plain sentence.") is None
