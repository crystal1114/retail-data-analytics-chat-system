"""
backend/app/config.py

Application configuration loaded via pydantic-settings.

Precedence (highest → lowest):
  1. Shell environment variables
  2. backend/.env file
  3. repo-root .env file
  4. Built-in defaults

The DATABASE_PATH is resolved relative to the *repo root* when it is a
relative path, so callers never need to worry about the current working
directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


# Project root = two directories above this file (backend/app/config.py)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKEND_ENV = _REPO_ROOT / "backend" / ".env"
_ROOT_ENV = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Load from both possible .env locations; pydantic-settings merges them
        env_file=(str(_ROOT_ENV), str(_BACKEND_ENV)),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── OpenAI ──────────────────────────────────────────────────────────────
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"

    # ── Database ────────────────────────────────────────────────────────────
    database_path: str = "data/retail.db"

    # ── Frontend ────────────────────────────────────────────────────────────
    frontend_api_base_url: str = "http://localhost:8000"

    # ── Derived helpers ─────────────────────────────────────────────────────
    @property
    def resolved_db_path(self) -> Path:
        """Return an absolute Path to the SQLite database."""
        p = Path(self.database_path)
        if p.is_absolute():
            return p
        return (_REPO_ROOT / p).resolve()

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key != "sk-...")


# Singleton instance – import this everywhere
settings = Settings()
