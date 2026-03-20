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


class ChartDataset(BaseModel):
    label: str
    data: list[Any]


class ChartData(BaseModel):
    """Flexible chart data structure supporting all viz types."""
    # For line/bar/horizontal_bar charts
    labels: list[str] | None = None
    datasets: list[ChartDataset] | None = None
    # For kpi_card
    kpis: list[dict[str, Any]] | None = None
    # For table
    columns: list[str] | None = None
    rows: list[list[Any]] | None = None


class StructuredResponse(BaseModel):
    """Structured response from LLM with visualization metadata."""
    intent: str = Field(default="unknown")
    viz_type: str = Field(default="none")
    insight: str = Field(default="")
    chart_data: dict[str, Any] | None = None
    answer: str = Field(default="")


class ChatResponse(BaseModel):
    reply: str = Field(..., description="Natural-language answer from the assistant")
    structured: dict[str, Any] | None = Field(
        default=None,
        description="Structured response with viz_type, chart_data, insight",
    )
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
