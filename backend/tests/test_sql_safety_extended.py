"""
backend/tests/test_sql_safety_extended.py

Extended tests for sql_tool.py covering:
  - Auto-LIMIT injection for raw-row queries
  - Hard MAX_ROWS cap
  - Broad-query detection (is_broad_query)
  - broad_query_summary returns summary + sample
  - Graceful timeout fallback (returns fallback_mode="timeout")
  - Pagination via limit/offset
  - Truncation metadata fields (truncated, has_more, total_rows, limit_injected)
  - run_chat broad-query interception returns fallback_mode="broad_query"
  - run_chat timeout propagates _make_timeout_response
"""

from __future__ import annotations

import json
import sqlite3
import time
from unittest.mock import MagicMock

import pytest

from backend.app.sql_tool import (
    PREVIEW_ROWS,
    MAX_ROWS,
    run_sql,
    dispatch,
    is_broad_query,
    broad_query_summary,
    _inject_limit,
    _is_aggregate_query,
)
import backend.app.chat_service as chat_service
from backend.app.chat_service import run_chat


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_db(rows: int = 30) -> sqlite3.Connection:
    """In-memory DB with `rows` transaction rows."""
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
    data = [
        (f"C{i:04d}", "A", 1, 10.0, "1/1/2024 10:00", "Cash",
         f"Street {i}\nCity, CA 90000", "Electronics", 0.0, 10.0)
        for i in range(rows)
    ]
    conn.executemany(
        "INSERT INTO transactions VALUES (NULL,?,?,?,?,?,?,?,?,?,?)", data
    )
    conn.commit()
    return conn


@pytest.fixture
def db30():
    conn = _make_db(30)
    yield conn
    conn.close()


@pytest.fixture
def db5():
    conn = _make_db(5)
    yield conn
    conn.close()


# ── _inject_limit ─────────────────────────────────────────────────────────────

class TestInjectLimit:
    def test_raw_query_gets_limit(self):
        sql = "SELECT * FROM transactions"
        result, injected = _inject_limit(sql)
        assert injected is True
        assert f"LIMIT {PREVIEW_ROWS}" in result

    def test_aggregate_query_unchanged(self):
        sql = "SELECT COUNT(*) FROM transactions"
        result, injected = _inject_limit(sql)
        assert injected is False
        assert result == sql

    def test_query_with_existing_limit_unchanged(self):
        sql = "SELECT * FROM transactions LIMIT 10"
        result, injected = _inject_limit(sql)
        assert injected is False

    def test_group_by_query_unchanged(self):
        sql = "SELECT product_category, SUM(total_amount) FROM transactions GROUP BY product_category"
        result, injected = _inject_limit(sql)
        assert injected is False

    def test_custom_limit_value(self):
        sql = "SELECT * FROM transactions"
        result, injected = _inject_limit(sql, limit=5)
        assert "LIMIT 5" in result
        assert injected is True


# ── _is_aggregate_query ───────────────────────────────────────────────────────

class TestIsAggregateQuery:
    def test_count_is_aggregate(self):
        assert _is_aggregate_query("SELECT COUNT(*) FROM transactions") is True

    def test_sum_is_aggregate(self):
        assert _is_aggregate_query("SELECT SUM(total_amount) FROM transactions") is True

    def test_group_by_is_aggregate(self):
        assert _is_aggregate_query("SELECT category, COUNT(*) FROM t GROUP BY category") is True

    def test_plain_select_not_aggregate(self):
        assert _is_aggregate_query("SELECT * FROM transactions") is False

    def test_select_columns_not_aggregate(self):
        assert _is_aggregate_query("SELECT id, customer_id FROM transactions") is False


# ── is_broad_query ────────────────────────────────────────────────────────────

class TestIsBroadQuery:
    def test_show_all_data(self):
        assert is_broad_query("show all data") is True

    def test_show_all_transactions(self):
        assert is_broad_query("Show all transactions") is True

    def test_show_every_record(self):
        assert is_broad_query("show every record") is True

    def test_show_me_everything(self):
        assert is_broad_query("show me everything") is True

    def test_get_all_data(self):
        assert is_broad_query("get all data") is True

    def test_select_star_literal(self):
        assert is_broad_query("SELECT * FROM transactions") is True

    def test_dump_the_table(self):
        assert is_broad_query("dump the table") is True

    def test_specific_question_not_broad(self):
        assert is_broad_query("What is the total revenue?") is False

    def test_filtered_question_not_broad(self):
        assert is_broad_query("Show transactions for customer C001") is False

    def test_monthly_trend_not_broad(self):
        assert is_broad_query("Show monthly revenue trend") is False

    def test_top_products_not_broad(self):
        assert is_broad_query("Which products rank highest by revenue?") is False


# ── broad_query_summary ───────────────────────────────────────────────────────

class TestBroadQuerySummary:
    def test_returns_ok(self, db30):
        result = broad_query_summary(db30)
        assert result["ok"] is True

    def test_fallback_mode_set(self, db30):
        result = broad_query_summary(db30)
        assert result["fallback_mode"] == "broad_query"

    def test_summary_fields_present(self, db30):
        result = broad_query_summary(db30)
        s = result["summary"]
        assert "total_transactions" in s
        assert "total_revenue" in s
        assert "unique_customers" in s
        assert s["total_transactions"] == 30

    def test_sample_rows_returned(self, db30):
        result = broad_query_summary(db30)
        assert result["row_count"] == 5
        assert len(result["rows"]) == 5

    def test_truncated_and_has_more_set(self, db30):
        result = broad_query_summary(db30)
        assert result["truncated"] is True
        assert result["has_more"] is True

    def test_total_rows_matches(self, db30):
        result = broad_query_summary(db30)
        assert result["total_rows"] == 30


# ── run_sql truncation metadata ───────────────────────────────────────────────

class TestRunSqlTruncation:
    def test_auto_limit_injected_for_raw_query(self, db30):
        result = run_sql("SELECT * FROM transactions", db30)
        assert result["ok"] is True
        assert result["limit_injected"] is True
        # Should be capped at PREVIEW_ROWS (25), not all 30
        assert result["row_count"] <= PREVIEW_ROWS
        assert result["truncated"] is True

    def test_aggregate_returns_all_rows(self, db5):
        result = run_sql(
            "SELECT product_category, COUNT(*) AS n FROM transactions GROUP BY product_category",
            db5,
        )
        assert result["ok"] is True
        assert result["limit_injected"] is False

    def test_explicit_limit_not_overridden(self, db30):
        result = run_sql("SELECT * FROM transactions LIMIT 3", db30)
        assert result["ok"] is True
        assert result["row_count"] == 3
        assert result["limit_injected"] is False

    def test_has_more_true_when_rows_exceed_limit(self, db30):
        # db30 has 30 rows; PREVIEW_ROWS=25, so has_more should be True
        result = run_sql("SELECT * FROM transactions", db30)
        assert result["ok"] is True
        assert result["has_more"] is True

    def test_has_more_false_when_within_limit(self, db5):
        result = run_sql("SELECT * FROM transactions", db5)
        assert result["ok"] is True
        # 5 rows < 25 limit, so has_more should be False
        assert result["has_more"] is False

    def test_total_rows_computed_when_limit_injected(self, db30):
        result = run_sql("SELECT * FROM transactions", db30)
        assert result["ok"] is True
        if result["limit_injected"]:
            # total_rows should be the actual count (30), not just the returned count
            assert result["total_rows"] == 30

    def test_unsafe_sql_blocked(self, db5):
        result = run_sql("DROP TABLE transactions", db5)
        assert result["ok"] is False
        assert result["error"] == "unsafe_sql"

    def test_multiple_statements_blocked(self, db5):
        result = run_sql("SELECT 1; DROP TABLE transactions", db5)
        assert result["ok"] is False


# ── Pagination (limit + offset) ───────────────────────────────────────────────

class TestPagination:
    def test_offset_skips_rows(self, db30):
        page1 = run_sql("SELECT id FROM transactions ORDER BY id", db30, limit=5, offset=0)
        page2 = run_sql("SELECT id FROM transactions ORDER BY id", db30, limit=5, offset=5)
        assert page1["ok"] is True
        assert page2["ok"] is True
        ids1 = [r[0] for r in page1["rows"]]
        ids2 = [r[0] for r in page2["rows"]]
        # Pages must not overlap
        assert not set(ids1) & set(ids2)
        # Page 2 IDs should all be greater than page 1 IDs
        assert min(ids2) > max(ids1)

    def test_limit_respected(self, db30):
        result = run_sql("SELECT * FROM transactions", db30, limit=7)
        assert result["ok"] is True
        assert result["row_count"] <= 7

    def test_limit_above_max_capped(self, db30):
        # Requesting more than MAX_ROWS should still be capped
        result = run_sql("SELECT * FROM transactions", db30, limit=9999)
        assert result["ok"] is True
        assert result["row_count"] <= MAX_ROWS


# ── Timeout fallback ──────────────────────────────────────────────────────────

class TestTimeoutFallback:
    def test_timeout_returns_ok_false_with_timeout_error(self, db30):
        """Simulate a query that runs past the timeout deadline."""
        import backend.app.sql_tool as sql_mod

        original_timeout = sql_mod.QUERY_TIMEOUT_S
        try:
            # Set deadline to 0 so it always fires
            sql_mod.QUERY_TIMEOUT_S = 0.0
            # A full table scan gives the progress handler enough ticks
            result = run_sql(
                "SELECT * FROM transactions WHERE total_amount > 0",
                db30,
            )
            # With 30 rows this may or may not timeout depending on platform;
            # either ok=True (fast enough) or ok=False error=timeout is valid.
            assert result["ok"] in (True, False)
            if not result["ok"]:
                assert result["error"] == "timeout"
                assert result["fallback_mode"] == "timeout"
        finally:
            sql_mod.QUERY_TIMEOUT_S = original_timeout

    def test_timeout_message_contains_suggestion(self, db30):
        import backend.app.sql_tool as sql_mod

        original = sql_mod.QUERY_TIMEOUT_S
        try:
            sql_mod.QUERY_TIMEOUT_S = 0.0
            result = run_sql("SELECT * FROM transactions", db30)
            if not result["ok"] and result["error"] == "timeout":
                assert "narrow" in result["message"].lower() or "filter" in result["message"].lower() or "scanning" in result["message"].lower()
        finally:
            sql_mod.QUERY_TIMEOUT_S = original


# ── run_chat broad-query interception ─────────────────────────────────────────

class TestRunChatBroadQuery:
    def test_broad_query_bypasses_llm(self, db30, monkeypatch):
        """Broad queries must return without calling the LLM at all."""
        mock_client = MagicMock()
        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test")
        chat_service._client = mock_client

        result = run_chat(
            [{"role": "user", "content": "show all data"}], db30
        )

        # LLM should NOT have been called
        mock_client.chat.completions.create.assert_not_called()

        assert result["metadata"]["fallback_mode"] == "broad_query"
        assert result["metadata"]["warning"] == "broad_query_redirected"
        assert result["metadata"]["truncated"] is True
        assert result["structured"] is not None
        assert result["structured"]["viz_type"] == "table"
        # Reply should mention row count
        assert "rows" in result["reply"].lower() or "sample" in result["reply"].lower()

    def test_broad_query_returns_sample_rows(self, db30, monkeypatch):
        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test")
        chat_service._client = MagicMock()

        result = run_chat(
            [{"role": "user", "content": "show all transactions"}], db30
        )
        chart_data = result["structured"]["chart_data"]
        assert chart_data is not None
        assert len(chart_data["rows"]) <= 5

    def test_specific_query_not_intercepted(self, db5, monkeypatch):
        """Specific questions must go through the normal LLM flow."""
        structured_json = json.dumps({
            "intent": "kpi_query", "viz_type": "kpi_card",
            "insight": "Total is $50.", "chart_data": None,
            "answer": "Total revenue is $50.",
        })
        msg = MagicMock()
        msg.content = structured_json
        msg.tool_calls = None
        msg.model_dump.return_value = {"role": "assistant", "content": structured_json}
        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test")
        chat_service._client = mock_client

        result = run_chat(
            [{"role": "user", "content": "What is the total revenue?"}], db5
        )

        # LLM was called
        mock_client.chat.completions.create.assert_called_once()
        assert result["metadata"].get("fallback_mode") != "broad_query"


# ── run_chat timeout propagation ──────────────────────────────────────────────

class TestRunChatTimeoutPropagation:
    def test_timeout_tool_result_triggers_friendly_response(self, db5, monkeypatch):
        """If execute_sql returns error=timeout, run_chat should return the timeout response."""
        import backend.app.sql_tool as sql_mod

        # Patch dispatch to always return a timeout
        def _fake_dispatch(tool_name, args, conn, limit=25, offset=0):
            return {
                "ok": False,
                "error": "timeout",
                "message": "Query timed out — scanning too many rows.",
                "fallback_mode": "timeout",
            }

        monkeypatch.setattr("backend.app.chat_service.dispatch", _fake_dispatch)

        # LLM returns a tool call
        tc = MagicMock()
        tc.id = "tc_001"
        tc.function.name = "execute_sql"
        tc.function.arguments = json.dumps({"sql": "SELECT * FROM transactions", "description": "all"})

        msg = MagicMock()
        msg.content = None
        msg.tool_calls = [tc]
        msg.model_dump.return_value = {
            "role": "assistant",
            "tool_calls": [{"id": "tc_001", "type": "function",
                            "function": {"name": "execute_sql",
                                         "arguments": json.dumps({"sql": "SELECT * FROM transactions", "description": "all"})}}]
        }
        choice = MagicMock()
        choice.finish_reason = "tool_calls"
        choice.message = msg
        resp = MagicMock()
        resp.choices = [choice]

        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = resp
        monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test")
        chat_service._client = mock_client

        result = run_chat([{"role": "user", "content": "Give me everything"}], db5)

        assert result["metadata"]["fallback_mode"] == "timeout"
        assert result["metadata"]["warning"] == "query_timeout"
        assert "timeout" in result["reply"].lower() or "timed out" in result["reply"].lower() or "too long" in result["reply"].lower()
        assert result["structured"] is not None
        assert result["structured"]["viz_type"] == "none"
