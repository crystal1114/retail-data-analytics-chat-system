"""
backend/app/sql_tool.py

Single tool exposed to the LLM: execute_sql.

Safety & performance rules (defence-in-depth):
  1. SELECT-only guard — write/DDL keywords are rejected.
  2. Broad-query detection — "show all data / all transactions" is intercepted
     before SQL runs; a summary+sample is returned instead.
  3. Auto-LIMIT injection — non-aggregate queries that lack a LIMIT clause
     have LIMIT {PREVIEW_ROWS} appended automatically.
  4. Hard row cap — fetchmany(MAX_ROWS) prevents accidental full-table dumps.
  5. Graceful timeout — SQLite progress handler aborts queries > QUERY_TIMEOUT_S
     and returns a friendly narrowing suggestion.
  6. Rich metadata — every result includes truncated, total_rows (when known),
     has_more, fallback_mode so callers can render pagination/warnings.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

# ── Tuneable limits ──────────────────────────────────────────────────────────

# Default rows shown in a table preview
PREVIEW_ROWS = 25
# Hard ceiling — never send more than this to the LLM
MAX_ROWS = 100
# Seconds before a query is killed
QUERY_TIMEOUT_S = 3.0

# ── Safety ────────────────────────────────────────────────────────────────────

_WRITE_KEYWORDS = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|REPLACE\s+INTO|TRUNCATE|ATTACH|DETACH"
    r"|PRAGMA\s+\w+\s*=|VACUUM|REINDEX|ANALYZE)\b",
    re.IGNORECASE,
)

# Detects queries that contain GROUP BY / aggregate functions → safe to return
# more rows; queries WITHOUT these are raw row dumps that need a LIMIT guard.
# Note: no trailing \b because ( is not a word character.
_AGGREGATE_PATTERN = re.compile(
    r"\b(GROUP\s+BY|HAVING)|\b(COUNT|SUM|AVG|MIN|MAX)\s*\(",
    re.IGNORECASE,
)

# Broad-query patterns that should be redirected to summary+sample mode.
# Only matches unqualified "all data / all transactions" requests.  A qualifier
# between "all" and the noun (e.g. "all Electronics transactions") means the
# user wants a filtered result, not a full dump, so it must NOT match.
_BROAD_QUERY_PATTERNS = re.compile(
    r"\b(show\s+(all|every|the\s+entire|all\s+the)\s+(data|transactions?|records?|rows?|table)\b"
    r"|show\s+me\s+(all|the\s+full)\s+(the\s+)?(data|transactions?|records?|rows?|table)\b"
    r"|show\s+me\s+everything\b"
    r"|get\s+all\s+(data|transactions?|records?|rows?)\b"
    r"|select\s+\*\s+from\s+transactions\s*;?\s*$"
    r"|dump\s+(the\s+)?(data|table|transactions)\b"
    r"|everything\s+in\s+(the\s+)?(database|table|transactions)\b"
    r")",
    re.IGNORECASE | re.DOTALL,
)

# Detects if the SQL already has a top-level LIMIT clause
_HAS_LIMIT = re.compile(r"\bLIMIT\s+\d+", re.IGNORECASE)


def _validate_sql(sql: str) -> str | None:
    """Returns None if SQL is safe, or an error string if it should be blocked."""
    stripped = sql.strip()
    no_comments = re.sub(r"--[^\n]*", "", stripped)
    no_comments = re.sub(r"/\*.*?\*/", "", no_comments, flags=re.DOTALL).strip()

    first_keyword = no_comments.upper().split()[0] if no_comments.split() else ""
    if first_keyword not in ("SELECT", "WITH"):
        return "Only SELECT statements (including CTEs) are permitted."
    if _WRITE_KEYWORDS.search(no_comments):
        return "Query contains a forbidden keyword (write/DDL operation)."
    inner = no_comments.rstrip(";")
    if ";" in inner:
        return "Multiple statements are not permitted."
    return None


def _is_aggregate_query(sql: str) -> bool:
    """True when the SQL computes aggregates (safe to return more rows)."""
    return bool(_AGGREGATE_PATTERN.search(sql))


def _inject_limit(sql: str, limit: int = PREVIEW_ROWS) -> tuple[str, bool]:
    """
    If SQL is a raw-row query without a LIMIT, append LIMIT {limit}.
    Returns (possibly_modified_sql, was_injected).
    """
    if _is_aggregate_query(sql):
        return sql, False
    if _HAS_LIMIT.search(sql):
        return sql, False
    # Strip trailing semicolon before adding LIMIT
    clean = sql.rstrip().rstrip(";")
    return f"{clean}\nLIMIT {limit}", True


def is_broad_query(user_text: str) -> bool:
    """True when the user's natural-language message is a broad data dump request."""
    return bool(_BROAD_QUERY_PATTERNS.search(user_text))


# ── Executor ──────────────────────────────────────────────────────────────────

def run_sql(
    sql: str,
    conn: sqlite3.Connection,
    limit: int = PREVIEW_ROWS,
    offset: int = 0,
) -> dict[str, Any]:
    """
    Validate and execute a SELECT query with safety guards.

    Returns a uniform response dict:
      Success: {
        "ok": True,
        "columns": [...],
        "rows": [...],
        "row_count": N,
        "truncated": bool,
        "has_more": bool,
        "total_rows": N | None,   # present when we ran a COUNT(*) subquery
        "limit_injected": bool,   # True when LIMIT was auto-added
        "fallback_mode": str | None,  # "timeout" | "broad_query" | None
      }
      Failure: {"ok": False, "error": "<code>", "message": "<detail>"}
    """
    # 1. Safety
    err = _validate_sql(sql)
    if err:
        return {"ok": False, "error": "unsafe_sql", "message": err}

    # 2. Auto-inject LIMIT for raw-row queries
    effective_limit = min(limit, MAX_ROWS)
    safe_sql, limit_injected = _inject_limit(sql, effective_limit)

    # 3. Apply offset for pagination
    if offset > 0:
        clean = safe_sql.rstrip().rstrip(";")
        safe_sql = f"{clean}\nOFFSET {offset}"

    # 4. Timeout via SQLite progress handler
    deadline = time.monotonic() + QUERY_TIMEOUT_S

    def _timeout_check():
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(_timeout_check, 500)

    try:
        cursor = conn.execute(safe_sql)
        columns = [d[0] for d in cursor.description] if cursor.description else []

        # Fetch up to MAX_ROWS + 1 so we can detect has_more
        raw_rows = cursor.fetchmany(MAX_ROWS + 1)
        has_more = len(raw_rows) > MAX_ROWS
        row_list = [list(r) for r in raw_rows[:MAX_ROWS]]

        # Try to get total_rows count for pagination metadata on raw-row queries
        total_rows: int | None = None
        if limit_injected and not has_more:
            try:
                count_sql = f"SELECT COUNT(*) FROM ({sql.rstrip().rstrip(';')})"
                cnt_cursor = conn.execute(count_sql)
                cnt = cnt_cursor.fetchone()
                if cnt:
                    total_rows = cnt[0]
                    has_more = total_rows > (offset + len(row_list))
            except Exception:
                pass  # count is best-effort

        # Evaluate truncated AFTER the count check so we know the true has_more
        truncated = has_more

        return {
            "ok": True,
            "columns": columns,
            "rows": row_list,
            "row_count": len(row_list),
            "truncated": truncated,
            "has_more": has_more,
            "total_rows": total_rows,
            "limit_injected": limit_injected,
            "fallback_mode": None,
        }

    except sqlite3.OperationalError as exc:
        msg = str(exc)
        if "interrupted" in msg.lower():
            return {
                "ok": False,
                "error": "timeout",
                "message": (
                    "Query timed out — it was scanning too many rows. "
                    "Try narrowing your question with a filter (e.g. a date range, "
                    "category, or state) or ask for an aggregate (SUM, COUNT, AVG) instead."
                ),
                "fallback_mode": "timeout",
            }
        return {"ok": False, "error": "sql_error", "message": msg}
    except Exception as exc:
        return {"ok": False, "error": "execution_error", "message": str(exc)}
    finally:
        conn.set_progress_handler(None, 0)


def broad_query_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """
    Return a quick summary + 5-row sample for broad 'show all data' requests.
    This avoids timing out or returning 100k rows.
    """
    try:
        # Summary stats
        summary_sql = """
            SELECT
                COUNT(*)                           AS total_transactions,
                ROUND(SUM(total_amount), 2)        AS total_revenue,
                COUNT(DISTINCT customer_id)        AS unique_customers,
                COUNT(DISTINCT product_id)         AS unique_products,
                COUNT(DISTINCT payment_method)     AS payment_methods,
                MIN(transaction_date)              AS earliest_date,
                MAX(transaction_date)              AS latest_date
            FROM transactions
        """
        cur = conn.execute(summary_sql)
        cols = [d[0] for d in cur.description]
        row = cur.fetchone()
        summary = dict(zip(cols, list(row))) if row else {}

        # 5-row sample
        sample_cur = conn.execute(
            "SELECT id, customer_id, product_id, product_category, "
            "payment_method, ROUND(total_amount,2) AS total_amount, "
            "transaction_date FROM transactions LIMIT 5"
        )
        sample_cols = [d[0] for d in sample_cur.description]
        sample_rows = [list(r) for r in sample_cur.fetchall()]

        return {
            "ok": True,
            "fallback_mode": "broad_query",
            "summary": summary,
            "columns": sample_cols,
            "rows": sample_rows,
            "row_count": len(sample_rows),
            "truncated": True,
            "has_more": True,
            "total_rows": summary.get("total_transactions"),
            "message": (
                f"This table has {summary.get('total_transactions', 'many'):,} rows — "
                "returning a summary and 5-row sample instead of the full dataset. "
                "Ask a specific question to get targeted results."
            ),
        }
    except Exception as exc:
        return {"ok": False, "error": "broad_query_error", "message": str(exc)}


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

                "⚠️ RESULTS ARE CAPPED — raw row queries are automatically limited to "
                f"{PREVIEW_ROWS} rows. Always prefer aggregations (COUNT, SUM, AVG, GROUP BY) "
                "over raw row fetches. Never attempt to return the full table.\n\n"

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

                "If result.truncated=true, note this in your answer and suggest narrowing the query."
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


def dispatch(
    tool_name: str,
    tool_args: dict[str, Any],
    conn: sqlite3.Connection,
    limit: int = PREVIEW_ROWS,
    offset: int = 0,
) -> dict[str, Any]:
    """Route tool call from the LLM to the correct handler."""
    if tool_name == "execute_sql":
        sql = tool_args.get("sql", "")
        if not sql:
            return {"ok": False, "error": "missing_argument", "message": "sql is required"}
        return run_sql(sql, conn, limit=limit, offset=offset)
    return {
        "ok": False,
        "error": "unknown_tool",
        "message": f"Tool '{tool_name}' is not registered.",
    }
