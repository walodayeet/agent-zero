#!/usr/bin/env bash
# Agent Zero on bare metal: Python server + host terminal (no Docker, no RFC).
set -euo pipefail
cd "$(dirname "$0")"

# Bare metal is auto-detected (non-dockerized, no RFC_PASSWORD); keep this explicit for clarity.
export A0_LOCAL_RUNTIME=${A0_LOCAL_RUNTIME:-true}
export PATH="$PWD/.venv/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/tmp/playwright"
export A0_PORT="${A0_PORT:-5000}"
export A0_HOST="${A0_HOST:-127.0.0.1}"
# Login is disabled by default; allow the local origin so the UI can call the API.
export ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-http://${A0_HOST}:${A0_PORT},http://localhost:${A0_PORT},http://127.0.0.1:${A0_PORT}}"

exec .venv/bin/python run_ui.py --host="$A0_HOST" --port="$A0_PORT" "$@"
