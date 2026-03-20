"""
backend/app/tools.py

Definitions of the callable tools exposed to the LLM via OpenAI function calling.
"""

from __future__ import annotations

from typing import Any
import sqlite3

from .repository import (
    METRIC_ALLOWLIST,
    compare_customers,
    get_business_metric,
    get_customer_purchases,
    get_customer_summary,
    get_product_stores,
    get_product_summary,
)

# ── OpenAI tool schemas ──────────────────────────────────────────────────────────

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_customer_summary",
            "description": (
                "Returns high-level statistics for a specific retail customer: "
                "transaction count, total spend, average order value, favourite "
                "product category, favourite product, and favourite payment method."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The numeric customer ID (e.g. '109318'). Do not guess.",
                    }
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_customer_purchases",
            "description": (
                "Returns a list of recent purchases for a specific customer, "
                "ordered by transaction date descending."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {"type": "string", "description": "The numeric customer ID."},
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of purchases to return (1–100, default 20).",
                        "default": 20,
                    },
                },
                "required": ["customer_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_summary",
            "description": (
                "Returns aggregate statistics for a specific product: "
                "transaction count, total units sold, total revenue, average price, "
                "average discount, unique customers, store count. "
                "Valid product IDs are: A, B, C, D."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": "The product ID (one of: A, B, C, D).",
                    }
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_product_stores",
            "description": (
                "Returns a list of store locations that sell a specific product "
                "with per-store transaction count and revenue."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "The product ID (A, B, C, or D)."}
                },
                "required": ["product_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_business_metric",
            "description": (
                "Returns structured business analytics metric. "
                "Use for aggregate, trend, comparison, ranking, and distribution questions. "
                "\n\nAvailable metrics:\n"
                "- overall_kpis: Total revenue, transactions, customers, products, avg values\n"
                "- monthly_revenue: Revenue trend by calendar month (line chart)\n"
                "- monthly_transactions: Transaction count trend by month (line chart)\n"
                "- monthly_revenue_by_category: Monthly revenue per category (multi-line chart)\n"
                "- monthly_revenue_by_product: Monthly revenue per product A/B/C/D (multi-line chart)\n"
                "- revenue_by_category: Revenue, units, discount per category (bar/pie chart)\n"
                "- category_comparison: Full KPI comparison across all categories\n"
                "- product_comparison: Full KPI comparison across all products\n"
                "- top_products_by_revenue: Products ranked by revenue (horizontal bar)\n"
                "- top_customers_by_spend: Top spending customers (horizontal bar)\n"
                "- revenue_by_store: Stores ranked by revenue (horizontal bar)\n"
                "- payment_method_breakdown: Transaction and revenue share by payment method (pie)\n"
                "- revenue_by_payment_method: Revenue comparison by payment method (bar)\n"
                "- discount_by_category: Average discount rates by category (bar)\n"
                "- quantity_by_category: Units sold by category (bar)\n"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "metric_name": {
                        "type": "string",
                        "enum": sorted(METRIC_ALLOWLIST),
                        "description": "The metric to retrieve.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "For ranked metrics, how many rows to return (default 10).",
                        "default": 10,
                    },
                },
                "required": ["metric_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_customers",
            "description": (
                "Returns a side-by-side comparison of two customers' statistics."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id_a": {"type": "string", "description": "First customer's numeric ID."},
                    "customer_id_b": {"type": "string", "description": "Second customer's numeric ID."},
                },
                "required": ["customer_id_a", "customer_id_b"],
            },
        },
    },
]

# ── Tool name → handler mapping ──────────────────────────────────────────────────

def dispatch_tool(
    tool_name: str,
    tool_args: dict[str, Any],
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    try:
        if tool_name == "get_customer_summary":
            return get_customer_summary(conn, tool_args["customer_id"])

        elif tool_name == "get_customer_purchases":
            return get_customer_purchases(
                conn,
                tool_args["customer_id"],
                limit=tool_args.get("limit", 20),
            )

        elif tool_name == "get_product_summary":
            return get_product_summary(conn, tool_args["product_id"])

        elif tool_name == "get_product_stores":
            return get_product_stores(conn, tool_args["product_id"])

        elif tool_name == "get_business_metric":
            return get_business_metric(
                conn,
                tool_args["metric_name"],
                limit=tool_args.get("limit", 10),
            )

        elif tool_name == "compare_customers":
            return compare_customers(
                conn,
                tool_args["customer_id_a"],
                tool_args["customer_id_b"],
            )

        else:
            return {
                "ok": False,
                "error": "unknown_tool",
                "message": f"Tool '{tool_name}' is not registered.",
            }

    except KeyError as exc:
        return {
            "ok": False,
            "error": "missing_argument",
            "message": f"Required argument missing for tool '{tool_name}': {exc}",
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "dispatch_error",
            "message": f"Error executing tool '{tool_name}': {exc}",
        }
