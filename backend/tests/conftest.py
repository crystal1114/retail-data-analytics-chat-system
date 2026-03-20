# backend/tests/conftest.py
"""
Shared pytest fixtures.

All tests use an in-memory SQLite database seeded with a small, known dataset
so no external files are required.
"""

from __future__ import annotations

import sqlite3
import pytest

# ── Seed data ────────────────────────────────────────────────────────────────────
#
# Customers: "C001", "C002"
# Products:  "A", "B"
# Categories: "Electronics", "Books"
#

SEED_SQL = """
CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT NOT NULL,
    product_id       TEXT NOT NULL,
    quantity         INTEGER,
    price            REAL,
    transaction_date TEXT,
    payment_method   TEXT,
    store_location   TEXT,
    product_category TEXT,
    discount_pct     REAL,
    total_amount     REAL
);

INSERT INTO transactions VALUES
  (1, 'C001', 'A', 2, 50.00, '1/15/2024 10:00', 'Cash',        '123 Main St\nSpringfield, IL', 'Electronics', 10.0, 90.00),
  (2, 'C001', 'B', 1, 30.00, '2/20/2024 11:00', 'Credit Card', '456 Oak Ave\nShelbyville, IL', 'Books',       5.0,  28.50),
  (3, 'C001', 'A', 3, 50.00, '3/10/2024 09:00', 'Cash',        '123 Main St\nSpringfield, IL', 'Electronics', 10.0, 135.00),
  (4, 'C002', 'B', 2, 30.00, '1/22/2024 14:00', 'PayPal',      '789 Elm Rd\nCapital City, IL', 'Books',       0.0,  60.00),
  (5, 'C002', 'A', 1, 50.00, '4/5/2024 16:00',  'Debit Card',  '456 Oak Ave\nShelbyville, IL', 'Electronics', 15.0, 42.50);
"""


@pytest.fixture
def db() -> sqlite3.Connection:
    """Return an in-memory SQLite connection seeded with test data."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SEED_SQL)
    conn.commit()
    yield conn
    conn.close()
