# Review Notes: Retail Data Analytics Chat System

> Prepared for the live review meeting.

---

## Architecture Summary

```
┌──────────────────────────────────────────────────────────────┐
│  React Frontend (Vite)  →  POST /api/chat  →  FastAPI       │
│                                                              │
│  FastAPI  ──►  chat_service.run_chat()                       │
│                    │                                         │
│                    ▼                                         │
│          OpenAI Chat API — model drafts SQLite SELECT        │
│                    │                                         │
│          ◄── execute_sql(query) ───────────────────┐         │
│                    │                              │         │
│                    ▼                              │         │
│          sql_tool.dispatch / run_sql()            │         │
│          (SELECT-only guard, LIMIT, timeout)      │         │
│                    │                              │         │
│                    ▼                              │         │
│          SQLite (`data/retail.db`)                │         │
│                    │                              │         │
│          rows + metadata ─────────────────────────┘         │
│                    │                                         │
│          final answer ◄── OpenAI (grounded in results)      │
└──────────────────────────────────────────────────────────────┘
```

### Key modules

| File | Responsibility |
|---|---|
| `scripts/ingest.py` | One-shot CSV → SQLite loader |
| `backend/app/config.py` | `pydantic-settings` config singleton |
| `backend/app/db.py` | SQLite connection factory & FastAPI dependency |
| `backend/app/sql_tool.py` | Schema text for the LLM; `execute_sql` tool; validation, limits, timeouts |
| `backend/app/chat_service.py` | NL → SQL orchestration loop; broad-query shortcut; final grounded reply |
| `backend/app/main.py` | FastAPI app (`/api/health`, `/api/chat`), CORS |
| `frontend/src/App.tsx` | React chat UI |

---

## How the LLM Is Integrated with the Data Layer

The system uses a **NL → SQL → Answer** pipeline:

1. `chat_service.run_chat()` assembles a message list: system prompt (with the
   full table schema) + conversation history.
2. The LLM translates the user's natural-language question into a SQLite `SELECT`
   statement and calls the single `execute_sql` tool.
3. `sql_tool.dispatch()` validates the SQL (SELECT-only guard, no multi-statement
   batches), injects a LIMIT for raw-row queries, and executes it against SQLite.
4. Query results (rows + truncation/timeout metadata) are sent back as a
   `role=tool` message.
5. The model produces a final structured JSON response (natural-language answer +
   visualisation hint) grounded only in the query results.

This loop repeats (up to `max_tool_rounds=6`) until `finish_reason == "stop"`.

---

## Why NL → SQL Is Acceptable Here

| Concern | How it is addressed |
|---|---|
| SQL injection | SELECT-only guard blocks writes/DDL; no multi-statement batches |
| Data sensitivity | Public Kaggle retail dataset, not private PII |
| Intended use | Internal / business analytics over owned data |
| Query flexibility | Ad-hoc questions, comparisons, and follow-ups without a new endpoint per intent |
| Row dumps | Auto-LIMIT injection, row cap, broad-query interception, query timeouts |

---

## How Intent Classification Works

Intent classification is **implicit and model-driven**. There is no hard-coded
classifier — the LLM reads the question and chooses *what SQL to write*, then
labels its response with an `intent` field from a fixed set:

`customer_query` · `product_query` · `trend_query` · `comparison_query` ·
`ranking_query` · `distribution_query` · `kpi_query` · `custom_query`

The model also resolves pronouns ("they", "it") using conversation history in the
message list, enabling multi-turn follow-ups without extra code.

---

## Safety Rails (defence in depth)

All enforced in `sql_tool.py`:

* **SELECT-only guard** — write/DDL keywords are rejected before execution.
* **Single-statement guard** — semicolons inside the query body are rejected.
* **Auto-LIMIT injection** — raw-row queries without a LIMIT get one appended.
* **Hard row cap** — `fetchmany(MAX_ROWS)` prevents full-table dumps.
* **Query timeout** — SQLite progress handler aborts queries > 3 seconds.
* **Broad-query interception** — "show all data" style requests are caught before
  the LLM is called and return a summary + sample instead.

---

## Technical Decisions

### SQL execution layer

`sql_tool.py` validates LLM-produced SQL, executes it, and returns a uniform
`{ "ok": bool, "data": ..., "error": ..., ... }` envelope (including truncation
and timeout metadata). The chat service never runs raw user text as SQL; only
strings that pass validation are executed.

### LLM integration approach

1. The system prompt embeds the database schema and strict SQL rules (e.g. date
   handling for this dataset).
2. A single OpenAI function tool, `execute_sql`, carries the generated `SELECT`.
3. `chat_service.run_chat()` loops: model proposes SQL → tool runs on SQLite →
   results return as `role=tool` messages → model emits the final grounded reply
   (and optional structured chart payload).
4. Broad "dump the whole table" style questions can be short-circuited before
   SQL generation when they match heuristics in `chat_service` / `sql_tool`.

### Intent and flexibility

There is no separate intent classifier. The model chooses *what* to query from
natural language, which supports paraphrases and follow-ups ("same chart but for
last quarter") without new backend routes for each phrasing.

### Edge-case handling

| Situation | Behaviour |
|---|---|
| Empty or invalid query result | LLM explains no matching data (or suggests narrowing) |
| SQL validation failure | `execute_sql` returns `ok=False`; model surfaces the error |
| Missing OPENAI_API_KEY | Graceful reply explaining the issue |
| Ambiguous question | LLM asks a clarifying question or proposes a reasonable default |
| Malformed tool JSON args | Parse errors caught; graceful error returned |
| Query timeout / too many rows | Executor aborts or truncates; metadata explains limits |
| Disallowed SQL (writes, multi-statement) | Blocked by `sql_tool` before execution |

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
| Disallowed SQL (writes, DDL) | `sql_tool._validate_sql` | Returns `ok=False, error="unsafe_sql"` |
| Query timeout / too many rows | `sql_tool.run_sql` | Aborts or truncates; metadata explains limits |
| Broad "show all" requests | `sql_tool.is_broad_query` + `chat_service` | Caught before LLM call; returns summary + sample |
| Empty / no-match result | LLM | Model explains no matching data or suggests narrowing |
| Missing OPENAI_API_KEY | `chat_service.run_chat` | Returns informative message immediately |
| Malformed tool JSON args | `chat_service.run_chat` | Parse errors caught; graceful error returned |
| Empty CSV rows | `scripts/ingest.py` | Skipped silently, counted as `skipped` |
| Non-numeric numeric fields | `scripts/ingest.py` | `_safe_int` / `_safe_float` return `None` |
| Duplicate ingestion | `scripts/ingest.py` | Table check before load; `--reset` flag |
| LLM produces no content | `chat_service.run_chat` | Returns `""` or last assistant content |
| Too many tool rounds | `chat_service.run_chat` | Capped at `max_tool_rounds`; returns partial answer |

---

## Tradeoffs and Next Steps

### Tradeoffs Made

| Decision | Tradeoff |
|---|---|
| NL → SQL (vs bounded tools) | Maximum flexibility, but requires safety rails to prevent writes |
| Implicit intent classification | Less control; model behaviour depends on prompt quality |
| Denormalised schema | Fast reads, but updates (if any) would require care |
| TEXT date storage | Simple ingestion, but date range queries need explicit conversion |
| No streaming | Simpler code, but UX feels slower for long responses |
| SQLite | No concurrent writes; not suitable for multi-user production |

### Recommended Next Steps

1. **Streaming + progressive UI** — use `stream=True` with SSE so tokens render
   as they arrive. For multi-round SQL queries, stream an intermediate
   "Querying…" status after each tool call so the UI never appears frozen.
2. **Caching** — add a simple TTL cache for expensive aggregate queries.
3. **Multi-LLM support with intent-based routing** — abstract the LLM client to
   support multiple providers (Anthropic, Ollama, etc.) and route by query
   complexity: simple lookups use a cheap, fast model (e.g. GPT-4o-mini, Haiku);
   complex multi-step analyses use a larger model (e.g. GPT-4o, Sonnet).

---

## Evaluation Framework

The evaluation suite lives in `backend/evals/` and tests the full NL → SQL →
Answer pipeline against a ground truth dataset with deterministic assertions and
an optional LLM-as-judge tier.

### Components

| File | Purpose |
|---|---|
| `backend/evals/seed.py` | Deterministic in-memory SQLite database (22 transactions, 5 customers, 4 products, 4 stores spanning 2023–2024) with pre-verified totals |
| `backend/evals/golden.json` | 37 evaluation cases across 11 categories with assertions and optional reference answers |
| `backend/evals/run_eval.py` | Standalone eval runner — deterministic checks + optional `--judge` flag |
| `backend/evals/judge.py` | LLM-as-judge: scores answers on correctness, completeness, clarity, and grounding (1–5 each) |
| `backend/evals/test_eval.py` | Pytest wrapper — each golden case is a parametrized test item under `@pytest.mark.eval` |

### Coverage (37 cases)

| Category | Count | Examples |
|---|---|---|
| KPI / aggregation | 4 | Total revenue, unique customers, avg discount |
| Customer | 7 | Spend, history, avg order value, top product |
| Product | 5 | Revenue, units sold, avg discount, stores |
| Trend / time-based | 4 | Monthly trends, Jan 2024, Q1 2024, year filter |
| Ranking | 5 | Top customers, stores, products |
| Comparison | 3 | C001 vs C002, Electronics vs Clothing |
| Follow-up (multi-turn) | 3 | Context resolution across turns |
| Edge cases | 4 | Unknown IDs, unsafe SQL (DELETE, DROP) |
| Location / distribution | 2 | Hawaii store revenue, payment breakdown |

### Assertion types (deterministic)

* `sql_executes` — at least one tool call returned `ok: true`
* `result_contains_value` — expected numeric value appears in SQL result rows
* `answer_contains` / `answer_excludes` — substring presence/absence (case-insensitive)
* `intent` / `viz_type` — structured response field matches expected value
* `min_result_rows` — SQL returned at least N rows
* `no_error` — metadata has no error key

### LLM-as-judge (optional, `--judge` flag)

A second LLM call (default: `gpt-4o-mini`) scores each response on four
dimensions (1–5 each):

| Dimension | What it measures |
|---|---|
| Correctness | Numbers and facts match the SQL results exactly |
| Completeness | All parts of the question are addressed |
| Clarity | Answer is well-structured and easy to understand |
| Grounding | Answer is grounded only in the returned data |

### Running

```bash
# Deterministic only (fast, no extra API cost):
python backend/evals/run_eval.py

# With LLM judge:
python backend/evals/run_eval.py --judge

# Single category:
python backend/evals/run_eval.py --category customer

# Via pytest:
pytest -m eval -v

# With judge via pytest:
EVAL_JUDGE=1 pytest -m eval -v
```

### Results from latest run

```
Eval Results: 37/37 passed (100%)
Judge overall: 4.68 / 5.0 (avg across 37 scored cases)
```

### Lessons from early eval runs

* **Intent labels are non-deterministic** — questions like "What is the total
  revenue for product A?" sit at the boundary of `product_query` and `kpi_query`.
  The model picks differently across runs. Intent assertions were removed from
  borderline cases; only unambiguous intents (trends, rankings, distributions)
  are asserted.
* **Broad-query regex had false positives** — "Show me all Electronics
  transactions" was incorrectly intercepted as a dump-the-whole-table request
  because the regex didn't account for a qualifier between "all" and
  "transactions". The regex was tightened so filtered requests pass through to
  the LLM.
