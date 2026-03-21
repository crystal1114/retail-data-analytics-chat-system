"""
backend/app/main.py

FastAPI application — route wiring and dependency setup.

Data layer: LLM-generated SQL (NL → SQL → Answer pipeline).
  - /api/chat   : conversational endpoint; LLM writes SELECT queries
  - /api/health : readiness check
  - /docs       : auto-generated Swagger UI

The old pre-canned repository endpoints (/api/customers, /api/products,
/api/metrics) have been removed.  All analytics are now served through
the flexible /api/chat endpoint using LLM-generated SQL.
"""

from __future__ import annotations

import sqlite3

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .chat_service import run_chat
from .config import settings
from .db import get_db
from .schemas import ChatRequest, ChatResponse, HealthResponse

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Retail Data Analytics Chat System",
    description=(
        "AI-powered retail analytics using LLM-generated SQL. "
        "Ask any question in natural language — the assistant translates it "
        "to SQL, queries the retail dataset, and returns a grounded answer "
        "with charts and visualizations."
    ),
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Readiness check — verifies DB file exists and OpenAI key is set."""
    db_path = settings.resolved_db_path
    db_status = "ok" if db_path.exists() else "missing"
    return HealthResponse(
        status="ok",
        database=db_status,
        openai_configured=settings.openai_configured,
    )


# ── Chat ──────────────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse, tags=["Chat"])
def chat(
    request: ChatRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> ChatResponse:
    """
    Conversational analytics endpoint.

    The LLM translates the user's natural-language question into a SQLite
    SELECT statement, executes it against the retail transactions table,
    then returns a grounded natural-language answer with a visualization spec.
    """
    messages = [m.model_dump() for m in request.messages]
    result = run_chat(messages=messages, conn=conn)
    return ChatResponse(
        reply=result["reply"],
        structured=result.get("structured"),
        tool_results=result.get("tool_results", []),
        metadata=result.get("metadata", {}),
    )


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root() -> dict[str, str]:
    return {
        "message": "Retail Analytics API v2 — NL→SQL pipeline",
        "docs": "/docs",
        "health": "/api/health",
    }
