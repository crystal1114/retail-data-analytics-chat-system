"""
backend/app/main.py

FastAPI application – route wiring and dependency setup.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from .chat_service import run_chat, stream_chat
from .config import settings
from .db import get_db
from .repository import METRIC_ALLOWLIST, get_business_metric, get_customer_purchases, get_customer_summary, get_product_stores, get_product_summary
from .schemas import ChatRequest, ChatResponse, DataEnvelope, HealthResponse

# ── App setup ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Retail Data Analytics Chat System",
    description=(
        "AI-powered retail analytics: ask questions in natural language "
        "about customers, products, and business metrics."
    ),
    version="1.0.0",
)

# Allow frontend (Vite dev server) to call the backend during development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Health ────────────────────────────────────────────────────────────────────────

@app.get("/api/health", response_model=HealthResponse, tags=["System"])
def health_check() -> HealthResponse:
    """Basic health / readiness check."""
    db_path = settings.resolved_db_path
    db_status = "ok" if db_path.exists() else "missing"
    return HealthResponse(
        status="ok",
        database=db_status,
        openai_configured=settings.openai_configured,
    )


# ── Customers ─────────────────────────────────────────────────────────────────────

@app.get("/api/customers/{customer_id}", response_model=DataEnvelope, tags=["Customers"])
def get_customer(
    customer_id: str = FPath(..., description="Numeric customer ID, e.g. 109318"),
    conn: sqlite3.Connection = Depends(get_db),
) -> DataEnvelope:
    """
    Returns a summary and recent purchases for the given customer.
    Combines get_customer_summary + get_customer_purchases(limit=10).
    """
    summary = get_customer_summary(conn, customer_id)
    if not summary["ok"]:
        raise HTTPException(status_code=404, detail=summary["message"])

    purchases = get_customer_purchases(conn, customer_id, limit=10)
    recent = purchases.get("data", []) if purchases["ok"] else []

    return DataEnvelope(
        ok=True,
        data={
            **summary["data"],
            "recent_purchases": recent,
        },
    )


# ── Products ──────────────────────────────────────────────────────────────────────

@app.get("/api/products/{product_id}", response_model=DataEnvelope, tags=["Products"])
def get_product(
    product_id: str = FPath(..., description="Product ID: A, B, C, or D"),
    conn: sqlite3.Connection = Depends(get_db),
) -> DataEnvelope:
    """
    Returns a summary and store list for the given product.
    Combines get_product_summary + get_product_stores.
    """
    summary = get_product_summary(conn, product_id.upper())
    if not summary["ok"]:
        raise HTTPException(status_code=404, detail=summary["message"])

    stores = get_product_stores(conn, product_id.upper())
    store_list = stores.get("data", []) if stores["ok"] else []

    return DataEnvelope(
        ok=True,
        data={
            **summary["data"],
            "stores": store_list,
        },
    )


# ── Metrics ───────────────────────────────────────────────────────────────────────

@app.get("/api/metrics/{metric_name}", response_model=DataEnvelope, tags=["Metrics"])
def get_metric(
    metric_name: str = FPath(
        ...,
        description=(
            "One of: "
            + ", ".join(sorted(METRIC_ALLOWLIST))
        ),
    ),
    limit: int = 10,
    conn: sqlite3.Connection = Depends(get_db),
) -> DataEnvelope:
    """
    Returns structured business metric data.
    metric_name must be in the fixed allowlist.
    """
    result = get_business_metric(conn, metric_name, limit=limit)
    if not result["ok"]:
        status = 400 if result.get("error") == "invalid_metric" else 404
        raise HTTPException(status_code=status, detail=result["message"])

    return DataEnvelope(ok=True, data=result["data"])


# ── Chat ──────────────────────────────────────────────────────────────────────────

@app.post("/api/chat", response_model=ChatResponse, tags=["Chat"])
async def chat(
    request: ChatRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> ChatResponse:
    """
    Conversational endpoint (non-streaming, kept for backward-compat / tests).
    """
    messages = [m.model_dump() for m in request.messages]
    result = await run_chat(messages=messages, conn=conn)
    return ChatResponse(
        reply=result["reply"],
        structured=result.get("structured"),
        tool_results=result.get("tool_results", []),
        metadata=result.get("metadata", {}),
    )


@app.post("/api/chat/stream", tags=["Chat"])
async def chat_stream(
    request: ChatRequest,
    conn: sqlite3.Connection = Depends(get_db),
) -> StreamingResponse:
    """
    Streaming conversational endpoint using Server-Sent Events (SSE).

    Emits JSON lines prefixed with 'data: ':
      data: {"type": "token",     "content": "..."}
      data: {"type": "tool_call", "tool": "...", "status": "running"}
      data: {"type": "tool_done", "tool": "...", "ok": true}
      data: {"type": "done",      "structured": {...}, "tool_results": [...], "metadata": {...}}
      data: {"type": "error",     "message": "..."}
    """
    messages = [m.model_dump() for m in request.messages]

    return StreamingResponse(
        stream_chat(messages=messages, conn=conn),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Root ──────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root() -> dict[str, str]:
    return {
        "message": "Retail Analytics API",
        "docs": "/docs",
        "health": "/api/health",
    }
