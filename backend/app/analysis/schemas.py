"""
backend/app/analysis/schemas.py

Pydantic models and type definitions for the Thinking Mode analysis pipeline.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# ── Request ──────────────────────────────────────────────────────────────────

class AnalysisRequest(BaseModel):
    prompt: str = Field(
        ...,
        min_length=1,
        description="The user's broad analysis request (e.g. '全面分析数据')",
    )


# ── Pipeline data structures ─────────────────────────────────────────────────

StepType = Literal["sql", "python"]
StepStatus = Literal["pending", "running", "done", "failed", "skipped"]


class AnalysisStep(BaseModel):
    step_id: str
    title: str
    type: StepType
    description: str
    depends_on: list[str] = Field(default_factory=list)
    status: StepStatus = "pending"
    sql: str | None = None
    code: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None


class AnalysisSection(BaseModel):
    title: str
    content: str
    table: dict[str, Any] | None = None
    chart_data: dict[str, Any] | None = None


class AnalysisReport(BaseModel):
    executive_summary: str
    sections: list[AnalysisSection] = Field(default_factory=list)


# ── SSE event payloads ───────────────────────────────────────────────────────

class StatusEvent(BaseModel):
    phase: Literal["planning", "executing", "reporting"]


class PlanEvent(BaseModel):
    steps: list[dict[str, str]]


class StepStartEvent(BaseModel):
    step_id: str
    title: str
    current: int
    total: int


class StepDoneEvent(BaseModel):
    step_id: str
    status: Literal["ok", "failed"]
    summary: str | None = None


class ErrorEvent(BaseModel):
    message: str
    partial_steps: list[dict[str, Any]] | None = None
