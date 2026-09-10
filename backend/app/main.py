"""FastAPI application entry point.

Run from the repository root:

    uvicorn app.main:app --reload --app-dir backend --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routes import health, simulation
from app.config import get_settings


def create_app() -> FastAPI:
    """Build the application.

    A factory rather than a module-level singleton so tests can construct an app
    with overridden settings without reaching into import side effects.
    """
    settings = get_settings()

    application = FastAPI(
        title=settings.app_name,
        version=__version__,
        summary="Counterfactual replay of ambulance trips through simulated Bengaluru traffic.",
        description=(
            "Serves simulation state produced by SUMO. This API computes no vehicle "
            "motion of its own: SUMO is the source of truth, and every travel time or "
            "delay it reports is derived from simulation output."
        ),
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    application.include_router(health.router, prefix="/api")
    application.include_router(simulation.router, prefix="/api")

    return application


app = create_app()


@app.get("/", include_in_schema=False)
def root() -> dict[str, str]:
    """Point callers at the useful endpoints."""
    return {
        "service": "EMS Black Box API",
        "version": __version__,
        "health": "/api/health",
        "docs": "/docs",
    }
