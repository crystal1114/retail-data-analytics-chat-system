"""
backend/tests/test_api.py

Integration tests for the FastAPI endpoints using TestClient.
OpenAI is mocked — no live API calls.
"""

from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import backend.app.chat_service as chat_service
from backend.app.main import app
from backend.app.db import get_db


# ── Test DB fixture ───────────────────────────────────────────────────────────

def _make_test_db():
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
        ],
    )
    conn.commit()
    return conn


@pytest.fixture
def client():
    test_conn = _make_test_db()
    app.dependency_overrides[get_db] = lambda: test_conn
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    test_conn.close()


# ── Helper ────────────────────────────────────────────────────────────────────

def _make_stop_response(content: str) -> MagicMock:
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


# ── Health endpoint ───────────────────────────────────────────────────────────

def test_health_endpoint(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "status" in data
    assert "database" in data
    assert "openai_configured" in data


# ── Chat endpoint ─────────────────────────────────────────────────────────────

def test_chat_returns_reply(client, monkeypatch):
    structured = json.dumps({
        "intent": "kpi_query", "viz_type": "kpi_card",
        "insight": "There are 2 transactions.",
        "chart_data": {"kpis": [{"label": "Transactions", "value": "2", "icon": "🛍️"}]},
        "answer": "There are 2 transactions in the test DB.",
    })

    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = _make_stop_response(structured)
    monkeypatch.setattr("backend.app.chat_service.settings.openai_api_key", "sk-test-key")
    chat_service._client = mock_client

    resp = client.post("/api/chat", json={"messages": [{"role": "user", "content": "How many transactions?"}]})
    assert resp.status_code == 200
    data = resp.json()
    assert "reply" in data
    assert "tool_results" in data
    assert "metadata" in data
    assert "There are 2 transactions" in data["reply"]


def test_chat_requires_messages(client):
    resp = client.post("/api/chat", json={})
    assert resp.status_code == 422


def test_chat_empty_messages_rejected(client):
    resp = client.post("/api/chat", json={"messages": []})
    assert resp.status_code == 422


def test_root_endpoint(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "NL" in resp.json().get("message", "") or "Retail" in resp.json().get("message", "")
