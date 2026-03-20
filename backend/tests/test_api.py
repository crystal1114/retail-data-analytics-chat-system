# backend/tests/test_api.py
"""
C. API endpoint tests using FastAPI's TestClient.

Tests:
- GET /api/health
- GET /api/customers/{id}
- GET /api/products/{id}
- GET /api/metrics/{metric_name}
- POST /api/chat
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app import db as db_module

# ── In-memory test DB ─────────────────────────────────────────────────────────────

_SEED_SQL = """
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
  (1,'CTEST','A',2,50.0,'1/15/2024 10:00','Cash','Store1\nCity1','Electronics',10.0,90.0),
  (2,'CTEST','B',1,30.0,'2/20/2024 11:00','PayPal','Store2\nCity2','Books',5.0,28.5);
"""

@pytest.fixture(autouse=True)
def override_db():
    """Replace get_db dependency with an in-memory test connection."""
    test_conn = sqlite3.connect(":memory:", check_same_thread=False)
    test_conn.row_factory = sqlite3.Row
    test_conn.executescript(_SEED_SQL)
    test_conn.commit()

    def fake_get_db():
        yield test_conn

    app.dependency_overrides[db_module.get_db] = fake_get_db
    yield
    app.dependency_overrides.clear()
    test_conn.close()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ── Health ────────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_status_ok(self, client: TestClient):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "database" in body
        assert "openai_configured" in body


# ── Customers ─────────────────────────────────────────────────────────────────────

class TestCustomerEndpoint:
    def test_existing_customer(self, client: TestClient):
        resp = client.get("/api/customers/CTEST")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["customer_id"] == "CTEST"
        assert data["transaction_count"] == 2
        assert "recent_purchases" in data
        assert len(data["recent_purchases"]) > 0

    def test_missing_customer_404(self, client: TestClient):
        resp = client.get("/api/customers/NOONE")
        assert resp.status_code == 404

    def test_customer_has_spend(self, client: TestClient):
        resp = client.get("/api/customers/CTEST")
        total = resp.json()["data"]["total_spend"]
        assert abs(total - 118.5) < 0.01


# ── Products ──────────────────────────────────────────────────────────────────────

class TestProductEndpoint:
    def test_existing_product(self, client: TestClient):
        resp = client.get("/api/products/A")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        data = body["data"]
        assert data["product_id"] == "A"
        assert "stores" in data

    def test_case_insensitive(self, client: TestClient):
        resp_lower = client.get("/api/products/a")
        resp_upper = client.get("/api/products/A")
        assert resp_lower.status_code == resp_upper.status_code

    def test_missing_product_404(self, client: TestClient):
        resp = client.get("/api/products/Z")
        assert resp.status_code == 404

    def test_product_fields(self, client: TestClient):
        resp = client.get("/api/products/B")
        data = resp.json()["data"]
        for field in ("product_id", "transaction_count", "total_revenue", "stores"):
            assert field in data, f"Missing field: {field}"


# ── Metrics ───────────────────────────────────────────────────────────────────────

class TestMetricsEndpoint:
    def test_overall_kpis(self, client: TestClient):
        resp = client.get("/api/metrics/overall_kpis")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert "total_revenue" in data
        assert "total_transactions" in data

    def test_revenue_by_category(self, client: TestClient):
        resp = client.get("/api/metrics/revenue_by_category")
        assert resp.status_code == 200

    def test_monthly_revenue(self, client: TestClient):
        resp = client.get("/api/metrics/monthly_revenue")
        assert resp.status_code == 200

    def test_invalid_metric_400(self, client: TestClient):
        resp = client.get("/api/metrics/exec_sql_now")
        assert resp.status_code == 400

    def test_all_valid_metrics(self, client: TestClient):
        from backend.app.repository import METRIC_ALLOWLIST
        for name in METRIC_ALLOWLIST:
            resp = client.get(f"/api/metrics/{name}")
            assert resp.status_code == 200, f"Metric '{name}' returned {resp.status_code}"


# ── Chat ──────────────────────────────────────────────────────────────────────────

class TestChatEndpoint:
    def test_no_api_key_returns_graceful_reply(self, client: TestClient):
        from backend.app import chat_service
        with patch.object(type(chat_service.settings), "openai_configured",
                          new_callable=PropertyMock, return_value=False):
            resp = client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": "Hello"}]},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "reply" in body
        assert body["reply"] != ""

    def test_chat_with_mocked_openai(self, client: TestClient):
        from backend.app import chat_service

        # Build a mock that immediately returns a stop response
        mock_client = MagicMock()
        msg = MagicMock()
        msg.content = "There are 2 transactions in the test DB."
        msg.tool_calls = []
        msg.model_dump.return_value = {"role": "assistant", "content": msg.content}
        choice = MagicMock()
        choice.finish_reason = "stop"
        choice.message = msg
        mock_resp = MagicMock()
        mock_resp.choices = [choice]
        mock_client.chat.completions.create.return_value = mock_resp

        with patch("backend.app.chat_service.OpenAI", return_value=mock_client), \
             patch.object(type(chat_service.settings), "openai_configured",
                          new_callable=PropertyMock, return_value=True), \
             patch.object(chat_service.settings, "openai_api_key", "sk-fake"):

            resp = client.post(
                "/api/chat",
                json={"messages": [{"role": "user", "content": "How many transactions?"}]},
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "reply" in body
        assert "tool_results" in body
        assert "metadata" in body

    def test_chat_requires_messages(self, client: TestClient):
        resp = client.post("/api/chat", json={})
        assert resp.status_code == 422   # Validation error

    def test_chat_empty_messages_rejected(self, client: TestClient):
        resp = client.post("/api/chat", json={"messages": []})
        assert resp.status_code == 422
