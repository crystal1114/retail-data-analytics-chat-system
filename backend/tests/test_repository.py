"""
backend/tests/test_repository.py  (repurposed as test_sql_tool.py content)

Unit tests for the sql_tool module — safety validation and query execution.
"""

from __future__ import annotations

import sqlite3
import pytest
from backend.app.sql_tool import run_sql, _validate_sql, dispatch


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
            ("C001", "C", 4, 20.0, "3/10/2024 8:00",  "Cash",   "S1", "Clothing",     0.0, 80.00),
        ],
    )
    conn.commit()
    yield conn
    conn.close()


# ── _validate_sql ─────────────────────────────────────────────────────────────

class TestValidateSql:
    def test_select_ok(self):
        assert _validate_sql("SELECT 1") is None

    def test_select_with_whitespace_ok(self):
        assert _validate_sql("  \n  SELECT id FROM transactions") is None

    def test_insert_blocked(self):
        assert _validate_sql("INSERT INTO transactions VALUES (1)") is not None

    def test_update_blocked(self):
        assert _validate_sql("UPDATE transactions SET price=0") is not None

    def test_delete_blocked(self):
        assert _validate_sql("DELETE FROM transactions") is not None

    def test_drop_blocked(self):
        assert _validate_sql("DROP TABLE transactions") is not None

    def test_create_blocked(self):
        assert _validate_sql("CREATE TABLE foo (id INT)") is not None

    def test_pragma_write_blocked(self):
        assert _validate_sql("PRAGMA journal_mode=WAL") is not None

    def test_multiple_statements_blocked(self):
        assert _validate_sql("SELECT 1; DROP TABLE transactions") is not None

    def test_comment_stripped_before_check(self):
        # SELECT hidden after a comment that looks like another keyword
        assert _validate_sql("-- INSERT comment\nSELECT 1") is None

    def test_non_select_with_comment_blocked(self):
        assert _validate_sql("-- SELECT\nINSERT INTO foo VALUES (1)") is not None


# ── run_sql ───────────────────────────────────────────────────────────────────

class TestRunSql:
    def test_count_all(self, db):
        r = run_sql("SELECT COUNT(*) AS n FROM transactions", db)
        assert r["ok"] is True
        assert r["rows"][0][0] == 3

    def test_sum_revenue(self, db):
        r = run_sql("SELECT ROUND(SUM(total_amount), 2) AS total FROM transactions", db)
        assert r["ok"] is True
        assert r["rows"][0][0] == pytest.approx(198.50, 0.01)

    def test_filter_by_customer(self, db):
        r = run_sql("SELECT COUNT(*) FROM transactions WHERE customer_id='C001'", db)
        assert r["ok"] is True
        assert r["rows"][0][0] == 2

    def test_group_by_payment(self, db):
        r = run_sql(
            "SELECT payment_method, COUNT(*) AS n FROM transactions GROUP BY payment_method ORDER BY n DESC",
            db,
        )
        assert r["ok"] is True
        assert r["columns"] == ["payment_method", "n"]
        assert r["rows"][0][0] == "Cash"
        assert r["rows"][0][1] == 2

    def test_nonexistent_table_returns_error(self, db):
        r = run_sql("SELECT * FROM nonexistent", db)
        assert r["ok"] is False
        assert r["error"] == "sql_error"

    def test_write_blocked_at_executor_level(self, db):
        r = run_sql("INSERT INTO transactions (customer_id) VALUES ('X')", db)
        assert r["ok"] is False
        assert r["error"] == "unsafe_sql"

    def test_columns_returned(self, db):
        r = run_sql("SELECT customer_id, total_amount FROM transactions LIMIT 1", db)
        assert r["ok"] is True
        assert r["columns"] == ["customer_id", "total_amount"]

    def test_row_count_in_result(self, db):
        r = run_sql("SELECT * FROM transactions", db)
        assert r["ok"] is True
        assert r["row_count"] == 3


# ── dispatch ──────────────────────────────────────────────────────────────────

class TestDispatch:
    def test_execute_sql_dispatched(self, db):
        r = dispatch("execute_sql", {"sql": "SELECT COUNT(*) FROM transactions", "description": "test"}, db)
        assert r["ok"] is True

    def test_unknown_tool(self, db):
        r = dispatch("totally_unknown_tool", {}, db)
        assert r["ok"] is False
        assert r["error"] == "unknown_tool"

    def test_missing_sql_arg(self, db):
        r = dispatch("execute_sql", {"description": "no sql"}, db)
        assert r["ok"] is False
