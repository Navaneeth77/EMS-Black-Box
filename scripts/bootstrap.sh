#!/usr/bin/env bash
# Create the Python virtualenv and install what the current scaffold needs.
#
# Installs backend + dev tooling only. The geospatial stack in
# simulation/requirements.txt is a large install that nothing uses until map
# ingestion begins, so it is left for that phase - see docs/ROADMAP.md.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "error: $PYTHON_BIN not found." >&2
  echo "This project targets Python 3.11 - the geospatial stack has patchy wheel" >&2
  echo "coverage on newer interpreters. Install it (brew install python@3.11), or" >&2
  echo "override with PYTHON_BIN=python3 if you accept the risk." >&2
  exit 1
fi

if [ ! -d .venv ]; then
  echo "==> Creating .venv with $PYTHON_BIN"
  "$PYTHON_BIN" -m venv .venv
else
  echo "==> Reusing existing .venv"
fi

# shellcheck disable=SC1091
source .venv/bin/activate

echo "==> Upgrading pip"
python -m pip install --quiet --upgrade pip

echo "==> Installing backend requirements"
pip install --quiet -r backend/requirements.txt

echo "==> Installing dev requirements"
pip install --quiet -r requirements-dev.txt

echo "==> Installing ems_sim + ems_analysis (editable)"
pip install --quiet -e .

echo
echo "Done. Activate with:  source .venv/bin/activate"
echo "Run the backend:      uvicorn app.main:app --reload --app-dir backend --port 8000"
echo "Run the tests:        pytest"
echo
echo "Not installed (not needed until map ingestion - see docs/ROADMAP.md):"
echo "  simulation/requirements.txt  osmnx, geopandas, shapely, pyproj"
echo "  analysis/requirements.txt    pandas, pyarrow"
