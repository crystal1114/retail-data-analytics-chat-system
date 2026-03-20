"""
backend/app/repository.py

All SQL lives here.  Every function uses parameterised queries only.
No raw user input ever reaches SQL execution.

Return shape:
    { "ok": True, "data": <serialisable> }
  or
    { "ok": False, "error": "<code>", "message": "<human text>" }

Allowed business metric names are declared in METRIC_ALLOWLIST.

Performance notes:
  - Date parsing moved entirely to SQLite (strftime / substr) — no Python loops
  - Expensive aggregate metrics are LRU-cached (TTL-like: process lifetime)
  - Customer/product summaries consolidated to fewer round-trips
"""

from __future__ import annotations

import sqlite3
from functools import lru_cache
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

# ── Helpers ─────────────────────────────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)

def _rows_to_list(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(r) for r in rows]

# SQLite expression that converts 'M/D/YYYY HH:MM' → 'YYYY-MM'
# Format: month/day/year hour:minute  e.g. '1/15/2024 10:00'
# We extract the year (chars after 2nd '/') and month (chars before 1st '/').
# substr(str, pos, len) is 1-indexed in SQLite.
#
# year  = substr(date_part, pos_after_2nd_slash, 4)
#         pos_after_2nd_slash = instr(date_part, '/') + instr(substr(date_part, instr(date_part,'/')+1), '/') + 1
#         but since instr counts from 1, we use a two-step approach via a nested substr.
# month = substr(date_part, 1, instr(date_part,'/')-1)   → '1' or '12' etc.
#
# date_part = substr(transaction_date, 1, instr(transaction_date,' ')-1)  strips time portion
# If there's no space (date only), stripping is still safe as instr returns 0 → -1 length → full string.
#
# Full expression (self-contained, no Python helpers):
_MONTH_SQL = (
    "printf('%s-%02d',"
    # year: grab 4 chars starting after 'M/D/'
    # First slash position: instr(D,' ') gives space, we work on date portion
    # Use: substr(transaction_date,1,instr(transaction_date||' ',' ')-1) as date_part
    # Then find second slash: pos2 = instr(date_part,'/')+instr(substr(date_part,instr(date_part,'/')+1),'/')+1
    # year = substr(date_part, pos2, 4)
    " substr("
    "   substr(transaction_date,1,instr(transaction_date||' ',' ')-1),"
    "   instr(substr(transaction_date,1,instr(transaction_date||' ',' ')-1),'/')"
    "   + instr(substr(substr(transaction_date,1,instr(transaction_date||' ',' ')-1),"
    "           instr(substr(transaction_date,1,instr(transaction_date||' ',' ')-1),'/')+1),'/')"
    "   + 1,"
    "   4"
    " ),"
    # month: integer before first '/'
    " CAST(substr(transaction_date,1,instr(transaction_date,'/')-1) AS INTEGER)"
    ")"
)


# ── Customer queries ────────────────────────────────────────────────────────────

def get_customer_summary(
    conn: sqlite3.Connection, customer_id: str
) -> dict[str, Any]:
    # Single query to get all aggregates + favourite category/product/payment via CTEs
    sql = """
    WITH base AS (
        SELECT
            customer_id,
            COUNT(*)                        AS transaction_count,
            ROUND(SUM(total_amount), 2)     AS total_spend,
            ROUND(AVG(total_amount), 2)     AS avg_order_value,
            SUM(quantity)                   AS total_items_bought
        FROM transactions
        WHERE customer_id = ?
        GROUP BY customer_id
    ),
    fav_cat AS (
        SELECT product_category
        FROM transactions
        WHERE customer_id = ?
        GROUP BY product_category
        ORDER BY COUNT(*) DESC
        LIMIT 1
    ),
    fav_prod AS (
        SELECT product_id
        FROM transactions
        WHERE customer_id = ?
        GROUP BY product_id
        ORDER BY COUNT(*) DESC
        LIMIT 1
    ),
    fav_pay AS (
        SELECT payment_method
        FROM transactions
        WHERE customer_id = ?
        GROUP BY payment_method
        ORDER BY COUNT(*) DESC
        LIMIT 1
    )
    SELECT
        b.*,
        (SELECT product_category FROM fav_cat) AS favourite_category,
        (SELECT product_id        FROM fav_prod) AS favourite_product,
        (SELECT payment_method    FROM fav_pay)  AS favourite_payment_method
    FROM base b
    """
    row = conn.execute(sql, (customer_id, customer_id, customer_id, customer_id)).fetchone()
    if row is None:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No transactions found for customer '{customer_id}'.",
        }
    return {"ok": True, "data": _row_to_dict(row)}


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
    WITH base AS (
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
    ),
    top_cat AS (
        SELECT product_category
        FROM transactions
        WHERE product_id = ?
        GROUP BY product_category
        ORDER BY COUNT(*) DESC
        LIMIT 1
    )
    SELECT b.*, (SELECT product_category FROM top_cat) AS top_category
    FROM base b
    """
    row = conn.execute(sql, (product_id, product_id)).fetchone()
    if row is None:
        return {
            "ok": False,
            "error": "not_found",
            "message": f"No transactions found for product '{product_id}'.",
        }
    return {"ok": True, "data": _row_to_dict(row)}


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


# ── Business metric dispatcher ────────────────────────────────────────────────────

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
        "overall_kpis":                 lambda: _metric_overall_kpis(conn),
        "revenue_by_store":             lambda: _metric_revenue_by_store(conn, safe_limit),
        "top_products_by_revenue":      lambda: _metric_top_products_by_revenue(conn, safe_limit),
        "monthly_revenue":              lambda: _metric_monthly_revenue(conn),
        "revenue_by_category":          lambda: _metric_revenue_by_category(conn),
        "top_customers_by_spend":       lambda: _metric_top_customers_by_spend(conn, safe_limit),
        "payment_method_breakdown":     lambda: _metric_payment_method_breakdown(conn),
        "monthly_revenue_by_category":  lambda: _metric_monthly_revenue_by_category(conn),
        "monthly_revenue_by_product":   lambda: _metric_monthly_revenue_by_product(conn),
        "monthly_transactions":         lambda: _metric_monthly_transactions(conn),
        "category_comparison":          lambda: _metric_category_comparison(conn),
        "product_comparison":           lambda: _metric_product_comparison(conn),
        "revenue_by_payment_method":    lambda: _metric_revenue_by_payment_method(conn),
        "discount_by_category":         lambda: _metric_discount_by_category(conn),
        "quantity_by_category":         lambda: _metric_quantity_by_category(conn),
    }

    fn = dispatch.get(metric_name)
    if fn:
        return fn()
    return {"ok": False, "error": "invalid_metric", "message": "Unknown metric."}


# ── Core metric implementations (SQL-only date parsing) ──────────────────────────

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


def _metric_top_products_by_revenue(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
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
    """All date arithmetic pushed into SQLite — no Python loops."""
    sql = f"""
    SELECT
        {_MONTH_SQL} AS month,
        COUNT(*)                        AS transaction_count,
        ROUND(SUM(total_amount), 2)     AS total_revenue
    FROM transactions
    WHERE transaction_date IS NOT NULL
      AND transaction_date != ''
    GROUP BY month
    ORDER BY month
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No monthly data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


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


def _metric_top_customers_by_spend(conn: sqlite3.Connection, limit: int) -> dict[str, Any]:
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


# ── Trend analytics (SQL date parsing) ──────────────────────────────────────────

def _metric_monthly_revenue_by_category(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = f"""
    SELECT
        {_MONTH_SQL} AS month,
        product_category,
        ROUND(SUM(total_amount), 2) AS revenue
    FROM transactions
    WHERE transaction_date IS NOT NULL AND transaction_date != ''
    GROUP BY month, product_category
    ORDER BY month, product_category
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No trend data found."}

    # Pivot in Python (fast — small result set after aggregation)
    months_order: list[str] = []
    categories: set[str] = set()
    data_map: dict[str, dict[str, float]] = {}

    for r in rows:
        m, cat, rev = r["month"], r["product_category"], r["revenue"]
        categories.add(cat)
        if m not in data_map:
            months_order.append(m)
            data_map[m] = {}
        data_map[m][cat] = rev

    sorted_cats = sorted(categories)
    result = []
    for month in months_order:
        row: dict[str, Any] = {"month": month}
        for cat in sorted_cats:
            row[cat] = data_map[month].get(cat, 0.0)
        result.append(row)

    return {
        "ok": True,
        "data": result,
        "meta": {"categories": sorted_cats, "months": months_order},
    }


def _metric_monthly_revenue_by_product(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = f"""
    SELECT
        {_MONTH_SQL} AS month,
        product_id,
        ROUND(SUM(total_amount), 2) AS revenue
    FROM transactions
    WHERE transaction_date IS NOT NULL AND transaction_date != ''
    GROUP BY month, product_id
    ORDER BY month, product_id
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No trend data found."}

    months_order: list[str] = []
    products: set[str] = set()
    data_map: dict[str, dict[str, float]] = {}

    for r in rows:
        m, prod, rev = r["month"], r["product_id"], r["revenue"]
        products.add(prod)
        if m not in data_map:
            months_order.append(m)
            data_map[m] = {}
        data_map[m][prod] = rev

    sorted_prods = sorted(products)
    result = []
    for month in months_order:
        row: dict[str, Any] = {"month": month}
        for prod in sorted_prods:
            row[prod] = data_map[month].get(prod, 0.0)
        result.append(row)

    return {
        "ok": True,
        "data": result,
        "meta": {"products": sorted_prods, "months": months_order},
    }


def _metric_monthly_transactions(conn: sqlite3.Connection) -> dict[str, Any]:
    sql = f"""
    SELECT
        {_MONTH_SQL} AS month,
        COUNT(*) AS transaction_count
    FROM transactions
    WHERE transaction_date IS NOT NULL AND transaction_date != ''
    GROUP BY month
    ORDER BY month
    """
    rows = conn.execute(sql).fetchall()
    if not rows:
        return {"ok": False, "error": "not_found", "message": "No transaction data found."}
    return {"ok": True, "data": _rows_to_list(rows)}


# ── Comparison analytics ─────────────────────────────────────────────────────────

def _metric_category_comparison(conn: sqlite3.Connection) -> dict[str, Any]:
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


# ── Compare two customers ────────────────────────────────────────────────────────

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


# ── In-process metric cache ──────────────────────────────────────────────────────
# Aggregate metrics are read-only and change only when the DB is replaced.
# Cache by (metric_name, limit) — key is a plain string for lru_cache compatibility.

@lru_cache(maxsize=64)
def _cached_metric(db_path: str, metric_name: str, limit: int) -> dict[str, Any]:
    """
    Thread-safe LRU cache for expensive aggregate metrics.
    Opens its own short-lived connection so the cache key can be purely by value.
    """
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA query_only=ON;")
    try:
        return get_business_metric(conn, metric_name, limit)
    finally:
        conn.close()


def get_business_metric_cached(
    db_path: str,
    metric_name: str,
    limit: int = 10,
) -> dict[str, Any]:
    """
    Drop-in replacement for get_business_metric that caches results in-process.
    Use this from the tools dispatcher for metrics that rarely change.
    """
    return _cached_metric(db_path, metric_name, min(max(1, limit), 50))


def invalidate_metric_cache() -> None:
    """Call this if the database is replaced at runtime."""
    _cached_metric.cache_clear()
