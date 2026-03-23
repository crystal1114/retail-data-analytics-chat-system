# Review Notes: Retail Data Analytics Chat System

> Prepared for the live review meeting.

---

## Architecture Summary

```
┌─────────────────────────────────────────────────────────────────────┐
│  React Frontend (Vite)                                              │
│    ├── Chat Mode  → POST /api/chat      → FastAPI (sync JSON)      │
│    └── Thinking   → POST /api/analysis  → FastAPI (SSE stream)     │
│                                                                     │
│  ── Chat Mode (gpt-5-mini) ──────────────────────────────────────  │
│  chat_service.run_chat()                                            │
│      │  OpenAI Chat API — model drafts SQLite SELECT                │
│      │  ◄── execute_sql(query) ──┐                                  │
│      │  sql_tool.run_sql()       │                                  │
│      │  SQLite → rows + metadata ┘                                  │
│      └► final grounded answer                                       │
│                                                                     │
│  ── Thinking Mode (gpt-5.4, reasoning_effort=low) ───────────────  │
│  analysis/pipeline.run_analysis()  ── SSE stream ──►  frontend      │
│      ├─ planner.plan_steps()         → 3-8 analysis steps           │
│      ├─ executor.execute_step()  ×N  → SQL or Python (sandbox)      │
│      └─ reporter.generate_report()   → structured report            │
└─────────────────────────────────────────────────────────────────────┘
```

### Key modules

| File | Responsibility |
|---|---|
| `scripts/ingest.py` | One-shot CSV → SQLite loader |
| `backend/app/config.py` | `pydantic-settings` config singleton; separate model settings for Chat and Thinking Mode |
| `backend/app/db.py` | SQLite connection factory & FastAPI dependency |
| `backend/app/sql_tool.py` | Schema text for the LLM; `execute_sql` tool; validation, limits, timeouts |
| `backend/app/chat_service.py` | Chat Mode: NL → SQL orchestration loop; broad-query shortcut; final grounded reply |
| `backend/app/analysis/pipeline.py` | Thinking Mode: async SSE generator orchestrating planner → executor → reporter |
| `backend/app/analysis/planner.py` | Decomposes broad requests into 3–8 concrete SQL/Python steps via LLM |
| `backend/app/analysis/executor.py` | Generates and runs SQL or Python code for each step |
| `backend/app/analysis/sandbox.py` | Restricted Python sandbox for LLM-generated pandas code |
| `backend/app/analysis/reporter.py` | Assembles step results into a structured report via LLM |
| `backend/app/analysis/schemas.py` | Pydantic models for analysis pipeline data structures |
| `backend/app/main.py` | FastAPI app (`/api/health`, `/api/chat`, `/api/analysis`), CORS |
| `frontend/src/App.tsx` | React UI with Chat/Thinking Mode toggle |
| `frontend/src/components/AnalysisView.tsx` | Thinking Mode UI: input, real-time progress, report display |
| `frontend/src/components/AnalysisReport.tsx` | Renders structured analysis report with tables |

---

## How the LLM Is Integrated with the Data Layer

### Chat Mode (gpt-5-mini)

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

### Thinking Mode (gpt-5.4, reasoning_effort=low)

For broad analysis requests, a separate **planner → executor → reporter** pipeline
streams real-time progress via SSE:

1. **Planner** — the LLM decomposes the user's request into 3–8 concrete steps.
   The planner is tuned to **prefer SQL steps** — they are faster and more
   reliable. Python (pandas) steps are only created when the user's request
   requires genuine cross-step computation (e.g. correlation between result sets,
   percentile ranking, pivot tables merging multiple queries). The planner does
   **not** add a final "synthesis" or "summary" step because the reporter handles
   that automatically.
2. **Executor** — for each step, the LLM generates code (SQL or Python), which is
   executed against the database or in a restricted sandbox. Key resilience
   features:
   - **CTE support** — SQL validation accepts both `SELECT` and `WITH` (CTEs) as
     starting keywords, enabling complex analytical queries.
   - **Robust code extraction** — handles markdown fences, reasoning prose, and
     mixed content from the LLM. Finds the first `SELECT`/`WITH` keyword even
     when the model prepends explanatory text.
   - **Retry with error feedback** — if a SQL or Python step fails, the error is
     fed back to the LLM for up to 2 self-correction attempts. This is
     especially important for the tricky `M/D/YYYY H:MM` date expressions.
   - **Empty-code fallback** — if the LLM returns no usable code (e.g. for a
     vague synthesis step), the raw text response is treated as a summary
     rather than raising a hard failure.
3. **Reporter** — completed step results are passed to the LLM, which assembles a
   structured report with an executive summary, narrative sections, and tables.
   Failed steps are excluded; the report is generated from whatever succeeded.

The pipeline runs in a background thread, pushing SSE events into a thread-safe
queue. The async generator pulls from the queue and yields events to the frontend
as they arrive. Concurrency is limited to 3 simultaneous streams via a semaphore,
with a 3-minute overall pipeline timeout.

#### When the Python sandbox is invoked

The sandbox (`analysis/sandbox.py`) is only called when the planner creates a
`type: "python"` step. In practice this is rare — most analysis requests
(including "全面分析一下") produce **all SQL steps**. The sandbox is invoked
only when the user's request explicitly requires cross-DataFrame operations that
cannot be expressed in a single SQL query, such as:

- Correlation between metrics from separate queries
- Percentile ranking across different result sets
- Pivot tables that merge multiple SQL outputs

The sandbox restricts builtins to a safe set (no `os`, `subprocess`, `sys`,
`__import__`), caps input DataFrames at 10,000 rows, enforces a 30-second
timeout, and auto-aliases the last assigned variable to `result` if the LLM
forgets the mandatory assignment.

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

* **SELECT / CTE guard** — only queries starting with `SELECT` or `WITH` (CTEs)
  are allowed; all other statements are rejected.
* **Write-keyword detection** — `INSERT`, `UPDATE`, `DELETE`, `DROP`, `CREATE`,
  `ALTER`, `REPLACE INTO`, `TRUNCATE`, etc. are blocked. The check is specific
  enough to allow the SQLite `REPLACE()` string function (only `REPLACE INTO` is
  blocked).
* **Single-statement guard** — semicolons inside the query body are rejected.
* **Auto-LIMIT injection** — raw-row queries without a LIMIT get one appended.
* **Hard row cap** — `fetchmany(MAX_ROWS)` prevents full-table dumps.
* **Query timeout** — SQLite progress handler aborts queries > 3 seconds.
* **Broad-query interception** — "show all data" style requests are caught before
  the LLM is called and return a summary + sample instead.

Thinking Mode adds additional safeguards:

* **Python sandbox** — LLM-generated code runs with restricted builtins
  (`pandas`, `json`, `math` only), no `os`/`subprocess`/`sys`, 30-second timeout.
* **DataFrame row cap** — input DataFrames are capped at 5,000 rows in the sandbox.
* **Table row cap** — step results are capped at 200 rows before being stored.
* **Concurrency limit** — at most 3 concurrent analysis streams (semaphore).
* **Pipeline timeout** — entire pipeline aborts after 3 minutes.

---

## Technical Decisions

### SQL execution layer

`sql_tool.py` validates LLM-produced SQL, executes it, and returns a uniform
`{ "ok": bool, "data": ..., "error": ..., ... }` envelope (including truncation
and timeout metadata). The chat service never runs raw user text as SQL; only
strings that pass validation are executed.

### LLM integration approach

**Chat Mode** uses the OpenAI tool-calling loop:

1. The system prompt embeds the database schema and strict SQL rules (e.g. date
   handling for this dataset).
2. A single OpenAI function tool, `execute_sql`, carries the generated `SELECT`.
3. `chat_service.run_chat()` loops: model proposes SQL → tool runs on SQLite →
   results return as `role=tool` messages → model emits the final grounded reply
   (and optional structured chart payload).
4. Broad "dump the whole table" style questions can be short-circuited before
   SQL generation when they match heuristics in `chat_service` / `sql_tool`.

**Thinking Mode** uses a three-phase pipeline with dedicated LLM calls for each
phase (planning, code generation, report assembly). GPT-5.4 is used with
`reasoning_effort=low` — enough reasoning to improve reliability without excessive
latency. The pipeline adapts to GPT-5.4's API requirements
(`max_completion_tokens`, no custom `temperature`).

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
| Thinking Mode step failure | `analysis/pipeline.py` | Failed steps are marked; dependent steps skipped; report generated from successful steps |
| Thinking Mode pipeline timeout | `analysis/pipeline.py` | SSE error event sent after 3 minutes |
| Thinking Mode concurrent overload | `analysis/pipeline.py` | Semaphore rejects with "Too many concurrent analyses" |
| LLM generates unsafe Python | `analysis/sandbox.py` | Restricted builtins, blocked imports, 30-second thread timeout |
| LLM omits `result` variable | `analysis/sandbox.py` | Auto-aliases last assigned variable to `result` via static analysis |
| LLM returns empty code | `analysis/executor.py` | Raw LLM text is treated as summary; retried with feedback if blank |
| LLM wraps SQL in markdown/prose | `analysis/executor.py` | `_extract_sql()` strips fences/prose, finds first `SELECT`/`WITH` |
| SQL step fails (bad syntax, etc.) | `analysis/executor.py` | Error is fed back to LLM for up to 2 self-correction retries |
| CTE query rejected as unsafe | `sql_tool.py` | `WITH` is now accepted alongside `SELECT` as a valid starting keyword |
| `REPLACE()` function blocked | `sql_tool.py` | Write-keyword regex targets `REPLACE INTO` specifically, not `REPLACE()` |
| Nested objects in report payload | `AnalysisReport.tsx` | `formatValue()` safely stringifies any non-primitive before rendering |

---

## Tradeoffs and Next Steps

### Tradeoffs Made

| Decision | Tradeoff |
|---|---|
| NL → SQL (vs bounded tools) | Maximum flexibility, but requires safety rails to prevent writes |
| Implicit intent classification | Less control; model behaviour depends on prompt quality |
| Denormalised schema | Fast reads, but updates (if any) would require care |
| TEXT date storage | Simple ingestion, but date range queries need explicit conversion |
| Chat Mode not streamed | Simpler code, but UX feels slower for long responses |
| Thinking Mode uses `exec()` sandbox | Flexible Python analysis, but sandbox is not a full security boundary; planner is tuned to prefer SQL steps to minimise sandbox use |
| Two separate models | gpt-5-mini for fast chat, gpt-5.4 for deeper analysis — good cost/quality split, but Thinking Mode is slower |
| SQLite | No concurrent writes; not suitable for multi-user production |

### Recommended Next Steps

1. **Chat Mode streaming** — use `stream=True` with SSE so tokens render as they
   arrive. Thinking Mode already streams via SSE.
2. **Caching** — add a simple TTL cache for expensive aggregate queries.
3. **Chart rendering in Thinking Mode** — the report schema supports `chart_data`
   per section; wire it to the frontend chart components.
4. **Multi-LLM support** — abstract the LLM client to support multiple providers
   (Anthropic, Ollama, etc.).

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
Judge overall: 4.94 / 5.0 (avg across 37 scored cases)
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

### Lessons from Thinking Mode debugging

* **CTEs were rejected as unsafe** — the original SQL validator only accepted
  queries beginning with `SELECT`. CTEs (`WITH ... AS (...)`) were blocked as
  non-SELECT statements. Fix: accept `WITH` as a valid first keyword.
* **`REPLACE()` function triggered the write guard** — the write-keyword regex
  blocked `REPLACE` globally, which caught the SQLite `REPLACE(str, from, to)`
  string function used in date handling. Fix: change the pattern to
  `REPLACE\s+INTO` so only the DML form is blocked.
* **LLM wraps SQL in markdown fences or prose** — especially with reasoning
  models, the LLM often returned SQL wrapped in ` ```sql ... ``` ` fences or
  preceded by explanatory text. The original code expected a bare SQL string.
  Fix: `_extract_sql()` strips fences and scans for the first `SELECT`/`WITH`.
* **Python synthesis steps are fragile and often unnecessary** — the planner
  sometimes appended a final "综合诊断" (synthesis) step as `type: "python"`.
  The LLM then generated vague pseudo-code or empty responses because the task
  was too abstract, causing "Code did not assign a `result` variable" errors.
  Fix: (a) the planner prompt now explicitly avoids final synthesis steps
  (reporter does that), (b) the sandbox auto-aliases the last assigned variable
  to `result`, and (c) the executor returns the raw LLM text as a summary when
  no usable code is produced.
* **Reports still generated despite failed steps** — a step failure (e.g. the
  final Python synthesis step) does not block the reporter. The pipeline feeds
  all *successful* step results to the reporter, which assembles the report from
  whatever is available. This is by design — partial results are better than no
  report at all.
