# Review Notes: Retail Data Analytics Chat System

> Prepared for the live review meeting.

---

## How the LLM Is Integrated with the Data Layer

The LLM (OpenAI GPT-4o-mini) is connected to the data layer exclusively through
**OpenAI function calling**, not through natural-language-to-SQL:

1. `chat_service.run_chat()` assembles a message list: system prompt + conversation history.
2. It calls `client.chat.completions.create(tools=TOOL_DEFINITIONS, tool_choice="auto")`.
3. If the model requests a tool call (`finish_reason == "tool_calls"`), the tool name
   and JSON arguments are extracted from the response.
4. `tools.dispatch_tool(name, args, conn)` routes the call to the correct
   function in `repository.py`.
5. The repository executes a **pre-written, parameterised** SQL query and returns
   a structured dict.
6. The dict is serialised to JSON and sent back to the model as a `role=tool` message.
7. The model produces a final natural-language answer using only the tool output.

This loop repeats (up to `max_tool_rounds=5`) until `finish_reason == "stop"`.

---

## Why Tool-Calling Was Used

| Concern | Text-to-SQL approach | Tool-calling approach |
|---|---|---|
| SQL injection | High risk — user input reaches SQL | Impossible — user text never touches SQL |
| Hallucinated schema | Model invents columns | Model only picks from a fixed list |
| Auditability | Every query is different | Every query is unit-tested |
| Flexibility | High (any query) | Bounded (approved queries only) |
| Safety for production | Low without heavy sandboxing | High by design |

The bounded approach was chosen deliberately for a production analytics assistant
where correctness and safety outweigh query flexibility.

---

## How Intent Classification Works

Intent classification is **implicit and model-driven**:

* The system prompt describes the assistant's role and available tools.
* Each tool definition includes a clear description of when to use it.
* The model reads the conversation and selects the best-matching tool.

Supported intent classes (handled by tool selection):

| Intent | Tool used |
|---|---|
| `customer_query` | `get_customer_summary`, `get_customer_purchases` |
| `product_query` | `get_product_summary`, `get_product_stores` |
| `business_metric_query` | `get_business_metric` |
| `ambiguous_query` | Model asks clarifying question (no tool called) |
| `unsupported_query` | Model explains it cannot help |
| `compare_customers` | `compare_customers` |

The model also resolves pronouns ("they", "it") using conversation history in the
message list, enabling basic follow-up support without extra code.

---

## How IDs / Parameters Are Extracted

The model extracts IDs and parameters as part of tool argument generation:

* **Customer IDs** – the model recognises numeric strings like `109318` and
  passes them verbatim as `customer_id`.
* **Product IDs** – the model recognises `A`, `B`, `C`, `D` and passes them as
  `product_id`.
* **Metric names** – the tool schema uses an `enum` field listing all allowed
  metric names, constraining the model to valid choices.
* **Limit/top-N** – the model extracts numeric limits from phrases like "top 5".

If a required ID is absent, the tool schema's `required` field signals the model
to ask a clarifying question rather than guess.

---

## How Business Metrics Are Mapped

`METRIC_ALLOWLIST` (a Python `frozenset`) is the single source of truth:

```python
METRIC_ALLOWLIST = {
    "overall_kpis",
    "revenue_by_store",
    "top_products_by_revenue",
    "monthly_revenue",
    "revenue_by_category",
    "top_customers_by_spend",
    "payment_method_breakdown",
}
```

* The tool schema `enum` field is generated from this set, so the model can only
  request metrics that exist.
* `repository.get_business_metric()` validates against the same allowlist and
  returns `error="invalid_metric"` if a bad name somehow arrives.
* Each metric maps to a dedicated private function in `repository.py`.

---

## Data Modelling / Storage Decisions

### Schema

```sql
CREATE TABLE transactions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id      TEXT,    -- e.g. "109318"
    product_id       TEXT,    -- e.g. "A"
    quantity         INTEGER,
    price            REAL,
    transaction_date TEXT,    -- "12/26/2023 12:32"
    payment_method   TEXT,
    store_location   TEXT,    -- full address (multi-line)
    product_category TEXT,
    discount_pct     REAL,
    total_amount     REAL
);
```

**Why denormalised?** This is a read-only analytics system. A single flat table
avoids JOINs, simplifies queries, and matches the source CSV exactly.

**Why store `transaction_date` as TEXT?** The CSV uses `M/D/YYYY H:MM` format.
Converting to ISO8601 would work but adds complexity. Month extraction is done
in Python (in `_metric_monthly_revenue`) for simplicity.

**Indexes:**
```sql
CREATE INDEX idx_customer_id      ON transactions (customer_id);
CREATE INDEX idx_product_id       ON transactions (product_id);
CREATE INDEX idx_transaction_date ON transactions (transaction_date);
CREATE INDEX idx_product_category ON transactions (product_category);
```

These cover the four most common filter patterns.

---

## Edge Cases Handled

| Edge case | Where handled | Behaviour |
|---|---|---|
| Customer not in DB | `repository.get_customer_summary` | Returns `ok=False, error="not_found"` |
| Product not in DB | `repository.get_product_summary` | Returns `ok=False, error="not_found"` |
| Invalid metric name | `repository.get_business_metric` | Returns `ok=False, error="invalid_metric"` |
| Missing OPENAI_API_KEY | `chat_service.run_chat` | Returns informative message immediately |
| Malformed tool JSON args | `chat_service.run_chat` | `json.loads` exception caught; `{}` used |
| Unknown tool name | `tools.dispatch_tool` | Returns `ok=False, error="unknown_tool"` |
| Empty CSV rows | `scripts/ingest.py` | Skipped silently, counted as `skipped` |
| Non-numeric numeric fields | `scripts/ingest.py` | `_safe_int` / `_safe_float` return `None` |
| SQL injection in user text | Architecture | Impossible — text never reaches SQL |
| Duplicate ingestion | `scripts/ingest.py` | Table check before load; `--reset` flag |
| LLM produces no content | `chat_service.run_chat` | Returns `""` or last assistant content |
| Too many tool rounds | `chat_service.run_chat` | Capped at `max_tool_rounds`; returns partial answer |

---

## Tradeoffs and Next Steps

### Tradeoffs Made

| Decision | Tradeoff |
|---|---|
| Implicit intent classification | Less control; model behaviour depends on prompt quality |
| Denormalised schema | Fast reads, but updates (if any) would require care |
| TEXT date storage | Simple ingestion, but date range queries are slower |
| No streaming | Simpler code, but UX feels slower for long responses |
| SQLite | No concurrent writes; not suitable for multi-user production |

### Recommended Next Steps

1. **Add response streaming** for better perceived performance.
2. **Add charts** — render the `monthly_revenue` and `revenue_by_category` data
   as line/bar charts in the frontend.
3. **Docker Compose** — package backend + SQLite into a container for one-command startup.
4. **Richer product catalog** — map product IDs A/B/C/D to descriptive names.
5. **Rate limiting + auth** — add API key middleware before exposing publicly.
6. **Async DB access** — use `aiosqlite` to avoid blocking the event loop on large queries.
