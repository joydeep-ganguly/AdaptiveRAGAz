#!/usr/bin/env bash
# Starts the FastAPI backend in dev mode. Run from anywhere:
#   ./scripts/run_backend_dev.sh
# Assumes the backend venv is already activated and backend/.env is set up.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT/backend"
uvicorn app.main:app --reload --port 8000