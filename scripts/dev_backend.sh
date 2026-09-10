#!/usr/bin/env bash
# Start the FastAPI backend with reload.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [ ! -d .venv ]; then
  echo "error: no .venv. Run scripts/bootstrap.sh first." >&2
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

exec uvicorn app.main:app \
  --reload \
  --app-dir backend \
  --host "${EMS_API_HOST:-127.0.0.1}" \
  --port "${EMS_API_PORT:-8000}"
