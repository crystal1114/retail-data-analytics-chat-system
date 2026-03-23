"""
backend/app/main.py

FastAPI application — route wiring and dependency setup.

Data layer: LLM-generated SQL (NL → SQL → Answer pipeline).
  - /api/chat     : conversational endpoint; LLM writes SELECT queries
  - /api/analysis : Thinking Mode — SSE-streamed multi-step analysis
  - /api/health   : readiness check
  - /docs         : auto-generated Swagger UI

The old pre-canned repository endpoints (/api/customers, /api/products,
/api/metrics) have been removed.  All analytics are now served through
the flexible /api/chat endpoint using LLM-generated SQL.
"""

from __future__ import annotations

import io
import logging
import sqlite3

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .analysis.pipeline import run_analysis
from .analysis.schemas import AnalysisRequest
from .chat_service import run_chat
from .config import settings
from .db import get_db
from .schemas import ChatRequest, ChatResponse, HealthResponse

logger = logging.getLogger(__name__)

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


# ── Analysis (Thinking Mode) ──────────────────────────────────────────────────

@app.post("/api/analysis", tags=["Analysis"])
async def analysis(
    request: AnalysisRequest,
) -> StreamingResponse:
    """
    Thinking Mode: stream a multi-step analysis via Server-Sent Events.

    The pipeline plans analysis steps, executes each (SQL + optional pandas),
    and assembles a structured report — all streamed as SSE events.
    The pipeline opens its own DB connection (separate from the request lifecycle).
    """
    return StreamingResponse(
        run_analysis(request.prompt),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Voice transcription (Whisper) ────────────────────────────────────────────

_MAX_AUDIO_BYTES = 25 * 1024 * 1024  # Whisper limit: 25 MB


@app.post("/api/transcribe", tags=["Voice"])
async def transcribe(file: UploadFile = File(...)) -> dict[str, str]:
    """
    Transcribe an audio file to text using OpenAI Whisper.

    Accepts any format Whisper supports (webm, mp3, mp4, wav, etc.).
    Returns ``{"text": "transcribed text"}``.
    """
    if not settings.openai_configured:
        raise HTTPException(status_code=503, detail="OpenAI API key not configured")

    try:
        from openai import OpenAI
    except ImportError:
        raise HTTPException(status_code=503, detail="openai package not installed")

    audio_bytes = await file.read()
    if len(audio_bytes) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 25 MB limit")
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    kwargs: dict = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    client = OpenAI(**kwargs)

    try:
        ext = (file.filename or "audio.webm").rsplit(".", 1)[-1] or "webm"
        transcript = client.audio.transcriptions.create(
            model="whisper-1",
            file=(f"audio.{ext}", io.BytesIO(audio_bytes)),
        )
    except Exception as exc:
        logger.error("Whisper transcription failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"Transcription failed: {exc}")

    return {"text": transcript.text}


# ── Root ──────────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root() -> dict[str, str]:
    return {
        "message": "Retail Analytics API v2 — NL→SQL pipeline",
        "docs": "/docs",
        "health": "/api/health",
    }
