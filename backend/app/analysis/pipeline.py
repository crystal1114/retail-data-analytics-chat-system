"""
backend/app/analysis/pipeline.py

Async generator that orchestrates the full Thinking Mode pipeline:
  planner -> executor -> reporter

Yields SSE-formatted strings so FastAPI's StreamingResponse can stream
real-time progress to the frontend.

Architecture: the synchronous LLM/SQL work runs in a background thread and
pushes SSE events into a thread-safe queue.  The async generator pulls from
that queue and yields events to the HTTP response as they arrive — giving
the frontend true incremental streaming.
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import sqlite3
import threading
import traceback
from typing import Any, AsyncGenerator

from ..config import settings
from ..db import get_connection
from ..sql_tool import SCHEMA
from .executor import execute_step
from .planner import plan_steps
from .reporter import generate_report
from .schemas import AnalysisStep

logger = logging.getLogger(__name__)

PIPELINE_TIMEOUT_S = 180  # 3 minutes
_semaphore = threading.Semaphore(3)

_SENTINEL = None  # signals the producer thread is done


def _sse(event: str, data: dict[str, Any]) -> str:
    """Format a single SSE message."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _get_client() -> Any:
    """Build an OpenAI client (reuses chat_service pattern)."""
    from openai import OpenAI

    kwargs: dict[str, Any] = {"api_key": settings.openai_api_key}
    if settings.openai_base_url:
        kwargs["base_url"] = settings.openai_base_url
    return OpenAI(**kwargs)


def _run_pipeline_sync(
    prompt: str,
    q: queue.Queue[str | None],
) -> None:
    """
    Synchronous pipeline that pushes SSE events into *q* as they happen.
    Runs in a background thread; sends _SENTINEL when finished.
    Opens its own DB connection (the request-scoped one is closed by then).
    """
    model = settings.openai_model or "gpt-4o-mini"
    conn: sqlite3.Connection | None = None

    try:
        client = _get_client()
        conn = get_connection()

        # ── Phase 1: Planning ─────────────────────────────────────────
        q.put(_sse("status", {"phase": "planning"}))

        try:
            steps = plan_steps(prompt, SCHEMA, client, model)
        except Exception as exc:
            logger.error("Planner failed: %r\n%s", exc, traceback.format_exc())
            q.put(_sse("error", {"message": f"Planning failed: {exc!r}"}))
            return

        q.put(_sse("plan", {
            "steps": [
                {"step_id": s.step_id, "title": s.title, "type": s.type}
                for s in steps
            ],
        }))

        # ── Phase 2: Execution ────────────────────────────────────────
        q.put(_sse("status", {"phase": "executing"}))

        completed: dict[str, dict[str, Any]] = {}
        failed_ids: set[str] = set()

        for i, step in enumerate(steps):
            if any(dep in failed_ids for dep in step.depends_on):
                step.status = "skipped"
                q.put(_sse("step_done", {
                    "step_id": step.step_id,
                    "status": "failed",
                    "summary": "Skipped — dependency failed",
                }))
                failed_ids.add(step.step_id)
                continue

            q.put(_sse("step_start", {
                "step_id": step.step_id,
                "title": step.title,
                "current": i + 1,
                "total": len(steps),
            }))

            step.status = "running"
            try:
                result = execute_step(step, conn, client, model, completed)
            except Exception as exc:
                logger.error("Step %s failed: %s", step.step_id, exc)
                result = {"ok": False, "error": str(exc)}

            if result.get("ok", False):
                step.status = "done"
                step.result = result
                completed[step.step_id] = result
                summary = _result_summary(result)
                q.put(_sse("step_done", {
                    "step_id": step.step_id,
                    "status": "ok",
                    "summary": summary,
                }))
            else:
                step.status = "failed"
                step.error = result.get("error", "unknown error")
                failed_ids.add(step.step_id)
                q.put(_sse("step_done", {
                    "step_id": step.step_id,
                    "status": "failed",
                    "summary": step.error,
                }))

        # ── Phase 3: Reporter ─────────────────────────────────────────
        q.put(_sse("status", {"phase": "reporting"}))

        steps_info = [
            {
                "step_id": s.step_id,
                "title": s.title,
                "description": s.description,
                "result": completed.get(s.step_id, {"ok": False}),
            }
            for s in steps
            if s.step_id in completed
        ]

        try:
            report = generate_report(steps_info, client, model)
        except Exception as exc:
            logger.error("Reporter failed: %r\n%s", exc, traceback.format_exc())
            q.put(_sse("error", {
                "message": f"Report generation failed: {exc}",
                "partial_steps": [
                    {"step_id": s.step_id, "title": s.title, "status": s.status}
                    for s in steps
                ],
            }))
            return

        q.put(_sse("report", report.model_dump()))
        q.put(_sse("done", {}))

    except Exception as exc:
        logger.error("Pipeline error: %r\n%s", exc, traceback.format_exc())
        q.put(_sse("error", {"message": f"Unexpected error: {exc!r}"}))
    finally:
        if conn:
            conn.close()
        q.put(_SENTINEL)


def _result_summary(result: dict[str, Any]) -> str:
    """One-line summary of a step result for the progress stream."""
    if result.get("summary"):
        text = result["summary"]
        return text[:120] + "..." if len(text) > 120 else text

    rows = result.get("rows", [])
    cols = result.get("columns", [])
    if cols:
        return f"{len(rows)} row(s), {len(cols)} column(s)"

    return "completed"


async def run_analysis(
    prompt: str,
) -> AsyncGenerator[str, None]:
    """
    Async generator that streams SSE events for the full analysis pipeline.
    Used directly by FastAPI's StreamingResponse.
    """
    if not _semaphore.acquire(blocking=False):
        yield _sse("error", {"message": "Too many concurrent analyses. Please try again shortly."})
        return

    try:
        if not settings.openai_configured:
            yield _sse("error", {"message": "OpenAI API key is not configured."})
            return

        q: queue.Queue[str | None] = queue.Queue()

        thread = threading.Thread(
            target=_run_pipeline_sync,
            args=(prompt, q),
            daemon=True,
        )
        thread.start()

        deadline = asyncio.get_event_loop().time() + PIPELINE_TIMEOUT_S

        while True:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                yield _sse("error", {"message": "Analysis timed out after 3 minutes."})
                return

            # Poll the queue from the async context without blocking the event loop.
            # q.get(timeout=1.0) raises queue.Empty if no event is ready yet —
            # that just means the LLM / SQL work is still running; loop and retry.
            try:
                event = await asyncio.wait_for(
                    asyncio.to_thread(q.get, timeout=1.0),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                yield _sse("error", {"message": "Analysis timed out after 3 minutes."})
                return
            except queue.Empty:
                continue

            if event is _SENTINEL:
                break

            yield event

    except Exception as exc:
        logger.error("Pipeline error: %s", exc)
        yield _sse("error", {"message": f"Unexpected error: {exc}"})
    finally:
        _semaphore.release()
