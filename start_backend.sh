#!/bin/bash
# start_backend.sh — Start the Retail Analytics FastAPI backend

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env if it exists
if [ -f "$SCRIPT_DIR/.env" ]; then
  set -a
  source "$SCRIPT_DIR/.env"
  set +a
fi

export PYTHONPATH="$SCRIPT_DIR"

echo "[start_backend.sh] Starting on port 8000..."
echo "[start_backend.sh] OPENAI_BASE_URL=${OPENAI_BASE_URL}"
echo "[start_backend.sh] MODEL=${OPENAI_MODEL}"

exec uvicorn backend.app.main:app --host 0.0.0.0 --port 8000
