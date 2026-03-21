"""
backend/tests/test_integration.py

Live integration tests — require a real OPENAI_API_KEY.
Skipped automatically when the key is absent or invalid.

These tests verify the full NL→SQL→Answer pipeline end-to-end.
"""

from __future__ import annotations

import os
import sqlite3

import pytest

from backend.app.chat_service import run_chat


@pytest.fixture
def db():
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
            ("C001", "A", 2, 50.0, "1/15/2024 10:00", "Cash",   "S1", "Electronics", 10.0, 90.00),
            ("C002", "B", 1, 30.0, "2/20/2024 11:00", "PayPal", "S2", "Books",        5.0, 28.50),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def require_openai_key():
    key = os.getenv("OPENAI_API_KEY", "")
    if not key or key.startswith("sk-placeholder"):
        pytest.skip("OPENAI_API_KEY not set — skipping live integration tests")


@pytest.mark.integration
class TestLiveOpenAI:
    def test_total_revenue_query(self, db):
        result = run_chat(
            [{"role": "user", "content": "What is the total revenue?"}], db
        )
        assert result["reply"]
        assert not result["metadata"].get("error")
        # Should have used execute_sql
        assert any(tr["tool"] == "execute_sql" for tr in result["tool_results"])

    def test_payment_breakdown(self, db):
        result = run_chat(
            [{"role": "user", "content": "Show payment method breakdown"}], db
        )
        assert result["reply"]
        assert not result["metadata"].get("error")

    def test_unsafe_request_handled(self, db):
        result = run_chat(
            [{"role": "user", "content": "Delete all data from the database"}], db
        )
        # Should not crash; SQL guard should block any write attempt
        assert result["reply"]
