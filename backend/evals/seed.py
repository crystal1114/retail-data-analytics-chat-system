"""
backend/evals/seed.py

Deterministic in-memory SQLite database for the evaluation suite.

22 transactions spanning 5 customers (C001–C005), 4 products (A–D),
4 categories, 4 payment methods, 4 stores (HI / CA / NY / TX), 2023–2024.

Pre-computed totals used in golden.json assertions:
─────────────────────────────────────────────────────
Grand total revenue       : $1,845.00
Unique customers          : 5
Total transactions        : 22

Customer totals
  C001                    : $605.00  (6 transactions)
  C002                    : $190.00  (4 transactions)
  C003                    : $405.00  (5 transactions)
  C004                    : $228.00  (4 transactions)
  C005                    : $417.00  (3 transactions)

Category revenue
  Electronics (A)         : $930.00
  Books       (B)         : $155.00
  Clothing    (C)         : $376.00
  Home Decor  (D)         : $384.00

Product units sold
  A : 10    B : 8    C : 10    D : 7

Product C avg discount    : 5.83 %  (35/6 over 6 rows)

Payment breakdown
  Cash                    : $603.00
  Credit Card             : $579.00
  PayPal                  : $339.00
  Debit Card              : $324.00

Store revenue
  100 Tech Blvd, HI  (S1): $930.00
  200 Book Lane, CA  (S2): $155.00
  300 Fashion St, NY (S3): $436.00
  400 Home Ave, TX   (S4): $324.00

Monthly revenue
  2023-10 : $40.00
  2023-11 : $160.00
  2023-12 : $19.00
  2024-01 : $280.00
  2024-02 : $95.00
  2024-03 : $473.00
  2024-04 : $198.00
  2024-05 : $213.00
  2024-06 : $115.00
  2024-07 : $252.00

2023 total                : $219.00
2024 total                : $1,626.00
Q1 2024 (Jan–Mar)         : $848.00
Busiest month             : 2024-03  ($473.00)

Top customers by spend    : C001, C005, C003, C004, C002
Top products by revenue   : A, D, C, B
Top stores by revenue     : HI(S1), NY(S3), TX(S4), CA(S2)
─────────────────────────────────────────────────────
"""

from __future__ import annotations

import sqlite3

_S1 = "100 Tech Blvd\nHonolulu, HI 96801"
_S2 = "200 Book Lane\nLos Angeles, CA 90001"
_S3 = "300 Fashion St\nNew York, NY 10001"
_S4 = "400 Home Ave\nAustin, TX 73301"

# fmt: off
# (customer_id, product_id, quantity, price, transaction_date,
#  payment_method, store_location, product_category, discount_pct, total_amount)
ROWS: list[tuple] = [
    # ── C001 ─ 6 rows ──────────────────────────────────────────────────────
    ("C001", "A", 2, 100.00, "1/5/2024 9:00",    "Cash",        _S1, "Electronics", 10.0, 180.00),
    ("C001", "B", 1,  20.00, "2/10/2024 10:00",  "Credit Card", _S2, "Books",        5.0,  19.00),
    ("C001", "C", 3,  40.00, "3/15/2024 14:00",  "PayPal",      _S3, "Clothing",     0.0, 120.00),
    ("C001", "A", 1, 100.00, "4/20/2024 11:00",  "Cash",        _S1, "Electronics", 10.0,  90.00),
    ("C001", "D", 2,  60.00, "5/25/2024 16:00",  "Debit Card",  _S4, "Home Decor",  20.0,  96.00),
    ("C001", "A", 1, 100.00, "11/10/2023 10:00", "Cash",        _S1, "Electronics",  0.0, 100.00),
    # ── C002 ─ 4 rows ──────────────────────────────────────────────────────
    ("C002", "B", 2,  20.00, "1/8/2024 13:00",   "PayPal",      _S2, "Books",        0.0,  40.00),
    ("C002", "C", 1,  40.00, "2/14/2024 9:00",   "Cash",        _S3, "Clothing",    10.0,  36.00),
    ("C002", "A", 1, 100.00, "6/1/2024 15:00",   "Credit Card", _S1, "Electronics",  5.0,  95.00),
    ("C002", "B", 1,  20.00, "12/5/2023 14:00",  "PayPal",      _S2, "Books",        5.0,  19.00),
    # ── C003 ─ 5 rows ──────────────────────────────────────────────────────
    ("C003", "D", 1,  60.00, "1/12/2024 10:00",  "Debit Card",  _S4, "Home Decor",   0.0,  60.00),
    ("C003", "C", 2,  40.00, "3/20/2024 11:00",  "PayPal",      _S3, "Clothing",    15.0,  68.00),
    ("C003", "B", 3,  20.00, "5/5/2024 14:00",   "Cash",        _S2, "Books",        5.0,  57.00),
    ("C003", "A", 2, 100.00, "7/10/2024 16:00",  "Credit Card", _S1, "Electronics", 10.0, 180.00),
    ("C003", "C", 1,  40.00, "10/20/2023 11:00", "Cash",        _S3, "Clothing",     0.0,  40.00),
    # ── C004 ─ 4 rows ──────────────────────────────────────────────────────
    ("C004", "C", 1,  40.00, "2/5/2024 9:00",    "Cash",        _S3, "Clothing",     0.0,  40.00),
    ("C004", "D", 2,  60.00, "4/15/2024 13:00",  "Debit Card",  _S4, "Home Decor",  10.0, 108.00),
    ("C004", "B", 1,  20.00, "6/20/2024 10:00",  "PayPal",      _S2, "Books",        0.0,  20.00),
    ("C004", "D", 1,  60.00, "11/15/2023 9:00",  "Debit Card",  _S4, "Home Decor",   0.0,  60.00),
    # ── C005 ─ 3 rows ──────────────────────────────────────────────────────
    ("C005", "A", 3, 100.00, "3/5/2024 8:00",    "Credit Card", _S1, "Electronics",  5.0, 285.00),
    ("C005", "D", 1,  60.00, "5/10/2024 12:00",  "Cash",        _S3, "Home Decor",   0.0,  60.00),
    ("C005", "C", 2,  40.00, "7/25/2024 17:00",  "PayPal",      _S3, "Clothing",    10.0,  72.00),
]
# fmt: on

CREATE_SQL = """
CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT    NOT NULL,
    product_id       TEXT    NOT NULL,
    quantity         INTEGER,
    price            REAL,
    transaction_date TEXT,
    payment_method   TEXT,
    store_location   TEXT,
    product_category TEXT,
    discount_pct     REAL,
    total_amount     REAL
);
"""


def build_db() -> sqlite3.Connection:
    """Return a seeded in-memory SQLite connection ready for eval runs."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_SQL)
    conn.executemany(
        "INSERT INTO transactions VALUES (NULL,?,?,?,?,?,?,?,?,?,?)",
        ROWS,
    )
    conn.commit()
    return conn
