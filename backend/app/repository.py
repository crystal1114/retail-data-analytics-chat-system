"""
backend/app/repository.py

All SQL lives here.  Every function uses parameterised queries only.
No raw user input ever reaches SQL execution.

Return shape:
    { "ok": True, "data": <serialisable> }
  or
    { "ok": False, "error": "<code>", "message": "<human text>" }

Allowed business metric names are declared in METRIC_ALLOWLIST.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# ── Constants ───────────────────────────────────────────────────────────────────

METRIC_ALLOWLIST: frozenset[str] = frozenset(
    {
        "overall_kpis",
        "revenue_by_store",
        "top_products_by_revenue",
        "monthly_revenue",
        "revenue_by_category",
        "top_customers_by_spend",
        "payment_method_breakdown",
    }
)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ── Customer queries ────────────────────────────────────────────────────────────

def get_customer_summary(
    conn: sqlite3.Connection, customer_id: str
) -> dict[str, Any]:
    """
    Returns high-level stats for a single customer.

    Args:
        conn:        Open SQLite connection.
        customer_id: The customer ID string (e.g. "109318").

    Returns:
        ok=True  → data with transaction_count, total_spend, avg_order_value,
                   favourite_category, favourite_product, favourite_payment_method.
        ok=False → error="not_found" if customer does not exist.
    """
    sql = """
    SELECT
        customer_id,
        COUNT(*)                        AS transaction_count,
        ROUND(SUM(total_amount), 2)     AS total_spend,
        ROUND(AVG(total_amount), 2)     AS avg_order_value,
        SUM(quantity)                   AS total_items_bought
    FROM transactions
    WHERE customer_id = ?
    GROUP BY customer_id
    """
    row = conn.execute(sql, (customer_id,)).fetchone()
    if row is None:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No transactions found for customer '{customer_id}'.",
        }

    data = _row_to_dict(row)

    # Favourite category
    cat_row = conn.execute(
        """
        SELECT product_category, COUNT(*) AS cnt
        FROM transactions WHERE customer_id = ?
        GROUP BY product_category ORDER BY cnt DESC LIMIT 1
        """,
        (customer_id,),
    ).fetchone()
    data["favourite_category"] = cat_row["product_category"] if cat_row else None

    # Favourite product
    prod_row = conn.execute(
        """
        SELECT product_id, COUNT(*) AS cnt
        FROM transactions WHERE customer_id = ?
        GROUP BY product_id ORDER BY cnt DESC LIMIT 1
        """,
        (customer_id,),
    ).fetchone()
    data["favourite_product"] = prod_row["product_id"] if prod_row else None

    # Favourite payment method
    pay_row = conn.execute(
        """
        SELECT payment_method, COUNT(*) AS cnt
        FROM transactions WHERE customer_id = ?
        GROUP BY payment_method ORDER BY cnt DESC LIMIT 1
        """,
        (customer_id,),
    ).fetchone()
    data["favourite_payment_method"] = pay_row["payment_method"] if pay_row else None

    return {"ok": True, "data": data}


def get_customer_purchases(
    conn: sqlite3.Connection, customer_id: str, limit: int = 20
) -> dict[str, Any]:
    """
    Returns a paginated list of recent purchases for a customer.

    Args:
        conn:        Open SQLite connection.
        customer_id: The customer ID string.
        limit:       Maximum number of rows to return (default 20, max 100).

    Returns:
        ok=True  → data as a list of purchase dicts.
        ok=False → error="not_found".
    """
    safe_limit = min(max(1, limit), 100)
    sql = """
    SELECT
        product_id,
        product_category,
        quantity,
        ROUND(price, 2)        AS price,
        ROUND(discount_pct, 2) AS discount_pct,
        ROUND(total_amount, 2) AS total_amount,
        transaction_date,
        payment_method,
        store_location
    FROM transactions
    WHERE customer_id = ?
    ORDER BY transaction_date DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (customer_id, safe_limit)).fetchall()
    if not rows:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No purchases found for customer '{customer_id}'.",
        }
    return {"ok": True, "data": _rows_to_list(rows)}


# ── Product queries ──────────────────────────────────────────────────────────────

def get_product_summary(
    conn: sqlite3.Connection, product_id: str
) -> dict[str, Any]:
    """
    Returns aggregate stats for a single product.

    Args:
        conn:       Open SQLite connection.
        product_id: The product ID (e.g. "A", "B", "C", "D").

    Returns:
        ok=True  → data with transaction_count, total_units_sold, total_revenue,
                   avg_price, avg_discount_pct, top_category.
        ok=False → error="not_found".
    """
    sql = """
    SELECT
        product_id,
        COUNT(*)                        AS transaction_count,
        SUM(quantity)                   AS total_units_sold,
        ROUND(SUM(total_amount), 2)     AS total_revenue,
        ROUND(AVG(price), 2)            AS avg_price,
        ROUND(AVG(discount_pct), 4)     AS avg_discount_pct,
        COUNT(DISTINCT customer_id)     AS unique_customers,
        COUNT(DISTINCT store_location)  AS store_count
    FROM transactions
    WHERE product_id = ?
    GROUP BY product_id
    """
    row = conn.execute(sql, (product_id,)).fetchone()
    if row is None:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No transactions found for product '{product_id}'.",
        }

    data = _row_to_dict(row)

    # Top category for this product
    cat_row = conn.execute(
        """
        SELECT product_category, COUNT(*) AS cnt
        FROM transactions WHERE product_id = ?
        GROUP BY product_category ORDER BY cnt DESC LIMIT 1
        """,
        (product_id,),
    ).fetchone()
    data["top_category"] = cat_row["product_category"] if cat_row else None

    return {"ok": True, "data": data}


def get_product_stores(
    conn: sqlite3.Connection, product_id: str
) -> dict[str, Any]:
    """
    Returns the list of stores that carry a product, with per-store stats.

    Args:
        conn:       Open SQLite connection.
        product_id: The product ID.

    Returns:
        ok=True  → data as a list of {store_location, transaction_count, total_revenue}.
        ok=False → error="not_found".
    """
    sql = """
    SELECT
        store_location,
        COUNT(*)                    AS transaction_count,
        ROUND(SUM(total_amount), 2) AS total_revenue
    FROM transactions
    WHERE product_id = ?
    GROUP BY store_location
    ORDER BY total_revenue DESC
    """
    rows = conn.execute(sql, (product_id,)).fetchall()
    if not rows:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No stores found for product '{product_id}'.",
        }
    return {"ok": True, "data": _rows_to_list(rows)}


# ── Business metric queries ──────────────────────────────────────────────────────

def get_business_metric(
    conn: sqlite3.Connection,
    metric_name: str,
    limit: int = 10,
) -> dict[str, Any]:
    """
    Returns a structured business metric.

    Args:
        conn:        Open SQLite connection.
        metric_name: One of METRIC_ALLOWLIST.
        limit:       For ranked metrics, top-N (default 10).

    Returns:
        ok=True  → data structure depends on metric_name.
        ok=False → error="invalid_metric" or "not_found".
    """
    if metric_name not in METRIC_ALLOWLIST:
        return {
            "ok": False,
            "error": "invalid_metric",
            "message": (
                f"Unknown metric '{metric_name}'. "
                f"Allowed values: {sorted(METRIC_ALLOWLIST)}"
            ),
        }

    safe_limit = min(max(1, limit), 50)

    if metric_name == "overall_kpis":
        return _metric_overall_kpis(conn)
    elif metric_name == "revenue_by_store":
        return _metric_revenue_by_store(conn, safe_limit)
    elif metric_name == "top_products_by_revenue":
        return _metric_top_products_by_revenue(conn, safe_limit)
    elif metric_name == "monthly_revenue":
        return _metric_monthly_revenue(conn)
    elif metric_name == "revenue_by_category":
        return _metric_revenue_by_category(conn)
    elif metric_name == "top_customers_by_spend":
        return _metric_top_customers_by_spend(conn, safe_limit)
    elif metric_name == "payment_method_breakdown":
        return _metric_payment_method_breakdown(conn)

    # Should never reach here due to allowlist check above
    return {"ok": False, "error": "invalid_metric", "message": "Unknown metric."}


def _metric_overall_kpis(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = """
    SELECT
        ROUND(SUM(total_amount), 2)    AS total_revenue,
        COUNT(*)                        AS total_transactions,
        SUM(quantity)                   AS total_quantity_sold,
        COUNT(DISTINCT customer_id)     AS unique_customers,
        COUNT(DISTINCT product_id)      AS unique_products,
        ROUND(AVG(total_amount), 2)     AS avg_transaction_value,
        ROUND(AVG(discount_pct), 4)     AS avg_discount_pct
    FROM transactions
    """
    row = conn.execute(sql).fetchone()
    return {"ok": True, "data": _row_to_dict(row)}


def _metric_revenue_by_store(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
    sql = """
    SELECT
        store_location,
        COUNT(*)                    AS transaction_count,
        ROUND(SUM(total_amount), 2) AS total_revenue,
        COUNT(DISTINCT customer_id) AS unique_customers
    FROM transactions
    GROUP BY store_location
    ORDER BY total_revenue DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No store data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_top_products_by_revenue(
    conn: sqlite3.Connection, limit: int
) -> dict[str, Any]:
    sql = """
    SELECT
        product_id,
        product_category,
        COUNT(*)                    AS transaction_count,
        SUM(quantity)               AS total_units_sold,
        ROUND(SUM(total_amount), 2) AS total_revenue
    FROM transactions
    GROUP BY product_id
    ORDER BY total_revenue DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No product data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_monthly_revenue(conn: sqlite3.Connection) -> dict[str, Any]:
    # transaction_date can be "12/26/2023 12:32" or "8/5/2023 0:00"
    # SQLite's strftime needs ISO format; we build YYYY-MM via substr tricks
    sql = """
    SELECT
        SUBSTR(
            PRINTF('%04d-%02d',
                CAST(SUBSTR(transaction_date, LENGTH(transaction_date) - INSTR(REVERSE(transaction_date), '/') - 3, 4) AS INT),
                CAST(
                    CASE
                        WHEN INSTR(transaction_date, '/') = 2
                        THEN SUBSTR(transaction_date, 1, 1)
                        ELSE SUBSTR(transaction_date, 1, 2)
                    END
                AS INT)
            ), 1, 7
        ) AS month,
        COUNT(*)                    AS transaction_count,
        ROUND(SUM(total_amount), 2) AS total_revenue
    FROM transactions
    WHERE transaction_date IS NOT NULL
    GROUP BY month
    ORDER BY month
    """
    # Simpler approach: parse in Python after fetching
    rows_raw = conn.execute(
        """
        SELECT transaction_date, total_amount
        FROM transactions
        WHERE transaction_date IS NOT NULL
        """
    ).fetchall()

    from collections import defaultdict

    monthly: dict[str, dict] = defaultdict(lambda: {"transaction_count": 0, "total_revenue": 0.0})

    for r in rows_raw:
        date_str = r["transaction_date"]
        amount = r["total_amount"] or 0.0
        try:
            # Format: M/D/YYYY HH:MM or M/D/YYYY H:MM
            date_part = date_str.split(" ")[0]
            parts = date_part.split("/")
            month_key = f"{parts[2]}-{int(parts[0]):02d}"
        except (IndexError, ValueError):
            continue
        monthly[month_key]["transaction_count"] += 1
        monthly[month_key]["total_revenue"] += amount

    result = [
        {
            "month": k,
            "transaction_count": v["transaction_count"],
            "total_revenue": round(v["total_revenue"], 2),
        }
        for k, v in sorted(monthly.items())
    ]

    if not result:
        return {"ok": False, "error": "not_found", "message": "No monthly data found."}
    return {"ok": True, "data": result}


def _metric_revenue_by_category(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = """
    SELECT
        product_category,
        COUNT(*)                    AS transaction_count,
        SUM(quantity)               AS total_units_sold,
        ROUND(SUM(total_amount), 2) AS total_revenue,
        ROUND(AVG(discount_pct), 4) AS avg_discount_pct
    FROM transactions
    GROUP BY product_category
    ORDER BY total_revenue DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No category data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_top_customers_by_spend(
    conn: sqlite3.Connection, limit: int
) -> dict[str, Any]:
    sql = """
    SELECT
        customer_id,
        COUNT(*)                    AS transaction_count,
        ROUND(SUM(total_amount), 2) AS total_spend,
        ROUND(AVG(total_amount), 2) AS avg_order_value
    FROM transactions
    GROUP BY customer_id
    ORDER BY total_spend DESC
    LIMIT ?
    """
    rows = conn.execute(sql, (limit,)).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No customer data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_payment_method_breakdown(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = """
    SELECT
        payment_method,
        COUNT(*)                    AS transaction_count,
        ROUND(SUM(total_amount), 2) AS total_revenue,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2) AS pct_of_transactions
    FROM transactions
    GROUP BY payment_method
    ORDER BY transaction_count DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No payment data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


# ── Stretch: compare two customers ──────────────────────────────────────────────

def compare_customers(
    conn: sqlite3.Connection, customer_id_a: str, customer_id_b: str
) -> dict[str, Any]:
    """
    Returns side-by-side stats for two customers.

    Returns:
        ok=True  → data with both customers' summaries.
        ok=False → error="not_found" if either customer is missing.
    """
    a = get_customer_summary(conn, customer_id_a)
    b = get_customer_summary(conn, customer_id_b)

    if not a["ok"]:
        return a
    if not b["ok"]:
        return b

    return {
        "ok": True,
        "data": {
            "customer_a": a["data"],
            "customer_b": b["data"],
        },
    }
