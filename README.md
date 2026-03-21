# Retail Data Analytics Chat System

A production-quality, AI-powered retail analytics assistant that lets you ask
natural-language questions about a retail transaction dataset through a chat
interface. The LLM **translates questions into SQL**, the backend **executes
that SQL** against the SQLite retail dataset, and the model returns **grounded
natural-language answers** (and structured visualization hints) built only
from the query results.

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Architecture Summary](#architecture-summary)
3. [Why SQLite?](#why-sqlite)
4. [Natural language to SQL (data layer)](#natural-language-to-sql-data-layer)
5. [Technical Decisions](#technical-decisions)
6. [Dataset & Real ID Formats](#dataset--real-id-formats)
7. [Setup Instructions](#setup-instructions)
8. [Running the Backend](#running-the-backend)
9. [Running the Frontend](#running-the-frontend)
10. [Running Tests](#running-tests)
11. [API Reference](#api-reference)
12. [Example Chat Prompts](#example-chat-prompts)
13. [Known Limitations](#known-limitations)
14. [Future Improvements](#future-improvements)

---

## Project Overview

| Item | Detail |
|---|---|
| **Name** | Retail Data Analytics Chat System |
| **Dataset** | Kaggle Retail Transaction Dataset (~200 k rows) |
| **Backend** | FastAPI + Python, SQLite, OpenAI (NL → SQL via `execute_sql` tool) |
| **Frontend** | React + Vite + TypeScript |
| **LLM** | OpenAI GPT-4o-mini (configurable) |
| **Data Store** | SQLite (`data/retail.db`) |

The system answers three categories of questions:

* **Customer queries** – purchase history, total spend, favourite products
* **Product queries** – revenue, units sold, average discount, stores
* **Business metrics** – KPIs, monthly revenue trends, category breakdown, store rankings

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

**Key modules:**

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

## Why SQLite?

* **Zero infrastructure** – no separate database server to install or manage
* **Single file** – the entire dataset lives in `data/retail.db`, easy to copy/backup
* **Fast reads** – indexed queries on 200 k rows respond in milliseconds
* **Sufficient for analytics** – read-heavy workload with no concurrent writes
* **Local reproducibility** – reviewers can run the full system without any cloud services

---

## Natural language to SQL (data layer)

The data path is intentionally flexible:

1. **Natural language → SQL** – the LLM turns the user’s question into a SQLite
   `SELECT` using the real table schema.
2. **Execute against the dataset** – validated SQL runs on the retail
   transactions database.
3. **Grounded answers** – the model explains and summarizes **only** what the
   query returned (plus truncation/timeout metadata), not invented rows or metrics.

**Why this is an acceptable trade-off *here***

* **Public dataset** – the underlying data is a well-known Kaggle retail
  transaction set, not private customer PII in production.
* **Internal / business analytics over owned data** – the intended deployment is
  exploratory analytics on data the operator controls, not an open internet-facing
  SQL console over sensitive records.
* **Query flexibility and broader insights** – fixed canned APIs cannot cover every
  ad-hoc question; NL → SQL unlocks follow-ups, comparisons, and one-off analyses
  without shipping a new endpoint per intent.

**Safety rails (defence in depth)**

The executor in `sql_tool.py` still constrains what can run: **SELECT-only**
queries, no multi-statement batches, automatic `LIMIT` on raw row dumps, row
caps, query timeouts, and handling for overly broad “show me everything”
requests. That does not eliminate every text-to-SQL risk in the abstract, but it
aligns the architecture with the use case above.

---

## Technical Decisions

### Data Model

A single denormalised `transactions` table with 10 columns mirrors the CSV exactly.
Indexes on `customer_id`, `product_id`, and `transaction_date` cover all query patterns.
Normalisation into separate customer/product tables would add complexity with no
benefit for this read-only analytics use case.

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
4. Broad “dump the whole table” style questions can be short-circuited before
   SQL generation when they match heuristics in `chat_service` / `sql_tool`.

### Intent and flexibility

There is no separate intent classifier. The model chooses *what* to query from
natural language, which supports paraphrases and follow-ups (“same chart but for
last quarter”) without new backend routes for each phrasing.

### Edge-Case Handling

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

## Dataset & Real ID Formats

> **Important**: The dataset uses synthetic IDs that differ from examples in
> typical homework descriptions.

| Field | Real Format | Example |
|---|---|---|
| `CustomerID` | Numeric string | `109318`, `579675`, `993229` |
| `ProductID` | Single letter | `A`, `B`, `C`, `D` |
| `ProductCategory` | Text | `Books`, `Clothing`, `Electronics`, `Home Decor` |
| `PaymentMethod` | Text | `Cash`, `Credit Card`, `Debit Card`, `PayPal` |

All example prompts below use real ID formats from the dataset.

---

## Setup Instructions

### Prerequisites

* Python 3.11+
* Node.js 18+
* OpenAI API key

### 1. Clone / download the repository

```bash
git clone <repo-url>
cd retail-analytics
```

### 2. Install backend dependencies

```bash
pip install -r backend/requirements.txt
```

For tests, also install:
```bash
pip install -r backend/requirements-dev.txt
```

### 3. Install frontend dependencies

```bash
cd frontend
npm install
cd ..
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
OPENAI_API_KEY=sk-your-key-here
OPENAI_MODEL=gpt-4o-mini        # or gpt-4o, gpt-3.5-turbo
DATABASE_PATH=data/retail.db    # relative to repo root
```

**Env var precedence** (highest → lowest):
1. Shell environment (`export OPENAI_API_KEY=...`)
2. `backend/.env`
3. `.env` (repo root)
4. Built-in defaults

### 5. Download the dataset

Option A – from Kaggle:
```
https://www.kaggle.com/datasets/fahadrehman07/retail-transaction-dataset/data
```
Download `Retail_Transaction_Dataset.csv` and place it at:
```
data/Retail_Transaction_Dataset.csv
```

Option B – if you already have the file, copy it:
```bash
cp /path/to/Retail_Transaction_Dataset.csv data/
```

### 6. Ingest the dataset

```bash
python scripts/ingest.py
# Or with explicit paths:
python scripts/ingest.py --csv data/Retail_Transaction_Dataset.csv --db data/retail.db

# To force a full reload:
python scripts/ingest.py --reset
```

Expected output:
```
[INFO] Opening database: data/retail.db
[INFO] Loading CSV: data/Retail_Transaction_Dataset.csv
[INFO]   ... 200000 rows inserted
[INFO] Done. Inserted: 200,000  Skipped: 0
[INFO] Database written to: data/retail.db
```

---

## Running the Backend

```bash
# From repo root
uvicorn backend.app.main:app --reload --port 8000
```

The API will be available at `http://localhost:8000`.
Interactive docs: `http://localhost:8000/docs`

---

## Running the Frontend

```bash
cd frontend
npm run dev
```

The frontend will be available at `http://localhost:5173`.
API calls are proxied to `http://localhost:8000` automatically.

---

## Running Tests

```bash
# From repo root – run all unit/API tests (no API key required)
pytest

# Run with verbose output
pytest -v

# Run only repository tests
pytest backend/tests/test_repository.py -v

# Run only API tests
pytest backend/tests/test_api.py -v

# Run integration tests (requires OPENAI_API_KEY)
pytest -m integration -v
```

---

## API Reference

### `GET /api/health`
Returns backend status.

```json
{
  "status": "ok",
  "database": "ok",
  "openai_configured": true
}
```

### `POST /api/chat`

Primary analytics surface. The LLM generates SQL, the backend runs it on the
retail database, and the response is grounded in those results.

Request:
```json
{
  "messages": [
    { "role": "user", "content": "What has customer 109318 purchased?" }
  ]
}
```

Response (fields may vary; `structured` holds optional chart/KPI payloads for the UI):
```json
{
  "reply": "Customer 109318 has made ...",
  "structured": {
    "intent": "customer_history",
    "viz_type": "table",
    "insight": "...",
    "chart_data": {},
    "answer": "..."
  },
  "tool_results": [...],
  "metadata": { "model": "gpt-4o-mini", "tool_rounds": 2 }
}
```

---

## Example Chat Prompts

### Customer queries
```
What has customer 109318 purchased?
How much has customer 579675 spent in total?
Show the purchase history for customer 993229
What is the average order value for customer 463050?
Compare customer 109318 and customer 579675
```

### Product queries
```
Show me details for product A
What is the average discount for product C?
Which stores sell product B?
How many units of product D have been sold?
What is the total revenue for product A?
```

### Business analytics
```
What is the total revenue?
Which product categories generate the most revenue?
Show monthly revenue trends
Which stores generate the most sales?
What are the top products by revenue?
How many unique customers do we have?
What is the payment method breakdown?
Who are the top 5 customers by spend?
```

### Follow-up / context queries
```
User: Tell me about customer 109318
User: How much did they spend total?
User: What product did they buy most?

User: Show me details for product A
User: Which stores carry it?
```

---

## Known Limitations

1. **No persistent sessions** – conversation context lives only in the browser.
   Refreshing the page starts a fresh session.
2. **Store location is a full address** – the dataset uses full street addresses as
   the store identifier, not a store code. Queries like "revenue by store" return
   full address strings.
3. **ProductID is a single letter (A–D)** – this is how the dataset is structured.
   There are only four distinct products.
4. **No streaming** – the chat response waits for the full LLM completion before
   rendering. Multiple SQL round-trips may feel slow on complex questions.
5. **OpenAI-only** – the LLM layer is coupled to the OpenAI client. Swapping to
   Anthropic or a local model would require changes in `chat_service.py`.
6. **No authentication** – the API has no authentication layer; do not expose it
   publicly without adding one.

---

## Future Improvements

- [ ] **Streaming responses** – use `stream=True` and SSE to render tokens as they arrive
- [ ] **Charts** – render bar/line charts for metric results using Recharts or Chart.js
- [ ] **Pagination** – add cursor-based pagination for large purchase history lists
- [ ] **Session persistence** – store conversation history in localStorage or a backend session
- [ ] **Multi-LLM support** – abstract the LLM client to support Anthropic, Ollama, etc.
- [ ] **Caching** – add a simple TTL cache for expensive aggregate queries
- [ ] **Authentication** – add API key or OAuth2 middleware
- [ ] **Docker Compose** – single `docker-compose up` to start everything
- [ ] **Product name mapping** – enrich the single-letter product IDs with descriptive names
