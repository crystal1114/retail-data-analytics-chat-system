"""
backend/app/schemas.py

Pydantic request / response models for the API.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Chat ────────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str = Field(..., description="'user' or 'assistant'")
    content: str = Field(..., description="Message text")


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(
        ...,
        min_length=1,
        description="Conversation history ending with the latest user message",
    )


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Natural-language answer from the assistant")
    tool_results: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Raw tool outputs for debugging / transparency",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional metadata (intent, entities, model used, etc.)",
    )


# ── Health ──────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    database: str
    openai_configured: bool


# ── Generic data envelope ───────────────────────────────────────────────────────

class DataEnvelope(BaseModel):
    ok: bool
    data: Any = None
    error: str | None = None
    message: str | None = None
