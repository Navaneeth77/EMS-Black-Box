"""Shared pytest fixtures.

``pyproject.toml`` puts ``backend``, ``simulation`` and ``analysis`` on
``pythonpath``, so imports work without an editable install — useful when running
the tests before ``scripts/bootstrap.sh`` has been run.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path to the repository root."""
    return REPO_ROOT


@pytest.fixture()
def client() -> TestClient:
    """FastAPI test client against the real application."""
    from app.main import app

    return TestClient(app)
