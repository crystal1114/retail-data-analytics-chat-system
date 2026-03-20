# backend/tests/test_repository.py
"""
A. Repository unit tests

Covers:
- get_customer_summary  (found / not found)
- get_customer_purchases (found / not found)
- get_product_summary   (found / not found)
- get_product_stores    (found / not found)
- get_business_metric   (all metrics / invalid metric / empty variants)
- compare_customers     (both found / one missing)
"""

from __future__ import annotations

import sqlite3
import pytest

from backend.app.repository import (
    METRIC_ALLOWLIST,
    compare_customers,
    get_business_metric,
    get_customer_purchases,
    get_customer_summary,
    get_product_stores,
    get_product_summary,
)


# ── Customer summary ─────────────────────────────────────────────────────────────

class TestGetCustomerSummary:
    def test_found(self, db: sqlite3.Connection):
        result = get_customer_summary(db, "C001")
        assert result["ok"] is True
        data = result["data"]
        assert data["customer_id"] == "C001"
        assert data["transaction_count"] == 3
        assert abs(data["total_spend"] - 253.50) < 0.01
        assert data["favourite_product"] == "A"
        assert data["favourite_payment_method"] == "Cash"

    def test_not_found(self, db: sqlite3.Connection):
        result = get_customer_summary(db, "NOBODY")
        assert result["ok"] is False
        assert result["error"] == "not_found"
        assert "NOBODY" in result["message"]

    def test_favourite_category(self, db: sqlite3.Connection):
        result = get_customer_summary(db, "C001")
        # C001 has 2 'A' (Electronics) vs 1 'B' (Books) → Electronics
        assert result["data"]["favourite_category"] == "Electronics"


# ── Customer purchases ────────────────────────────────────────────────────────────

class TestGetCustomerPurchases:
    def test_found(self, db: sqlite3.Connection):
        result = get_customer_purchases(db, "C001")
        assert result["ok"] is True
        assert len(result["data"]) == 3

    def test_limit_respected(self, db: sqlite3.Connection):
        result = get_customer_purchases(db, "C001", limit=1)
        assert result["ok"] is True
        assert len(result["data"]) == 1

    def test_limit_capped_at_100(self, db: sqlite3.Connection):
        # Limit > 100 should be capped silently
        result = get_customer_purchases(db, "C001", limit=9999)
        assert result["ok"] is True

    def test_not_found(self, db: sqlite3.Connection):
        result = get_customer_purchases(db, "GHOST")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    def test_columns_present(self, db: sqlite3.Connection):
        result = get_customer_purchases(db, "C001")
        row = result["data"][0]
        for col in ("product_id", "quantity", "total_amount", "transaction_date"):
            assert col in row, f"Column '{col}' missing from purchase row"


# ── Product summary ───────────────────────────────────────────────────────────────

class TestGetProductSummary:
    def test_found(self, db: sqlite3.Connection):
        result = get_product_summary(db, "A")
        assert result["ok"] is True
        data = result["data"]
        assert data["product_id"] == "A"
        assert data["transaction_count"] == 3     # rows 1, 3, 5
        assert data["total_units_sold"] == 6       # 2+3+1
        assert abs(data["total_revenue"] - 267.50) < 0.01  # 90+135+42.50
        assert data["top_category"] == "Electronics"

    def test_not_found(self, db: sqlite3.Connection):
        result = get_product_summary(db, "Z")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    def test_avg_discount(self, db: sqlite3.Connection):
        result = get_product_summary(db, "B")
        data = result["data"]
        # B discounts: 5.0, 0.0 → avg = 2.5
        assert abs(data["avg_discount_pct"] - 2.5) < 0.01


# ── Product stores ────────────────────────────────────────────────────────────────

class TestGetProductStores:
    def test_found(self, db: sqlite3.Connection):
        result = get_product_stores(db, "A")
        assert result["ok"] is True
        stores = result["data"]
        # Rows 1, 3 → Springfield; row 5 → Shelbyville
        locations = [s["store_location"] for s in stores]
        assert any("Springfield" in loc for loc in locations)
        assert any("Shelbyville" in loc for loc in locations)

    def test_not_found(self, db: sqlite3.Connection):
        result = get_product_stores(db, "X")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    def test_ordered_by_revenue(self, db: sqlite3.Connection):
        result = get_product_stores(db, "A")
        revenues = [s["total_revenue"] for s in result["data"]]
        assert revenues == sorted(revenues, reverse=True)


# ── Business metrics ──────────────────────────────────────────────────────────────

class TestGetBusinessMetric:
    def test_invalid_metric(self, db: sqlite3.Connection):
        result = get_business_metric(db, "hacker_drop_table")
        assert result["ok"] is False
        assert result["error"] == "invalid_metric"

    def test_overall_kpis(self, db: sqlite3.Connection):
        result = get_business_metric(db, "overall_kpis")
        assert result["ok"] is True
        data = result["data"]
        assert data["total_transactions"] == 5
        assert data["unique_customers"] == 2
        assert data["unique_products"] == 2
        assert abs(data["total_revenue"] - 356.00) < 0.01  # 90+28.5+135+60+42.5

    def test_revenue_by_category(self, db: sqlite3.Connection):
        result = get_business_metric(db, "revenue_by_category")
        assert result["ok"] is True
        rows = result["data"]
        categories = {r["product_category"] for r in rows}
        assert "Electronics" in categories
        assert "Books" in categories

    def test_monthly_revenue(self, db: sqlite3.Connection):
        result = get_business_metric(db, "monthly_revenue")
        assert result["ok"] is True
        months = [r["month"] for r in result["data"]]
        assert "2024-01" in months
        assert "2024-02" in months

    def test_top_products_by_revenue(self, db: sqlite3.Connection):
        result = get_business_metric(db, "top_products_by_revenue")
        assert result["ok"] is True
        top = result["data"][0]["product_id"]
        assert top == "A"   # A has more revenue than B

    def test_revenue_by_store(self, db: sqlite3.Connection):
        result = get_business_metric(db, "revenue_by_store")
        assert result["ok"] is True
        assert len(result["data"]) > 0

    def test_top_customers_by_spend(self, db: sqlite3.Connection):
        result = get_business_metric(db, "top_customers_by_spend")
        assert result["ok"] is True
        assert result["data"][0]["customer_id"] == "C001"

    def test_payment_method_breakdown(self, db: sqlite3.Connection):
        result = get_business_metric(db, "payment_method_breakdown")
        assert result["ok"] is True
        methods = {r["payment_method"] for r in result["data"]}
        assert "Cash" in methods

    def test_all_allowed_metrics_succeed(self, db: sqlite3.Connection):
        """Every metric in METRIC_ALLOWLIST must return ok=True."""
        for name in METRIC_ALLOWLIST:
            result = get_business_metric(db, name)
            assert result["ok"] is True, f"Metric '{name}' returned ok=False"

    def test_limit_parameter(self, db: sqlite3.Connection):
        result = get_business_metric(db, "top_products_by_revenue", limit=1)
        assert result["ok"] is True
        assert len(result["data"]) == 1


# ── Compare customers ─────────────────────────────────────────────────────────────

class TestCompareCustomers:
    def test_both_found(self, db: sqlite3.Connection):
        result = compare_customers(db, "C001", "C002")
        assert result["ok"] is True
        assert "customer_a" in result["data"]
        assert "customer_b" in result["data"]
        assert result["data"]["customer_a"]["customer_id"] == "C001"
        assert result["data"]["customer_b"]["customer_id"] == "C002"

    def test_first_missing(self, db: sqlite3.Connection):
        result = compare_customers(db, "GHOST", "C002")
        assert result["ok"] is False
        assert result["error"] == "not_found"

    def test_second_missing(self, db: sqlite3.Connection):
        result = compare_customers(db, "C001", "GHOST")
        assert result["ok"] is False
        assert result["error"] == "not_found"
