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
    store_location   TEXT,               -- multi-line: street\\nCity, STATE ZIP  e.g. '123 Main St\\nSpringfield, IL 62701'
                                         -- STATE is a 2-letter US abbreviation: HI=Hawaii, CA=California, NY=New York, TX=Texas, etc.
                                         -- NEVER filter by full state name — always use the 2-letter code e.g. LIKE '%, HI %'
    product_category TEXT,               -- 'Books' | 'Clothing' | 'Electronics' | 'Home Decor'
    discount_pct     REAL,               -- e.g. 15.94 means 15.94% discount
    total_amount     REAL                -- quantity * price * (1 - discount_pct/100)
);
-- 100,000 rows covering 2023-2024

-- ══ DATE HELPERS (transaction_date is 'M/D/YYYY H:MM', NOT ISO — strftime() needs conversion) ══

-- Extract year:   CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT)
-- Extract month:  CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT)
-- Extract day:    CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)

-- ISO date string (required before ANY strftime call):
--   printf('%04d-%02d-%02d',
--     CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),
--     CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT),
--     CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)
--   )  →  e.g. '2024-03-11'

-- Month label (GROUP BY month):
--   printf('%04d-%02d',
--     CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),
--     CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT))

-- Day of week (0=Sunday … 6=Saturday):
--   strftime('%w', printf('%04d-%02d-%02d',
--     CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),
--     CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT),
--     CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)))

-- Day name CASE:
--   CASE strftime('%w', <iso_expr>)
--     WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
--     WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday' WHEN '5' THEN 'Friday'
--     WHEN '6' THEN 'Saturday' END

-- ══ CANONICAL EXAMPLE QUERIES ══

-- Transactions by day of week (all 7 days, alias AFTER expression):
--   SELECT
--     CASE strftime('%w', printf('%04d-%02d-%02d',
--         CAST(substr(transaction_date,instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1,4) AS INT),
--         CAST(substr(transaction_date,1,instr(transaction_date,'/')-1) AS INT),
--         CAST(substr(transaction_date,instr(transaction_date,'/')+1,instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT)))
--       WHEN '0' THEN 'Sunday' WHEN '1' THEN 'Monday' WHEN '2' THEN 'Tuesday'
--       WHEN '3' THEN 'Wednesday' WHEN '4' THEN 'Thursday'
--       WHEN '5' THEN 'Friday'   WHEN '6' THEN 'Saturday' END AS day_name,
--     COUNT(*) AS tx_count
--   FROM transactions GROUP BY day_name ORDER BY tx_count DESC

-- Revenue by store in Hawaii (aggregate in one query, never raw rows):
--   SELECT store_location, ROUND(SUM(total_amount),2) AS revenue
--   FROM transactions WHERE store_location LIKE '%, HI %'
--   GROUP BY store_location ORDER BY revenue DESC LIMIT 20
""".strip()

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": (
                "Execute a read-only SELECT query against the retail transactions database.\n\n"

                "⚠️ CRITICAL — transaction_date is stored as 'M/D/YYYY H:MM' (e.g. '3/11/2024 18:51').\n"
                "strftime() only works on ISO dates. You MUST convert first using:\n"
                "  ISO expr: printf('%04d-%02d-%02d',\n"
                "    CAST(substr(transaction_date, instr(transaction_date,'/')+instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')+1, 4) AS INT),\n"
                "    CAST(substr(transaction_date, 1, instr(transaction_date,'/')-1) AS INT),\n"
                "    CAST(substr(transaction_date, instr(transaction_date,'/')+1, instr(substr(transaction_date,instr(transaction_date,'/')+1),'/')-1) AS INT))\n\n"

                "Day of week: strftime('%w', <ISO_EXPR>) → '0'=Sunday … '6'=Saturday\n"
                "Month label: printf('%04d-%02d', YEAR_INT, MONTH_INT)\n\n"

                "⚠️ CRITICAL — store_location format is 'Street\\nCity, STATE ZIP'.\n"
                "STATE is always a 2-letter US abbreviation. NEVER use full state names.\n"
                "  Hawaii   → LIKE '%, HI %'\n"
                "  California → LIKE '%, CA %'\n"
                "  New York   → LIKE '%, NY %'\n\n"

                "Only SELECT statements are allowed. Results capped at 500 rows."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "sql": {
                        "type": "string",
                        "description": "A valid SQLite SELECT statement.",
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
