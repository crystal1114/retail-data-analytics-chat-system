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
from collections import defaultdict
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
        # NEW analytics metrics
        "monthly_revenue_by_category",
        "monthly_revenue_by_product",
        "monthly_transactions",
        "category_comparison",
        "product_comparison",
        "revenue_by_payment_method",
        "discount_by_category",
        "quantity_by_category",
    }
)


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]


# ── Date parsing helper ─────────────────────────────────────────────────────────

def _parse_month(date_str: str) -> str | None:
    """Parse 'M/D/YYYY HH:MM' → 'YYYY-MM'. Returns None on failure."""
    try:
        date_part = date_str.split(" ")[0]
        parts = date_part.split("/")
        return f"{parts[2]}-{int(parts[0]):02d}"
    except (IndexError, ValueError, AttributeError):
        return None


# ── Customer queries ────────────────────────────────────────────────────────────

def get_customer_summary(
    conn: sqlite3.Connection, customer_id: str
) -> dict[str, Any]:
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

    dispatch = {
        "overall_kpis": lambda: _metric_overall_kpis(conn),
        "revenue_by_store": lambda: _metric_revenue_by_store(conn, safe_limit),
        "top_products_by_revenue": lambda: _metric_top_products_by_revenue(conn, safe_limit),
        "monthly_revenue": lambda: _metric_monthly_revenue(conn),
        "revenue_by_category": lambda: _metric_revenue_by_category(conn),
        "top_customers_by_spend": lambda: _metric_top_customers_by_spend(conn, safe_limit),
        "payment_method_breakdown": lambda: _metric_payment_method_breakdown(conn),
        "monthly_revenue_by_category": lambda: _metric_monthly_revenue_by_category(conn),
        "monthly_revenue_by_product": lambda: _metric_monthly_revenue_by_product(conn),
        "monthly_transactions": lambda: _metric_monthly_transactions(conn),
        "category_comparison": lambda: _metric_category_comparison(conn),
        "product_comparison": lambda: _metric_product_comparison(conn),
        "revenue_by_payment_method": lambda: _metric_revenue_by_payment_method(conn),
        "discount_by_category": lambda: _metric_discount_by_category(conn),
        "quantity_by_category": lambda: _metric_quantity_by_category(conn),
    }

    fn = dispatch.get(metric_name)
    if fn:
        return fn()
    return {"ok": False, "error": "invalid_metric", "message": "Unknown metric."}


# ── Core metric implementations ──────────────────────────────────────────────────

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
    rows_raw = conn.execute(
        "SELECT transaction_date, total_amount FROM transactions WHERE transaction_date IS NOT NULL"
    ).fetchall()

    monthly: dict[str, dict] = defaultdict(lambda: {"transaction_count": 0, "total_revenue": 0.0})

    for r in rows_raw:
        month_key = _parse_month(r["transaction_date"])
        if month_key is None:
            continue
        amount = r["total_amount"] or 0.0
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


# ── NEW: Trend analytics ─────────────────────────────────────────────────────────

def _metric_monthly_revenue_by_category(conn: sqlite3.Connection) -> dict[str, Any]:
    """Monthly revenue broken down by category — good for stacked line/area charts."""
    rows_raw = conn.execute(
        """
        SELECT transaction_date, product_category, total_amount
        FROM transactions
        WHERE transaction_date IS NOT NULL
        """
    ).fetchall()

    # Build { month -> { category -> revenue } }
    data_map: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    categories: set[str] = set()

    for r in rows_raw:
        month_key = _parse_month(r["transaction_date"])
        if month_key is None:
            continue
        cat = r["product_category"] or "Unknown"
        categories.add(cat)
        data_map[month_key][cat] += r["total_amount"] or 0.0

    sorted_months = sorted(data_map.keys())
    sorted_cats = sorted(categories)

    result = []
    for month in sorted_months:
        row: dict[str, Any] = {"month": month}
        for cat in sorted_cats:
            row[cat] = round(data_map[month].get(cat, 0.0), 2)
        result.append(row)

    if not result:
        return {"ok": False, "error": "not_found", "message": "No trend data found."}

    return {
        "ok": True,
        "data": result,
        "meta": {"categories": sorted_cats, "months": sorted_months},
    }


def _metric_monthly_revenue_by_product(conn: sqlite3.Connection) -> dict[str, Any]:
    """Monthly revenue broken down by product (A/B/C/D)."""
    rows_raw = conn.execute(
        """
        SELECT transaction_date, product_id, total_amount
        FROM transactions
        WHERE transaction_date IS NOT NULL
        """
    ).fetchall()

    data_map: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    products: set[str] = set()

    for r in rows_raw:
        month_key = _parse_month(r["transaction_date"])
        if month_key is None:
            continue
        prod = r["product_id"] or "Unknown"
        products.add(prod)
        data_map[month_key][prod] += r["total_amount"] or 0.0

    sorted_months = sorted(data_map.keys())
    sorted_prods = sorted(products)

    result = []
    for month in sorted_months:
        row: dict[str, Any] = {"month": month}
        for prod in sorted_prods:
            row[prod] = round(data_map[month].get(prod, 0.0), 2)
        result.append(row)

    if not result:
        return {"ok": False, "error": "not_found", "message": "No trend data found."}

    return {
        "ok": True,
        "data": result,
        "meta": {"products": sorted_prods, "months": sorted_months},
    }


def _metric_monthly_transactions(conn: sqlite3.Connection) -> dict[str, Any]:
    """Monthly transaction count trend."""
    rows_raw = conn.execute(
        "SELECT transaction_date FROM transactions WHERE transaction_date IS NOT NULL"
    ).fetchall()

    monthly: dict[str, int] = defaultdict(int)
    for r in rows_raw:
        month_key = _parse_month(r["transaction_date"])
        if month_key:
            monthly[month_key] += 1

    result = [
        {"month": k, "transaction_count": v}
        for k, v in sorted(monthly.items())
    ]

    if not result:
        return {"ok": False, "error": "not_found", "message": "No transaction data found."}
    return {"ok": True, "data": result}


# ── NEW: Comparison analytics ────────────────────────────────────────────────────

def _metric_category_comparison(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full comparison of all categories across multiple KPIs."""
    sql = """
    SELECT
        product_category                              AS category,
        COUNT(*)                                      AS transaction_count,
        SUM(quantity)                                 AS total_units_sold,
        ROUND(SUM(total_amount), 2)                   AS total_revenue,
        ROUND(AVG(total_amount), 2)                   AS avg_order_value,
        ROUND(AVG(discount_pct), 2)                   AS avg_discount_pct,
        COUNT(DISTINCT customer_id)                   AS unique_customers,
        ROUND(100.0 * SUM(total_amount) / SUM(SUM(total_amount)) OVER (), 2) AS revenue_share_pct
    FROM transactions
    GROUP BY product_category
    ORDER BY total_revenue DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No category data."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_product_comparison(conn: sqlite3.Connection) -> dict[str, Any]:
    """Full comparison of all products across multiple KPIs."""
    sql = """
    SELECT
        product_id,
        COUNT(*)                                      AS transaction_count,
        SUM(quantity)                                 AS total_units_sold,
        ROUND(SUM(total_amount), 2)                   AS total_revenue,
        ROUND(AVG(total_amount), 2)                   AS avg_order_value,
        ROUND(AVG(price), 2)                          AS avg_price,
        ROUND(AVG(discount_pct), 2)                   AS avg_discount_pct,
        COUNT(DISTINCT customer_id)                   AS unique_customers,
        ROUND(100.0 * SUM(total_amount) / SUM(SUM(total_amount)) OVER (), 2) AS revenue_share_pct
    FROM transactions
    GROUP BY product_id
    ORDER BY total_revenue DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No product data."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_revenue_by_payment_method(conn: sqlite3.Connection) -> dict[str, Any]:
    """Revenue and transaction share by payment method."""
    sql = """
    SELECT
        payment_method,
        COUNT(*)                                       AS transaction_count,
        ROUND(SUM(total_amount), 2)                    AS total_revenue,
        ROUND(AVG(total_amount), 2)                    AS avg_order_value,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 2)            AS txn_share_pct,
        ROUND(100.0 * SUM(total_amount) / SUM(SUM(total_amount)) OVER (), 2) AS revenue_share_pct
    FROM transactions
    GROUP BY payment_method
    ORDER BY total_revenue DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No payment data."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_discount_by_category(conn: sqlite3.Connection) -> dict[str, Any]:
    """Average discount percentage by category."""
    sql = """
    SELECT
        product_category                AS category,
        ROUND(AVG(discount_pct), 2)     AS avg_discount_pct,
        ROUND(MIN(discount_pct), 2)     AS min_discount_pct,
        ROUND(MAX(discount_pct), 2)     AS max_discount_pct,
        COUNT(*)                        AS transaction_count
    FROM transactions
    GROUP BY product_category
    ORDER BY avg_discount_pct DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No discount data."}
    return {"ok": True, "data": _rows_to_list(rows)}


def _metric_quantity_by_category(conn: sqlite3.Connection) -> dict[str, Any]:
    """Units sold by category."""
    sql = """
    SELECT
        product_category                AS category,
        SUM(quantity)                   AS total_units_sold,
        ROUND(AVG(quantity), 2)         AS avg_quantity_per_txn,
        COUNT(*)                        AS transaction_count
    FROM transactions
    GROUP BY product_category
    ORDER BY total_units_sold DESC
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No quantity data."}
    return {"ok": True, "data": _rows_to_list(rows)}


# ── Stretch: compare two customers ──────────────────────────────────────────────

def compare_customers(
    conn: sqlite3.Connection, customer_id_a: str, customer_id_b: str
) -> dict[str, Any]:
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
