"""
backend/app/tools.py

Definitions of the callable tools exposed to the LLM via OpenAI function calling.

Each entry in TOOL_DEFINITIONS is an OpenAI-compatible function schema.
The mapping TOOL_HANDLER_MAP connects tool names to repository callables.
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
                        "description": (
                            "The numeric customer ID (e.g. '109318'). "
                            "Do not guess—ask the user if unknown."
                        ),
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
                "ordered by transaction date descending. Each row includes "
                "product_id, category, quantity, price, discount, total_amount, "
                "date, payment method, and store location."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id": {
                        "type": "string",
                        "description": "The numeric customer ID.",
                    },
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
                "average discount percentage, unique customers count, and store count. "
                "Valid product IDs are: A, B, C, D."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {
                        "type": "string",
                        "description": (
                            "The product ID (one of: A, B, C, D). "
                            "Do not guess—ask the user if unknown."
                        ),
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
                "Returns a list of store locations that sell a specific product, "
                "with per-store transaction count and total revenue. "
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
            "name": "get_business_metric",
            "description": (
                "Returns a structured business analytics metric. "
                "Use this for aggregate or trend questions about the business. "
                f"Available metrics: {sorted(METRIC_ALLOWLIST)}. "
                "Descriptions: "
                "'overall_kpis' – total revenue, transactions, quantity, unique customers/products; "
                "'revenue_by_store' – revenue ranked by store location; "
                "'top_products_by_revenue' – products ranked by revenue; "
                "'monthly_revenue' – revenue trend by calendar month; "
                "'revenue_by_category' – revenue broken down by product category; "
                "'top_customers_by_spend' – highest-spending customers; "
                "'payment_method_breakdown' – transaction share by payment method."
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
                "Returns a side-by-side comparison of two customers' statistics. "
                "Requires both customer IDs."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_id_a": {
                        "type": "string",
                        "description": "First customer's numeric ID.",
                    },
                    "customer_id_b": {
                        "type": "string",
                        "description": "Second customer's numeric ID.",
                    },
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
    """
    Routes a tool call to the correct repository function.

    Args:
        tool_name: One of the names in TOOL_DEFINITIONS.
        tool_args: Parsed JSON arguments from the LLM.
        conn:      Open SQLite connection.

    Returns:
        Repository result dict with 'ok' key.
    """
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
