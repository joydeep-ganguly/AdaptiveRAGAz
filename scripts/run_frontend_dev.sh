#!/usr/bin/env bash
# Starts the Streamlit frontend in dev mode. Run from anywhere:
#   ./scripts/run_frontend_dev.sh
# Assumes the frontend deps are installed and frontend/.env is set up.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT/frontend"
streamlit run streamlit_app.py