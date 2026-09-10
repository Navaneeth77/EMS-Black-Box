#!/usr/bin/env bash
# Start the Vite dev server.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/frontend"

if [ ! -d node_modules ]; then
  echo "==> Installing frontend dependencies"
  npm install
fi

exec npm run dev
