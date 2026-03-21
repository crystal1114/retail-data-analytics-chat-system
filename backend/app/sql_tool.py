"""
backend/app/sql_tool.py

Single tool exposed to the LLM: execute_sql.

The LLM generates a SELECT statement; this module validates it (read-only
guard), executes it against the SQLite DB, and returns the rows as JSON.

Safety rules enforced here (defence-in-depth):
  1. Statement must start with SELECT after stripping comments/whitespace.
  2. Forbidden keywords (write operations) are rejected by regex scan.
  3. Row cap of 500 prevents accidental full-table dumps.
  4. Execution timeout via SQLite's progress handler (1 s).

The dataset is an owned, public retail analytics dataset used for internal
business analytics.  Flexible SQL generation is intentional and appropriate
for this use-case.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

# ── Safety ────────────────────────────────────────────────────────────────────

# Keywords that must never appear in a safe SELECT query
_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|REPLACE|TRUNCATE|ATTACH|DETACH"
    r"|PRAGMA\s+\w+\s*=|VACUUM|REINDEX|ANALYZE)\b",
    re.IGNORECASE,
)

_MAX_ROWS = 500


def _validate_sql(sql: str) -> str | None:
    """
    Returns None if SQL is safe, or an error string describing why it was blocked.
    """
    stripped = sql.strip()

    # Remove single-line comments before checking
    no_comments = re.sub(r"--[^\n]*", "", stripped)
    no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL).strip()

    if not no_comments.upper().startswith("SELECT"):
        return "Only SELECT statements are permitted."

    if _WRITE_KEYWORDS.search(no_comments):
        return "Query contains a forbidden keyword (write/DDL operation)."

    # Block multiple statements (semicolon not at very end)
    inner = no_comments.rstrip(";")
    if ";" in inner:
        return "Multiple statements are not permitted."

    return None  # safe


# ── Executor ──────────────────────────────────────────────────────────────────

def run_sql(sql: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Validate and execute a SELECT query.  Returns a uniform response dict.

    Success:  {"ok": True,  "columns": [...], "rows": [...], "row_count": N}
    Failure:  {"ok": False, "error": "<code>", "message": "<detail>"}
    """
    # 1. Safety check
    err = _validate_sql(sql)
    if err:
        return {"ok": False, "error": "unsafe_sql", "message": err}

    # 2. Timeout via progress handler (~1 s wall-clock)
    deadline = time.monotonic() + 1.0

    def _timeout_check():
        if time.monotonic() > deadline:
            return 1  # non-zero → SQLite raises OperationalError
        return 0

    conn.set_progress_handler(_timeout_check, 1000)

    try:
        cursor = conn.execute(sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(_MAX_ROWS)
        row_list = [list(r) for r in rows]
        truncated = len(row_list) == _MAX_ROWS

        return {
            "ok": True,
            "columns": columns,
            "rows": row_list,
            "row_count": len(row_list),
            "truncated": truncated,
        }

    except sqlite3.OperationalError as exc:
        return {"ok": False, "error": "sql_error", "message": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": "execution_error", "message": str(exc)}
    finally:
        conn.set_progress_handler(None, 0)


# ── OpenAI tool schema ────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT    NOT NULL,   -- e.g. '109318'
    product_id       TEXT    NOT NULL,   -- one of: 'A', 'B', 'C', 'D'
    quantity         INTEGER,
    price            REAL,               -- unit price before discount
    transaction_date TEXT,               -- format: 'M/D/YYYY H:MM'  e.g. '3/11/2024 18:51'
    payment_method   TEXT,               -- 'Cash' | 'Credit Card' | 'Debit Card' | 'PayPal'
    store_location   TEXT,               -- full address string
    product_category TEXT,               -- 'Books' | 'Clothing' | 'Electronics' | 'Home Decor'
    discount_pct     REAL,               -- e.g. 15.94 means 15.94% discount
    total_amount     REAL                -- quantity * price * (1 - discount_pct/100)
);
-- 100,000 rows covering 2023-2024
-- Date parsing: use strftime('%Y-%m', substr(transaction_date, instr(transaction_date,'/')+length(...)...))
-- Easier month extraction: printf('%04d-%02d', ...) or use the helper below
-- Recommended month grouping:
--   printf('%04d-%02d',
--     CAST(substr(transaction_date, instr(transaction_date,'/',-1,2)+1, 4) AS INT),
--     CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT)
--   )
-- Simpler alternative that works reliably for this dataset:
--   substr('0'||CAST(CAST(substr(transaction_date,1,instr(transaction_date,'/')-1) AS INT) AS TEXT),-1)
-- BEST approach for month grouping on this dataset:
--   strftime('%Y-%m', printf('%04d-%02d-%02d',
--     CAST(substr(transaction_date, length(transaction_date)-8) AS INT),
--     CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT),
--     1))
""".strip()

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Execute a read-only SELECT query against the retail transactions database "
                "to answer analytics questions. The database contains 100,000 retail "
                "transactions from 2023-2024.\n\n"
                "IMPORTANT date handling: transaction_date is stored as 'M/D/YYYY H:MM' "
                "(e.g. '3/11/2024 18:51'). To group by month use:\n"
                "  printf('%04d-%02d', "
                "CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT), "
                "CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT))\n"
                "Or simpler: extract year with substr(transaction_date,-13,4) won't work reliably — "
                "use the printf approach above.\n\n"
                "Only SELECT statements are allowed. Results capped at 500 rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": (
                            "A valid SQLite SELECT statement. "
                            "Only SELECT is allowed — no INSERT/UPDATE/DELETE/DROP/CREATE."
                        ),
                    },
                    "description": {
                        "type": "string",
                        "description": "One sentence describing what this query computes.",
                    },
                },
                "required": ["sql", "description"],
            },
        },
    }
]


def dispatch(tool_name: str, tool_args: dict[str, Any], conn: sqlite3.Connection) -> dict[str, Any]:
    """Route tool call from the LLM to the correct handler."""
    if tool_name == "execute_sql":
        sql = tool_args.get("sql", "")
        if not sql:
            return {"ok": False, "error": "missing_argument", "message": "sql is required"}
        return run_sql(sql, conn)
    return {
        "ok": False,
        "error": "unknown_tool",
        "message": f"Tool '{tool_name}' is not registered.",
    }
