# backend/tests/test_integration.py
"""
D. Optional integration test – requires OPENAI_API_KEY to be set.

Marked with @pytest.mark.integration so they are skipped in CI unless
the API key is present.

Run with:
    pytest -m integration
"""

from __future__ import annotations

import os
import sqlite3
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def db() -> sqlite3.Connection:
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
          (1,'C001','A',2,50.0,'1/15/2024 10:00','Cash',
           '123 Main St\nSpringfield, IL','Electronics',10.0,90.0),
          (2,'C002','B',1,30.0,'2/20/2024 11:00','PayPal',
           '456 Oak Ave\nShelbyville, IL','Books',5.0,28.5);
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def require_openai_key():
    """Skip all integration tests if OPENAI_API_KEY is not configured."""
    if not os.getenv("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set – skipping integration tests")


class TestLiveOpenAI:
    def test_customer_query(self, db: sqlite3.Connection):
        from backend.app.chat_service import run_chat
        result = run_chat(
            [{"role": "user", "content": "Tell me about customer C001"}],
            db,
        )
        assert result["reply"]
        assert not result["metadata"].get("error")

    def test_business_metric_query(self, db: sqlite3.Connection):
        from backend.app.chat_service import run_chat
        result = run_chat(
            [{"role": "user", "content": "What is the total revenue?"}],
            db,
        )
        assert result["reply"]
        # Should have called get_business_metric
        tool_names = [t["tool"] for t in result["tool_results"]]
        assert "get_business_metric" in tool_names

    def test_missing_customer_graceful(self, db: sqlite3.Connection):
        from backend.app.chat_service import run_chat
        result = run_chat(
            [{"role": "user", "content": "Tell me about customer DOESNOTEXIST99999"}],
            db,
        )
        assert result["reply"]
        # Model should not crash even if data is not found
        assert "error" not in result["metadata"] or result["metadata"]["error"] is None
